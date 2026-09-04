"""API-level tests.

Hits the running stack over HTTP rather than mounting the app in-process,
because what matters here is the wiring: routes, serialisation, the database,
and the solver all agreeing. A TestClient would exercise the handlers while
skipping exactly the seams that break.

Skipped automatically when the stack is not up, so `pytest` stays useful
without Docker.
"""

from __future__ import annotations

import os
import time
from datetime import datetime
from zoneinfo import ZoneInfo

import httpx
import pytest

BASE = os.environ.get("WAYPOINT_API", "http://localhost:8000")

# The dispatch day these tests work on: TODAY, in the timezone the dispatch
# day is defined in -- not a fixed date.
#
# This is load-bearing, not tidiness. Several tests here report status events
# timestamped on this day, and api/field_status.py clamps any client timestamp
# more than MAX_BACKDATE (24 hours) before receipt, flagging the report
# untrusted. That is correct behaviour -- a phone claiming to have finished a
# job two days ago has a broken clock -- but it means a hardcoded date works
# for one day and then quietly stops.
#
# The symptom when it does is nowhere near the cause: `drift_by_technician`
# skips untrusted reports, so the assertion that fails is an empty drift map
# in a re-optimisation test, which reads as a solver bug. It cost an afternoon
# once; hence the length of this comment.
#
# Asia/Kuala_Lumpur specifically, and not the machine's zone, because that is
# what the API means by a day -- a test running in UTC after 16:00 local would
# otherwise ask for yesterday.
DAY = (
    datetime.now(ZoneInfo(os.environ.get("WAYPOINT_TZ", "Asia/Kuala_Lumpur")))
    .date()
    .isoformat()
)

# The dispatcher-side shared secret, when the stack under test has one.
#
# Empty is the development default and every dispatcher route is then open, so
# this suite works either way. Set WAYPOINT_DISPATCH_TOKEN to whatever
# DISPATCH_TOKEN is when running against a stack exposed beyond localhost --
# which is exactly when it becomes mandatory.
DISPATCH_TOKEN = os.environ.get("WAYPOINT_DISPATCH_TOKEN", "")
DISPATCH_AUTH = (
    {"Authorization": f"Bearer {DISPATCH_TOKEN}"} if DISPATCH_TOKEN else {}
)


def _up() -> bool:
    try:
        return httpx.get(f"{BASE}/health", timeout=2).status_code == 200
    except Exception:  # noqa: BLE001
        return False


def _seeded() -> bool:
    """Does the database hold jobs for today?

    Checked once, up front, because the alternative is thirty tests failing on
    assertions about empty lists. The seeded window is finite -- it is whatever
    days somebody last generated -- so "today" eventually walks off the end of
    it, and that should read as "seed a day" rather than as a broken API.
    """
    try:
        r = httpx.get(
            f"{BASE}/jobs", params={"day": DAY}, headers=DISPATCH_AUTH, timeout=10
        )
        return r.status_code == 200 and len(r.json()) > 0
    except Exception:  # noqa: BLE001
        return False


if not _up():
    pytestmark = pytest.mark.skip(reason=f"API not reachable at {BASE}")
elif not _seeded():
    pytestmark = pytest.mark.skip(
        reason=(
            f"no jobs on {DAY}. These tests run against today; seed it with\n"
            f"    docker compose exec api python -m data.seed "
            f"--jobs 40 --technicians 8 --day {DAY} --seed 42 --jobs-only\n"
            "(drop --jobs-only for a first seed, and see DEMO-DEPLOY.md if the "
            "frozen matrix is in use -- new coordinates need a re-freeze)"
        )
    )
else:
    pytestmark = []


@pytest.fixture(scope="module")
def client():
    """A client that carries the dispatcher token, when there is one.

    A DEFAULT header rather than passed per call, so a test hitting a
    dispatcher route does not have to remember. Technician routes override it
    with their own Authorization header, which is the correct precedence:
    `/field/*` never wants the dispatch secret.
    """
    with httpx.Client(base_url=BASE, timeout=180, headers=DISPATCH_AUTH) as c:
        yield c


@pytest.fixture(scope="module")
def run_id(client) -> int:
    """The most recent successful run, or a fresh one."""
    runs = client.get("/solve/runs", params={"day": DAY, "limit": 20}).json()
    ok = [r for r in runs if r["status"] == "succeeded"]
    if ok:
        return ok[0]["id"]

    started = client.post(
        "/solve", json={"day": DAY, "time_limit_s": 15, "workers": 8}
    ).json()
    for _ in range(90):
        time.sleep(2)
        row = client.get(f"/solve/runs/{started['id']}").json()
        if row["status"] == "succeeded":
            return started["id"]
        if row["status"] == "failed":
            pytest.fail(f"solve failed: {row['error']}")
    pytest.fail("solve did not finish in time")


# --- Health -----------------------------------------------------------------


def test_health(client):
    assert client.get("/health").json()["status"] == "ok"


def test_health_db_reports_schema_and_migration(client):
    body = client.get("/health/db").json()
    # "N/N", not a hardcoded 5: this asserts that every table the API expects
    # is present, which is the actual claim. Pinning the number meant every
    # migration that added a table broke a health test for no reason -- as
    # migration 0003 did, taking it to 7.
    found, expected = body["domain_tables"].split("/")
    assert found == expected
    assert body["migration"]


def test_health_routing_reports_provenance(client):
    body = client.get("/health/routing").json()
    assert "source" in body
    assert "reportable" in body


# --- CRUD -------------------------------------------------------------------


def test_lists_technicians(client):
    techs = client.get("/technicians").json()
    assert techs
    assert {"id", "name", "skills", "shift_start", "lat", "lon"} <= set(techs[0])


def test_lists_jobs_for_a_day(client):
    jobs = client.get("/jobs", params={"day": DAY}).json()
    assert jobs
    assert all("customer" in j for j in jobs)


def test_job_round_trips_through_create_and_delete(client):
    payload = {
        "customer": "Test Tower",
        "lat": 3.15,
        "lon": 101.71,
        "duration_minutes": 60,
        "required_skills": ["split_unit"],
        "required_parts": [],
        "hard_window_start": f"{DAY}T09:00:00+08:00",
        "hard_window_end": f"{DAY}T13:00:00+08:00",
        "priority": 1,
    }
    created = client.post("/jobs", json=payload)
    assert created.status_code == 201
    job = created.json()
    assert job["customer"] == "Test Tower"

    fetched = client.get(f"/jobs/{job['id']}").json()
    assert abs(fetched["lat"] - 3.15) < 1e-6
    assert abs(fetched["lon"] - 101.71) < 1e-6

    assert client.delete(f"/jobs/{job['id']}").status_code == 204
    assert client.get(f"/jobs/{job['id']}").status_code == 404


def test_rejects_a_window_shorter_than_the_job(client):
    """Validation at the edge, not deep in the solver."""
    r = client.post(
        "/jobs",
        json={
            "customer": "Impossible",
            "lat": 3.15,
            "lon": 101.71,
            "duration_minutes": 240,
            "hard_window_start": f"{DAY}T09:00:00+08:00",
            "hard_window_end": f"{DAY}T10:00:00+08:00",
        },
    )
    assert r.status_code == 422
    assert "could never be scheduled" in r.text


def test_rejects_a_reversed_shift(client):
    r = client.post(
        "/technicians",
        json={
            "name": "Backwards",
            "skills": [],
            "shift_start": "17:00",
            "shift_end": "08:00",
            "lat": 3.15,
            "lon": 101.71,
        },
    )
    assert r.status_code == 422
    assert "must be after" in r.text


def test_rejects_out_of_range_coordinates(client):
    r = client.post(
        "/jobs",
        json={
            "customer": "Nowhere",
            "lat": 999,
            "lon": 101.71,
            "duration_minutes": 60,
            "hard_window_start": f"{DAY}T09:00:00+08:00",
            "hard_window_end": f"{DAY}T13:00:00+08:00",
        },
    )
    assert r.status_code == 422


# --- Solve ------------------------------------------------------------------


def test_run_result_is_complete_and_valid(client, run_id):
    res = client.get(f"/solve/runs/{run_id}/result").json()
    m = res["metrics"]
    assert m["valid"], m["violations"]
    assert m["assigned"] + m["unassigned_count"] == m["total_jobs"]
    assert res["routes"]


def test_every_unassigned_job_has_a_reason(client, run_id):
    """The spec requires it: 'Unassigned jobs come with a reason.'"""
    res = client.get(f"/solve/runs/{run_id}/result").json()
    for u in res["unassigned"]:
        assert u["reason"] != "undetermined"
        assert u["message"]


def test_routes_are_internally_consistent(client, run_id):
    res = client.get(f"/solve/runs/{run_id}/result").json()
    for route in res["routes"]:
        seqs = [v["sequence"] for v in route["visits"]]
        assert seqs == sorted(seqs)
        assert seqs == list(range(len(seqs)))
        for v in route["visits"]:
            assert v["arrive"] <= v["start"] < v["end"]


def test_latest_endpoint_matches_a_run(client, run_id):
    latest = client.get(f"/solve/day/{DAY}/latest").json()
    assert latest["metrics"]["total_jobs"] > 0


def test_unknown_run_is_404(client):
    assert client.get("/solve/runs/999999").status_code == 404


# --- Reassign ---------------------------------------------------------------


def test_reassign_previews_without_committing(client, run_id):
    res = client.get(f"/solve/runs/{run_id}/result").json()
    src = next(r for r in res["routes"] if r["visits"])
    dst = next(r for r in res["routes"] if r["technician_ref"] != src["technician_ref"])
    job = src["visits"][-1]

    preview = client.post(
        "/solve/reassign",
        json={
            "run_id": run_id,
            "job_id": job["job_id"],
            "technician_id": dst["technician_id"],
            "time_limit_s": 10,
            "commit": False,
        },
    ).json()

    assert "ok" in preview
    if preview["ok"]:
        assert preview["valid"], "a previewed move must still be a valid schedule"
        assert "travel_delta_minutes" in preview

    # Nothing was written.
    after = client.get(f"/solve/runs/{run_id}/result").json()
    assert after["metrics"]["assigned"] == res["metrics"]["assigned"]


def test_reassign_names_the_customers_to_phone(client, run_id):
    """A count is not actionable on its own.

    One drag typically retimes a dozen jobs within their promised windows.
    Those need no phone call; a reassignment or a drop does. The preview has
    to separate the two and name the customers, or a dispatcher cannot judge
    whether "3 customer call(s)" is worth it.
    """
    res = client.get(f"/solve/runs/{run_id}/result").json()
    src = next(r for r in res["routes"] if len(r["visits"]) > 2)
    dst = next(
        r for r in res["routes"]
        if r["technician_ref"] != src["technician_ref"] and r["visits"]
    )
    preview = client.post(
        "/solve/reassign",
        json={
            "run_id": run_id,
            "job_id": src["visits"][-1]["job_id"],
            "technician_id": dst["technician_id"],
            "time_limit_s": 15,
            "commit": False,
        },
    ).json()

    if not preview["ok"]:
        pytest.skip(f"move rejected: {preview['reason']}")

    assert len(preview["calls"]) == preview["customer_calls"]
    # Customer names, not job refs -- this is read aloud off a screen.
    for line in preview["calls"]:
        assert not line.startswith("J"), f"{line} looks like a ref, not a name"
        assert ":" in line
    # Retimings are counted in moved_jobs but must never become calls.
    assert len(preview["moved_jobs"]) >= preview["customer_calls"]


def test_reassign_refuses_an_unqualified_technician(client, run_id):
    """The refusal carries a reason a dispatcher can act on, not a 500."""
    res = client.get(f"/solve/runs/{run_id}/result").json()
    techs = {t["id"]: t for t in client.get("/technicians").json()}
    jobs = {j["id"]: j for j in client.get("/jobs", params={"day": DAY}).json()}

    for route in res["routes"]:
        for v in route["visits"]:
            job = jobs.get(v["job_id"])
            if not job or not job["required_skills"]:
                continue
            for tid, t in techs.items():
                if not set(job["required_skills"]) <= set(t["skills"]):
                    preview = client.post(
                        "/solve/reassign",
                        json={
                            "run_id": run_id,
                            "job_id": v["job_id"],
                            "technician_id": tid,
                            "time_limit_s": 10,
                            "commit": False,
                        },
                    ).json()
                    assert preview["ok"] is False
                    assert "does not have" in preview["reason"]
                    return
    pytest.skip("no unqualified pairing available in this instance")


# --- Dispatch ---------------------------------------------------------------


def test_dispatch_provider_reports_readiness(client):
    body = client.get("/dispatch/provider").json()
    assert "provider" in body
    assert "ready" in body


def test_dispatch_apply_previews_a_typed_change(client, run_id):
    """Goes straight to /apply with a hand-built change, bypassing the LLM.

    That separation is the point: the deterministic half is testable without
    a model, a network call, or a credential.
    """
    techs = client.get("/technicians").json()
    body = client.post(
        "/dispatch/apply",
        json={
            "run_id": run_id,
            "change": {
                "kind": "remove_technician",
                "technician_ref": f"T{techs[0]['id']}",
                "confidence": 1.0,
            },
            "now": "12:00",
            "time_limit_s": 15,
            "commit": False,
        },
    ).json()

    assert body["ok"] is True
    assert body["valid"] is True
    assert techs[0]["name"] in body["summary"]


def test_dispatch_apply_rejects_an_unknown_technician(client, run_id):
    body = client.post(
        "/dispatch/apply",
        json={
            "run_id": run_id,
            "change": {"kind": "remove_technician", "technician_ref": "T9999",
                       "confidence": 1.0},
            "now": "12:00",
            "commit": False,
        },
    ).json()
    assert body["ok"] is False
    assert "not working today" in body["reason"]


def test_dispatch_rejects_a_malformed_change_at_the_schema(client, run_id):
    """change_shift without new_shift_end never reaches the solver."""
    r = client.post(
        "/dispatch/apply",
        json={
            "run_id": run_id,
            "change": {"kind": "change_shift", "technician_ref": "T1",
                       "confidence": 1.0},
            "now": "12:00",
            "commit": False,
        },
    )
    assert r.status_code == 422
    assert "new_shift_end" in r.text


# --- Technician access and /field scoping (field phase 2) --------------------


@pytest.fixture(scope="module")
def technician_ids(client) -> list[int]:
    rows = client.get("/technicians").json()
    if len(rows) < 2:
        pytest.skip("needs at least two seeded technicians")
    return [r["id"] for r in rows[:2]]


def _sign_in(client, technician_id: int) -> str:
    """Issue a code and redeem it. Returns the bearer token."""
    code = client.post(f"/technicians/{technician_id}/access-code").json()["code"]
    r = client.post("/field/auth/redeem", json={"code": code})
    assert r.status_code == 200, r.text
    return r.json()["token"]


def test_a_code_can_be_issued_and_redeemed_for_a_token(client, technician_ids):
    tech_id = technician_ids[0]
    issued = client.post(f"/technicians/{tech_id}/access-code")
    assert issued.status_code == 201
    body = issued.json()
    # Two groups of four. This is the format a dispatcher reads out.
    assert len(body["code"]) == 9 and body["code"][4] == "-"

    session = client.post("/field/auth/redeem", json={"code": body["code"]}).json()
    assert session["technician"]["id"] == tech_id
    assert len(session["token"]) == 43


def test_a_code_typed_messily_still_works(client, technician_ids):
    """Lowercase, no dash, and O where the screen showed 0."""
    code = client.post(
        f"/technicians/{technician_ids[0]}/access-code"
    ).json()["code"]
    typed = code.lower().replace("-", "").replace("0", "o")
    assert client.post("/field/auth/redeem", json={"code": typed}).status_code == 200


def test_a_code_is_single_use(client, technician_ids):
    code = client.post(
        f"/technicians/{technician_ids[0]}/access-code"
    ).json()["code"]
    assert client.post("/field/auth/redeem", json={"code": code}).status_code == 200
    assert client.post("/field/auth/redeem", json={"code": code}).status_code == 401


def test_issuing_a_new_code_kills_the_previous_unredeemed_one(
    client, technician_ids
):
    """Two live codes for one person is a support call waiting to happen."""
    tech_id = technician_ids[0]
    first = client.post(f"/technicians/{tech_id}/access-code").json()["code"]
    client.post(f"/technicians/{tech_id}/access-code")
    assert client.post("/field/auth/redeem", json={"code": first}).status_code == 401


def test_a_wrong_code_is_rejected_without_saying_why(client):
    r = client.post("/field/auth/redeem", json={"code": "ZZZZ-9999"})
    assert r.status_code == 401
    detail = r.json()["detail"].lower()
    # Must not distinguish expired / spent / revoked / never existed -- that
    # would make this endpoint an oracle for probing valid codes.
    for leak in ("expired", "already", "revoked", "unknown", "no such"):
        assert leak not in detail


def test_field_routes_require_a_token(client):
    assert client.get("/field/me").status_code == 401
    assert (
        client.get("/field/me", headers={"Authorization": "Bearer nonsense"})
    ).status_code == 401


def test_the_token_decides_whose_day_it_is(client, technician_ids):
    """The core scoping guarantee, stated as a test.

    Two technicians sign in; each token resolves to its own person. There is
    no technician_id parameter anywhere under /field, so a client cannot even
    express the request for someone else's data -- which is what makes this
    hold for every route added in later phases, not just this one.
    """
    a, b = technician_ids
    token_a = _sign_in(client, a)
    token_b = _sign_in(client, b)

    me_a = client.get("/field/me", headers={"Authorization": f"Bearer {token_a}"})
    me_b = client.get("/field/me", headers={"Authorization": f"Bearer {token_b}"})

    assert me_a.json()["id"] == a
    assert me_b.json()["id"] == b
    assert me_a.json()["id"] != me_b.json()["id"]


def test_revoking_access_logs_out_a_phone_that_already_has_a_token(
    client, technician_ids
):
    """The point of the code -> token link.

    Revoking only the unredeemed code would leave every signed-in handset
    working exactly as before, which is not what "revoke" means.
    """
    tech_id = technician_ids[0]
    token = _sign_in(client, tech_id)
    auth = {"Authorization": f"Bearer {token}"}
    assert client.get("/field/me", headers=auth).status_code == 200

    revoked = client.delete(f"/technicians/{tech_id}/access").json()
    assert revoked["tokens_revoked"] >= 1
    assert client.get("/field/me", headers=auth).status_code == 401


def test_access_status_reports_live_codes_and_signed_in_devices(
    client, technician_ids
):
    tech_id = technician_ids[1]
    client.delete(f"/technicians/{tech_id}/access")

    before = [
        r
        for r in client.get("/technicians/access").json()
        if r["technician_id"] == tech_id
    ][0]
    assert before["has_live_code"] is False
    assert before["active_devices"] == 0

    client.post(f"/technicians/{tech_id}/access-code")
    waiting = [
        r
        for r in client.get("/technicians/access").json()
        if r["technician_id"] == tech_id
    ][0]
    assert waiting["has_live_code"] is True
    assert waiting["code_expires_at"] is not None

    client.delete(f"/technicians/{tech_id}/access")


def test_issuing_a_code_for_an_unknown_technician_is_404(client):
    assert client.post("/technicians/999999/access-code").status_code == 404


def test_health_config_reports_the_allowed_origins(client):
    """CORS failures are invisible server-side, so the config is readable."""
    origins = client.get("/health/config").json()["cors_origins"]
    assert isinstance(origins, list)
    # The technician PWA's origin must be there or the phone cannot call in.
    assert any(":3002" in o for o in origins)


# --- GET /field/today (field phase 3) ----------------------------------------


@pytest.fixture(scope="module")
def field_day(client, technician_ids, run_id):
    """A signed-in technician who has jobs on DAY.

    Depends on `run_id` so a schedule exists. Picks the first technician the
    solve actually gave work to -- an idle technician is a legitimate result
    and would make every assertion below vacuous.
    """
    result = client.get(f"/solve/runs/{run_id}/result").json()
    busy = [r for r in result["routes"] if r["visits"]]
    if not busy:
        pytest.skip("no technician has any jobs in this run")

    tech_id = busy[0]["technician_id"]
    token = _sign_in(client, tech_id)
    body = client.get(
        "/field/today",
        params={"day": DAY},
        headers={"Authorization": f"Bearer {token}"},
    )
    assert body.status_code == 200, body.text
    return tech_id, token, body.json()


def test_today_requires_a_token(client):
    assert client.get("/field/today").status_code == 401


def test_today_returns_the_technicians_own_jobs_in_visit_order(field_day):
    tech_id, _, day = field_day
    assert day["technician_id"] == tech_id
    assert day["jobs"], "expected at least one job"

    sequences = [j["sequence"] for j in day["jobs"]]
    assert sequences == sorted(sequences)
    # Visit order means arrival times ascend too. If these ever disagreed,
    # the technician would be reading the day in one order and driving it in
    # another.
    arrivals = [j["arrive"] for j in day["jobs"]]
    assert arrivals == sorted(arrivals)


def test_today_matches_what_the_dispatcher_console_shows(client, run_id, field_day):
    """The two halves of the system must agree about the same schedule.

    The console reads `/solve/runs/{id}/result`; the phone reads
    `/field/today`. Different routes, different shapes, same run -- and if
    they ever diverged, a dispatcher and a technician would be looking at
    different days while both believed they were looking at the same one.
    """
    tech_id, _, day = field_day
    result = client.get(f"/solve/runs/{run_id}/result").json()
    route = next(r for r in result["routes"] if r["technician_id"] == tech_id)

    assert [j["id"] for j in day["jobs"]] == [v["job_id"] for v in route["visits"]]


def test_a_technician_sees_only_their_own_jobs(client, run_id, field_day):
    """The scoping guarantee, over real data.

    Every job on this technician's screen is assigned to them in the run, and
    no job belonging to anyone else appears.
    """
    tech_id, _, day = field_day
    result = client.get(f"/solve/runs/{run_id}/result").json()

    mine = {
        v["job_id"]
        for r in result["routes"]
        if r["technician_id"] == tech_id
        for v in r["visits"]
    }
    others = {
        v["job_id"]
        for r in result["routes"]
        if r["technician_id"] != tech_id
        for v in r["visits"]
    }

    shown = {j["id"] for j in day["jobs"]}
    assert shown <= mine
    assert not (shown & others)


def test_two_technicians_get_different_days(client, run_id):
    result = client.get(f"/solve/runs/{run_id}/result").json()
    busy = [r for r in result["routes"] if r["visits"]]
    if len(busy) < 2:
        pytest.skip("needs two technicians with work")

    days = []
    for route in busy[:2]:
        token = _sign_in(client, route["technician_id"])
        days.append(
            client.get(
                "/field/today",
                params={"day": DAY},
                headers={"Authorization": f"Bearer {token}"},
            ).json()
        )

    a, b = ({j["id"] for j in d["jobs"]} for d in days)
    assert a and b
    assert not (a & b), "two technicians were shown the same job"


def test_times_are_rendered_in_the_dispatch_timezone(field_day):
    """Not UTC.

    The PWA reads the hour straight out of the ISO string rather than through
    the device clock, so a phone on the wrong timezone still shows Malaysian
    job times -- which only works if the offset in the string is Malaysian.
    Serving UTC would put every job on the Today screen eight hours early and
    look entirely plausible.
    """
    _, _, day = field_day
    for job in day["jobs"]:
        for key in ("arrive", "depart", "window_start", "window_end"):
            assert job[key].endswith("+08:00"), f"{key} = {job[key]}"
    assert day["server_time"].endswith("+08:00")


def test_the_day_carries_everything_the_detail_screen_needs(field_day):
    """Offline-first means one payload has to serve every screen.

    Phase 6 caches this response; a technician in a basement must still be
    able to open a job and see where they are going. A `GET /field/jobs/{id}`
    that only worked with signal would defeat the point.
    """
    _, _, day = field_day
    for job in day["jobs"]:
        for key in (
            "customer", "lat", "lon", "arrive", "depart", "duration_seconds",
            "window_start", "window_end", "parts", "status",
        ):
            assert job[key] is not None, key


def test_finish_estimate_is_the_last_job_leaving(field_day):
    _, _, day = field_day
    assert day["finish_estimate"] == day["jobs"][-1]["depart"]


def test_a_day_with_no_solve_is_empty_not_an_error(client, technician_ids):
    """A morning before dispatch has solved is not a broken app."""
    token = _sign_in(client, technician_ids[0])
    r = client.get(
        "/field/today",
        params={"day": "2019-01-01"},
        headers={"Authorization": f"Bearer {token}"},
    )
    assert r.status_code == 200
    body = r.json()
    assert body["run_id"] is None
    assert body["jobs"] == []
    assert body["finish_estimate"] is None


def test_status_is_reported_in_the_technicians_vocabulary(field_day):
    """jobs.status and the phone's statuses are different vocabularies."""
    _, _, day = field_day
    allowed = {"upcoming", "en_route", "arrived", "complete"}
    assert {j["status"] for j in day["jobs"]} <= allowed


def test_seeded_jobs_carry_the_detail_the_job_screen_renders(field_day):
    """The job detail screen has no route of its own -- it renders from this
    payload, so the payload has to actually carry the fields.

    Skips rather than fails on a database seeded before migration 0004: the
    columns are nullable by design and old rows genuinely have no address.
    """
    _, _, day = field_day
    with_detail = [j for j in day["jobs"] if j["address"]]
    if not with_detail:
        pytest.skip("day predates migration 0004 -- re-seed to populate detail")

    for job in with_detail:
        assert job["area"], job["id"]
        assert job["service_type"], job["id"]
        assert job["fault_description"], job["id"]
        # E.164. A local-format number with a leading 0 does not dial
        # reliably from a handset whose SIM region was never set, and the
        # detail screen turns this straight into a tel: link.
        assert job["phone"].startswith("+60"), job["phone"]


def test_coordinates_are_navigable(field_day):
    """The Navigate button hands off `job.lat`/`job.lon`, not the address.

    The address is written for a human to read; the coordinates are what the
    solver routed against, so navigating to them puts the technician where
    the schedule assumed they would be. Bounds are the Klang Valley -- wide
    enough not to be brittle, tight enough to catch a lat/lon transposition,
    which would otherwise send someone to Kenya with a straight face.
    """
    _, _, day = field_day
    for job in day["jobs"]:
        assert 2.5 < job["lat"] < 3.6, job
        assert 100.9 < job["lon"] < 102.1, job


# --- Status events (field phase 5) -------------------------------------------


def _now_iso() -> str:
    from datetime import UTC, datetime

    return datetime.now(UTC).isoformat()


def _report(client, token, job_id, status, at=None, event_id=None, seq=None):
    import uuid as _uuid

    return client.post(
        f"/field/jobs/{job_id}/status",
        headers={"Authorization": f"Bearer {token}"},
        json={
            "id": event_id or str(_uuid.uuid4()),
            "status": status,
            "at": at or _now_iso(),
            "device_seq": seq,
        },
    )


def _job_at(day, n: int) -> int:
    """The nth job on this day, or skip.

    How many a technician has is whatever the solver decided, and by the time
    these run a committed re-optimisation may have moved some of them
    elsewhere. Indexing blind turns that into an IndexError instead of an
    honest skip.
    """
    if len(day["jobs"]) <= n:
        pytest.skip(f"technician has {len(day['jobs'])} jobs, needed {n + 1}")
    return day["jobs"][n]["id"]


# The order work moves through. Used to assert ADVANCEMENT rather than a
# fixed value: these tests share a database with each other and with anything
# that drove the app by hand, so a job may already be further along than the
# test put it. "Never went backwards" is the real invariant anyway -- pinning
# an exact string would be testing the fixture, not the behaviour.
_RANK = {"upcoming": 0, "en_route": 1, "arrived": 2, "complete": 3}


def test_reporting_a_status_moves_the_job_on_the_technicians_screen(field_day, client):
    _, token, day = field_day
    job_id = _job_at(day, 0)

    assert _report(client, token, job_id, "en_route").status_code == 201

    after = client.get(
        "/field/today",
        params={"day": DAY},
        headers={"Authorization": f"Bearer {token}"},
    ).json()
    shown = next(j for j in after["jobs"] if j["id"] == job_id)
    assert _RANK[shown["status"]] >= _RANK["en_route"]


def test_en_route_has_no_representation_in_jobs_status(field_day, client):
    """The two vocabularies do not line up, and this is the gap.

    Nothing in `jobs.status` means "driving there". `in_progress` would be
    wrong -- solver/reoptimise.py PINS in_progress jobs as physically under
    way, and a technician still in the van is not -- so the column is left
    alone and the job stays movable, which is true.
    """
    _, token, day = field_day
    job_id = _job_at(day, 1)
    before = client.get(f"/jobs/{job_id}").json()["status"]

    _report(client, token, job_id, "en_route")

    assert client.get(f"/jobs/{job_id}").json()["status"] == before


def test_arriving_and_completing_update_the_solver_facing_cache(field_day, client):
    """`jobs.status` is a cache of the event log, kept because api/repo.py
    filters the solver's input on it. Without this, a completed job would stay
    `assigned` and every later solve would re-schedule finished work."""
    _, token, day = field_day
    job_id = _job_at(day, 2)

    _report(client, token, job_id, "arrived")
    assert client.get(f"/jobs/{job_id}").json()["status"] in ("in_progress", "done")

    _report(client, token, job_id, "complete")
    assert client.get(f"/jobs/{job_id}").json()["status"] == "done"


def test_replaying_an_event_records_it_once(field_day, client):
    """The idempotency guarantee, which the offline queue depends on.

    Same client-generated id, sent three times: three successes, one row. The
    response says which delivery was the real one.
    """
    import uuid as _uuid

    _, token, day = field_day
    job_id = _job_at(day, 3)
    event_id = str(_uuid.uuid4())
    at = _now_iso()

    first = _report(client, token, job_id, "en_route", at=at, event_id=event_id)
    assert first.status_code == 201
    assert first.json()["duplicate"] is False

    for _ in range(2):
        again = _report(client, token, job_id, "en_route", at=at, event_id=event_id)
        assert again.status_code == 201
        assert again.json()["duplicate"] is True
        # Identical event, so identical stored times -- a replay must not
        # move the moment the work happened.
        assert again.json()["occurred_at"] == first.json()["occurred_at"]
        assert again.json()["recorded_at"] == first.json()["recorded_at"]


def test_a_late_en_route_cannot_demote_a_completed_job(field_day, client):
    """A phone leaving a dead zone syncs in whatever order it manages.

    This is the scenario the append-only design exists for: an `en_route`
    queued at 09:00 and delivered after the `complete` from 10:15 must leave
    the job complete, on the technician's screen AND in the solver's cache.
    """
    _, token, day = field_day
    job_id = _job_at(day, 4)

    _report(client, token, job_id, "complete")
    late = _report(client, token, job_id, "en_route")

    assert late.status_code == 201
    assert late.json()["job_status"] == "complete"
    assert client.get(f"/jobs/{job_id}").json()["status"] == "done"


def test_a_clock_running_fast_is_clamped_and_flagged(field_day, client):
    from datetime import UTC, datetime, timedelta

    _, token, day = field_day
    job_id = _job_at(day, 5)
    future = (datetime.now(UTC) + timedelta(hours=4)).isoformat()

    body = _report(client, token, job_id, "en_route", at=future).json()
    assert body["time_adjusted"] is True
    assert body["occurred_at"] <= body["recorded_at"]


def test_a_naive_timestamp_is_rejected_at_the_schema(field_day, client):
    """A datetime with no offset would be read as UTC and land eight hours
    off in Malaysia -- the same silent-wrong shape as serving job times
    without an offset. Rejected at the edge rather than guessed at."""
    _, token, day = field_day
    job_id = _job_at(day, 0)
    assert _report(client, token, job_id, "en_route", at="2026-09-03T10:15:00").status_code == 422


def test_an_unknown_status_is_rejected(field_day, client):
    _, token, day = field_day
    assert _report(client, token, _job_at(day, 0), "teleported").status_code == 422


def test_a_technician_cannot_report_on_someone_elses_job(client, run_id, field_day):
    """404, never 403. A 403 would confirm the job exists, which is precisely
    what the technician is not entitled to know."""
    tech_id, _, _ = field_day
    result = client.get(f"/solve/runs/{run_id}/result").json()
    others = [
        v["job_id"]
        for r in result["routes"]
        if r["technician_id"] != tech_id
        for v in r["visits"]
    ]
    if not others:
        pytest.skip("no other technician has work in this run")

    # Sign in as a DIFFERENT technician -- properly authenticated -- and aim
    # at this one's job. The refusal below is about scope, not credentials.
    other_tech = next(
        r["technician_id"] for r in result["routes"] if r["technician_id"] != tech_id
    )
    token = _sign_in(client, other_tech)
    mine = [
        v["job_id"]
        for r in result["routes"]
        if r["technician_id"] == tech_id
        for v in r["visits"]
    ]
    assert _report(client, token, mine[0], "complete").status_code == 404


def test_reporting_on_a_job_that_does_not_exist_is_the_same_404(field_day, client):
    _, token, _ = field_day
    r = _report(client, token, 999999, "en_route")
    assert r.status_code == 404


def test_completing_jobs_does_not_put_holes_in_the_dispatcher_view(
    client, run_id, field_day
):
    """Regression: the console must survive a technician finishing work.

    `repo.load_day` filters the solver's input to unfinished jobs, which is
    right for a fresh solve and wrong for rebuilding a run that already
    happened -- `load_schedule` silently skips assignments whose job is not in
    the Problem, so a completed job left a GAP in its technician's sequence.
    The route rendered with holes and the independent checker rejected the
    schedule as invalid.

    Nothing surfaced it until phase 5, because the field app is the first
    thing in the system that ever sets a job to `done`.
    """
    _, token, day = field_day
    if not day["jobs"]:
        pytest.skip("technician has no jobs in this run")

    _report(client, token, _job_at(day, 0), "complete")

    result = client.get(f"/solve/runs/{run_id}/result").json()
    for route in result["routes"]:
        seqs = [v["sequence"] for v in route["visits"]]
        assert seqs == list(range(len(seqs))), (
            f"technician {route['technician_id']} has gaps: {seqs}"
        )
    assert result["metrics"]["valid"], result["metrics"]["violations"]


def test_a_completed_job_still_appears_on_the_technicians_day(client, field_day):
    """It collapses into the "done" group rather than disappearing.

    A finished job is exactly what a technician needs the address and phone
    number for when the customer rings back an hour later.
    """
    _, token, day = field_day
    if not day["jobs"]:
        pytest.skip("technician has no jobs in this run")
    job_id = _job_at(day, 0)

    _report(client, token, job_id, "complete")

    after = client.get(
        "/field/today",
        params={"day": DAY},
        headers={"Authorization": f"Bearer {token}"},
    ).json()
    shown = next((j for j in after["jobs"] if j["id"] == job_id), None)
    assert shown is not None
    assert shown["status"] == "complete"


# --- Completing a job (field phase 7) ----------------------------------------


# A real 8x8 JPEG, base64. Real bytes because the server checks the magic
# number before writing anything to the photo volume -- and a literal
# rather than generating one, so the test suite does not acquire an
# imaging dependency to produce 600 bytes.
TINY_JPEG = (
    "/9j/4AAQSkZJRgABAQAAAQABAAD/2wBDABQODxIPDRQSEBIXFRQYHjIhHhwcHj0sLiQySUBM"
    "S0dARkVQWnNiUFVtVkVGZIhlbXd7gYKBTmCNl4x9lnN+gXz/2wBDARUXFx4aHjshITt8U0ZT"
    "fHx8fHx8fHx8fHx8fHx8fHx8fHx8fHx8fHx8fHx8fHx8fHx8fHx8fHx8fHx8fHx8fHz/wAAR"
    "CAAIAAgDASIAAhEBAxEB/8QAHwAAAQUBAQEBAQEAAAAAAAAAAAECAwQFBgcICQoL/8QAtRAA"
    "AgEDAwIEAwUFBAQAAAF9AQIDAAQRBRIhMUEGE1FhByJxFDKBkaEII0KxwRVS0fAkM2JyggkK"
    "FhcYGRolJicoKSo0NTY3ODk6Q0RFRkdISUpTVFVWV1hZWmNkZWZnaGlqc3R1dnd4eXqDhIWG"
    "h4iJipKTlJWWl5iZmqKjpKWmp6ipqrKztLW2t7i5usLDxMXGx8jJytLT1NXW19jZ2uHi4+Tl"
    "5ufo6erx8vP09fb3+Pn6/8QAHwEAAwEBAQEBAQEBAQAAAAAAAAECAwQFBgcICQoL/8QAtREA"
    "AgECBAQDBAcFBAQAAQJ3AAECAxEEBSExBhJBUQdhcRMiMoEIFEKRobHBCSMzUvAVYnLRChYk"
    "NOEl8RcYGRomJygpKjU2Nzg5OkNERUZHSElKU1RVVldYWVpjZGVmZ2hpanN0dXZ3eHl6goOE"
    "hYaHiImKkpOUlZaXmJmaoqOkpaanqKmqsrO0tba3uLm6wsPExcbHyMnK0tPU1dbX2Nna4uPk"
    "5ebn6Onq8vP09fb3+Pn6/9oADAMBAAIRAxEAPwDJooor2jkP/9k="
)


def _uncompleted_job(day, offset: int = 0) -> int:
    """A job on this day with no completion yet, skipping `offset` of them.

    Completions are one per job by design, so these tests each need a job of
    their own AND a job nobody has finished -- including a previous run of
    this same suite against the same stack. Asking the day which jobs are
    already done is what makes them re-runnable instead of passing once on a
    freshly seeded database and failing forever after.
    """
    free = [j["id"] for j in day["jobs"] if not j["completed"]]
    if len(free) <= offset:
        pytest.skip(
            f"needed {offset + 1} jobs without a completion, found {len(free)}"
        )
    return free[offset]


def _complete(client, token, job_id, *, photo=None, notes="Done.", cid=None):
    import uuid as _uuid

    return client.post(
        f"/field/jobs/{job_id}/complete",
        headers={"Authorization": f"Bearer {token}"},
        json={
            "id": cid or str(_uuid.uuid4()),
            "parts_used": ["gas_r32", "filter_set"],
            "notes": notes,
            "at": _now_iso(),
            "photo_base64": photo,
        },
    )


def test_the_day_carries_the_parts_catalogue(field_day):
    """So the Complete screen can offer "we also used..." with no signal.

    Eight strings riding along with a response the phone already caches beats
    a second endpoint a technician in a basement cannot reach.
    """
    _, _, day = field_day
    assert "filter_set" in day["parts_catalogue"]
    assert len(day["parts_catalogue"]) >= 5


def test_a_completion_records_what_was_used(field_day, client):
    _, token, day = field_day
    job_id = _uncompleted_job(day, 0)

    body = _complete(client, token, job_id, notes="Regassed and cleaned filters.")
    assert body.status_code == 201, body.text
    out = body.json()
    assert out["duplicate"] is False
    assert sorted(out["parts_used"]) == ["filter_set", "gas_r32"]
    assert out["notes"] == "Regassed and cleaned filters."
    assert out["photo_key"] is None


def test_a_job_is_completed_once(field_day, client):
    """`job_id` is the primary key, so a retry is a no-op by construction.

    That is what lets the offline queue re-send without deduplicating for
    itself -- and it also means a second, different completion for the same
    job does not overwrite the first.
    """
    _, token, day = field_day
    job_id = _uncompleted_job(day, 1)

    first = _complete(client, token, job_id, notes="First answer.").json()
    assert first["duplicate"] is False

    again = _complete(client, token, job_id, notes="Different answer.").json()
    assert again["duplicate"] is True
    assert again["notes"] == "First answer."


def test_a_photo_is_stored_and_readable_by_its_owner(field_day, client):
    _, token, day = field_day
    job_id = _uncompleted_job(day, 2)

    out = _complete(client, token, job_id, photo=TINY_JPEG).json()
    key = out["photo_key"]
    assert key and key.endswith(".jpg")

    got = client.get(
        f"/field/photos/{key}", headers={"Authorization": f"Bearer {token}"}
    )
    assert got.status_code == 200
    assert got.headers["content-type"] == "image/jpeg"
    # SOI marker: what came back is the JPEG that went in, not an error page.
    assert got.content[:3] == bytes([0xFF, 0xD8, 0xFF])


def test_another_technician_cannot_read_the_photo(client, run_id, field_day):
    """A UUID is unguessable. It is not a permission."""
    tech_id, token, day = field_day
    job_id = _uncompleted_job(day, 3)
    key = _complete(client, token, job_id, photo=TINY_JPEG).json()["photo_key"]

    result = client.get(f"/solve/runs/{run_id}/result").json()
    other = next(
        (r["technician_id"] for r in result["routes"] if r["technician_id"] != tech_id),
        None,
    )
    if other is None:
        pytest.skip("only one technician in this run")

    intruder = _sign_in(client, other)
    r = client.get(
        f"/field/photos/{key}", headers={"Authorization": f"Bearer {intruder}"}
    )
    assert r.status_code == 404


def test_a_photo_key_cannot_walk_out_of_the_photo_directory(field_day, client):
    """The key arrives in a URL path, so it is checked rather than trusted."""
    _, token, _ = field_day
    for key in ("../../etc/passwd", "..%2F..%2Fetc%2Fpasswd", "not-a-uuid.jpg"):
        r = client.get(
            f"/field/photos/{key}", headers={"Authorization": f"Bearer {token}"}
        )
        assert r.status_code == 404, key


def test_something_that_is_not_a_jpeg_is_refused(field_day, client):
    import base64

    _, token, day = field_day
    r = _complete(
        client,
        token,
        _uncompleted_job(day, 4),
        photo=base64.b64encode(b"definitely not a jpeg").decode(),
    )
    assert r.status_code == 422
    assert "JPEG" in r.text


def test_completing_someone_elses_job_is_the_same_404(client, run_id, field_day):
    tech_id, _, _ = field_day
    result = client.get(f"/solve/runs/{run_id}/result").json()
    other = next(
        (r["technician_id"] for r in result["routes"] if r["technician_id"] != tech_id),
        None,
    )
    if other is None:
        pytest.skip("only one technician in this run")

    mine = [
        v["job_id"]
        for r in result["routes"]
        if r["technician_id"] == tech_id
        for v in r["visits"]
    ]
    token = _sign_in(client, other)
    assert _complete(client, token, mine[0]).status_code == 404


def test_a_completion_does_not_mark_the_job_done_on_its_own(field_day, client):
    """The status event does that.

    Fabricating one here would put two records of the same moment in an
    append-only log. The phone enqueues the event first and the completion
    second, and the ordered queue keeps them that way.
    """
    _, token, day = field_day
    job_id = _uncompleted_job(day, 5)
    before = client.get(f"/jobs/{job_id}").json()["status"]
    if before == "done":
        pytest.skip("job already complete from an earlier test")

    _complete(client, token, job_id)
    assert client.get(f"/jobs/{job_id}").json()["status"] == before

    _report(client, token, job_id, "complete")
    assert client.get(f"/jobs/{job_id}").json()["status"] == "done"


# --- Schedule changes (field phase 8) ----------------------------------------


@pytest.fixture(scope="module")
def reassignment(client, run_id):
    """Move one job between two technicians and return who was involved.

    Uses the same endpoint the dispatcher console's drag-and-drop uses, so
    what is tested is the path that actually happens rather than a synthetic
    one -- detection hangs off `repo.store_result`, which every schedule write
    passes through.
    """
    result = client.get(f"/solve/runs/{run_id}/result").json()
    busy = [r for r in result["routes"] if r["visits"]]
    if len(busy) < 2:
        pytest.skip("needs two technicians with work")

    giver, taker = busy[0], busy[1]

    # Try each of the giver's jobs in turn. A move can be legitimately refused
    # -- the taker may lack the skill, or have no room in their window -- and
    # skipping on the first refusal made this whole group of tests silently
    # vanish depending on what the solver happened to produce.
    job_id = None
    moved = {}
    for visit in giver["visits"]:
        moved = client.post(
            "/solve/reassign",
            json={
                "run_id": run_id,
                "job_id": visit["job_id"],
                "technician_id": taker["technician_id"],
                "commit": True,
                "time_limit_s": 20,
            },
        ).json()
        if moved.get("ok"):
            job_id = visit["job_id"]
            break
    if job_id is None:
        pytest.skip(f"no job could be moved: {moved.get('reason')}")

    return {
        "job_id": job_id,
        "from_id": giver["technician_id"],
        "to_id": taker["technician_id"],
        "from_name": giver["technician_name"],
        "to_name": taker["technician_name"],
    }


def _changes(client, token):
    return client.get(
        "/field/changes", headers={"Authorization": f"Bearer {token}"}
    ).json()


def test_changes_requires_a_token(client):
    assert client.get("/field/changes").status_code == 401


def test_losing_a_job_tells_the_technician_who_has_it_now(client, reassignment):
    """The sentence this app exists to replace: "don't go to Ampang"."""
    token = _sign_in(client, reassignment["from_id"])
    removed = [
        c
        for c in _changes(client, token)
        if c["kind"] == "removed" and c["job_id"] == reassignment["job_id"]
    ]
    assert removed, "the technician who lost the job was not told"
    detail = removed[0]["detail"]
    assert detail["moved_to"] == reassignment["to_name"]
    assert detail["customer"]
    assert detail["previous_arrive"]


def test_gaining_a_job_tells_the_technician_where_it_came_from(
    client, reassignment
):
    token = _sign_in(client, reassignment["to_id"])
    added = [
        c
        for c in _changes(client, token)
        if c["kind"] == "assigned" and c["job_id"] == reassignment["job_id"]
    ]
    assert added, "the technician who gained the job was not told"
    detail = added[0]["detail"]
    assert detail["moved_from"] == reassignment["from_name"]
    assert detail["new_arrive"]


def test_a_technician_is_only_told_about_their_own_day(client, reassignment):
    """Same scoping rule as everything else under /field."""
    from_token = _sign_in(client, reassignment["from_id"])
    to_token = _sign_in(client, reassignment["to_id"])

    mine = {c["id"] for c in _changes(client, from_token)}
    theirs = {c["id"] for c in _changes(client, to_token)}
    assert mine and theirs
    assert not (mine & theirs)


def test_change_times_carry_the_dispatch_offset(client, reassignment):
    """Not UTC. The phone reads the hour out of the string, so an offset of
    Z would put every "was 09:34" eight hours out and look plausible."""
    token = _sign_in(client, reassignment["from_id"])
    for change in _changes(client, token):
        assert change["created_at"].endswith("+08:00")
        for key in ("previous_arrive", "new_arrive"):
            value = change["detail"].get(key)
            if value:
                assert value.endswith("+08:00"), f"{key} = {value}"


def test_a_retime_has_to_be_worth_interrupting_for(client, reassignment):
    """Every recorded retime moved by at least the configured threshold.

    The dispatcher's noise floor (60s, solver/reoptimise.py) and the
    technician's are different numbers on purpose. Using the dispatcher's here
    would fire the interrupt on almost every solve, and people would learn to
    dismiss it without reading.
    """
    from datetime import datetime

    threshold = 15 * 60
    for tech_id in (reassignment["from_id"], reassignment["to_id"]):
        token = _sign_in(client, tech_id)
        for change in _changes(client, token):
            if change["kind"] != "retimed":
                continue
            before = datetime.fromisoformat(change["detail"]["previous_arrive"])
            after = datetime.fromisoformat(change["detail"]["new_arrive"])
            assert abs((after - before).total_seconds()) >= threshold


def test_acknowledging_removes_it_and_is_idempotent(client, reassignment):
    """The phone queues acks like every other write, so a retry after a dead
    zone is normal. A second 204 is the honest answer to "make sure this is
    acknowledged", which is what the request means."""
    token = _sign_in(client, reassignment["from_id"])
    before = _changes(client, token)
    if not before:
        pytest.skip("nothing outstanding to acknowledge")
    change_id = before[0]["id"]
    auth = {"Authorization": f"Bearer {token}"}

    assert client.post(f"/field/changes/{change_id}/ack", headers=auth).status_code == 204
    assert client.post(f"/field/changes/{change_id}/ack", headers=auth).status_code == 204

    after = {c["id"] for c in _changes(client, token)}
    assert change_id not in after
    assert len(after) == len(before) - 1


def test_acknowledging_someone_elses_change_is_404(client, reassignment):
    to_token = _sign_in(client, reassignment["to_id"])
    theirs = _changes(client, to_token)
    if not theirs:
        pytest.skip("nothing outstanding")

    intruder = _sign_in(client, reassignment["from_id"])
    r = client.post(
        f"/field/changes/{theirs[0]['id']}/ack",
        headers={"Authorization": f"Bearer {intruder}"},
    )
    assert r.status_code == 404


def test_acknowledging_a_change_that_does_not_exist_is_404(client, field_day):
    _, token, _ = field_day
    r = client.post(
        "/field/changes/999999/ack", headers={"Authorization": f"Bearer {token}"}
    )
    assert r.status_code == 404


# --- Re-optimising around reality (field phase 9) ----------------------------


def _latest_run(client) -> int:
    """The newest succeeded run for DAY, resolved at call time.

    NOT the module-scoped `run_id` fixture. Phase 8's tests commit
    reassignments, and phase 9's commit re-optimisations, so by the time these
    run the schedule has moved on -- and `/field/jobs/{id}/status` answers 404
    for a job that is not on the CURRENT run, which is exactly the behaviour
    that makes a reassignment take effect. Re-optimisation operates on the
    schedule that is live, so the tests have to as well.
    """
    runs = client.get("/solve/runs", params={"day": DAY, "limit": 50}).json()
    ok = [r for r in runs if r["status"] == "succeeded"]
    if not ok:
        pytest.skip("no succeeded run for the day")
    return max(ok, key=lambda r: r["id"])["id"]


def test_reoptimising_always_produces_a_usable_schedule(client):
    """Whatever has been reported, a plan comes back.

    This is the robustness property, and it earns a test of its own because
    all four bugs found while building phase 9 failed here and only here: an
    unsatisfiable model returns an EMPTY schedule, which reads exactly like a
    considered plan in which every job on the day was dropped.

    Deliberately makes no assumption about what is in the event log. By the
    time this runs, earlier tests have posted statuses with clamped clocks, on
    jobs since reassigned, several within the same second -- which is a fair
    approximation of a real database after a few weeks, and precisely the
    input that used to break it.
    """
    run_id = _latest_run(client)
    out = client.post(
        "/solve/reoptimise",
        json={"run_id": run_id, "now": "11:40", "time_limit_s": 25, "workers": 4},
    )
    assert out.status_code == 200, out.text
    body = out.json()
    assert body["solver_status"] not in ("INFEASIBLE", "MODEL_ERROR"), body["summary"]
    assert body["ok"] is True, body["summary"]


def test_drift_is_only_claimed_for_technicians_who_were_believed(client):
    """A report whose times could not all be true still says the work
    happened; it stops saying when. Nothing downstream should then quote a
    figure derived from those times."""
    run_id = _latest_run(client)
    body = client.post(
        "/solve/reoptimise",
        json={"run_id": run_id, "now": "11:40", "time_limit_s": 25, "workers": 4},
    ).json()
    assert body["untrusted"] <= body["reported"]
    if body["untrusted"] == body["reported"] and body["reported"] > 0:
        assert body["drift_minutes"] == {}


def test_finishing_late_moves_the_rest_of_the_day(client):
    """The definition of done, over HTTP.

    A technician reports finishing 50 minutes after the schedule said they
    would. Re-optimisation must (a) notice, (b) produce a usable schedule,
    and (c) move that technician's later jobs rather than re-planning around
    a morning that did not happen.
    """
    run_id = _latest_run(client)
    import uuid as _uuid
    from datetime import datetime, timedelta

    result = client.get(f"/solve/runs/{run_id}/result").json()

    # A technician who has not reported anything yet.
    #
    # Earlier tests in this file post statuses, and several of those land on
    # one technician within the same second -- which the vetting in
    # solver/reoptimise.py correctly refuses to believe, because nobody
    # arrives at three addresses in one minute. Picking a clean route is not
    # working around the vetting; it is giving this test a scenario where the
    # thing under test can happen at all.
    route = None
    for candidate in result["routes"]:
        if len(candidate["visits"]) < 2:
            continue
        peek = _sign_in(client, candidate["technician_id"])
        day = client.get(
            "/field/today",
            params={"day": DAY},
            headers={"Authorization": f"Bearer {peek}"},
        ).json()
        if day["jobs"] and all(j["status"] == "upcoming" for j in day["jobs"]):
            route = candidate
            break
    if route is None:
        pytest.skip("every technician with two jobs has already reported")

    tech_id = route["technician_id"]
    first, second = route["visits"][0], route["visits"][1]
    token = _sign_in(client, tech_id)
    auth = {"Authorization": f"Bearer {token}"}

    # A visit's start/end are HH:MM in the dispatch timezone, not ISO -- the
    # console renders them and never parses them back. The status endpoint
    # requires an aware timestamp, so the offset is supplied here.
    def moment(hhmm: str) -> datetime:
        return datetime.fromisoformat(f"{DAY}T{hhmm}:00+08:00")

    arrive = moment(first["start"])
    finished = moment(first["end"]) + timedelta(minutes=50)

    for status, when in (("arrived", arrive), ("complete", finished)):
        r = client.post(
            f"/field/jobs/{first['job_id']}/status",
            headers=auth,
            json={"id": str(_uuid.uuid4()), "status": status, "at": when.isoformat()},
        )
        assert r.status_code == 201, r.text

    out = client.post(
        "/solve/reoptimise",
        json={
            "run_id": run_id,
            "now": finished.strftime("%H:%M"),
            "time_limit_s": 30,
            "workers": 4,
        },
    ).json()

    # (a) It noticed, and put a number on it.
    #
    # Drift is only reported for a technician whose times were believed, so
    # this asserts the vetting accepted THIS technician's reports without
    # pinning `untrusted` to a number: on a shared database other technicians
    # carry reports from earlier tests, and their doubt is not this test's
    # business.
    #
    # 49-51, not exactly 50: the console renders visit times as HH:MM and this
    # test reads them back, so up to a minute of seconds is lost on the way in.
    # Drift itself is computed in seconds.
    assert out["reported"] >= 1
    assert 49 <= out["drift_minutes"].get(f"T{tech_id}", 0) <= 51

    # (b) It produced something usable. Both bugs found while building this
    # failed here, as an INFEASIBLE model returning an empty schedule -- which
    # reads exactly like a plan in which every job was dropped.
    assert out["ok"] is True, out["summary"]
    assert out["solver_status"] not in ("INFEASIBLE", "MODEL_ERROR"), out["summary"]
    # A handful of jobs may genuinely no longer fit after a fifty-minute
    # overrun; that is a schedule, not a collapse. The failure this guards
    # against was total -- an empty schedule showed up as +38.
    assert out["unassigned_delta"] < 5, out["summary"]

    # (c) The next job on that technician's route moved later.
    moved = [m for m in out["moves"] if m.startswith(f"J{second['job_id']}:")]
    assert moved, f"J{second['job_id']} did not move: {out['moves']}"


def test_a_committed_reoptimisation_reaches_the_technician(client):
    """The whole chain, in one test: a phone reports, the schedule re-plans,
    and the phones affected are told about it."""
    run_id = _latest_run(client)
    out = client.post(
        "/solve/reoptimise",
        json={
            "run_id": run_id,
            "now": "10:30",
            "commit": True,
            "time_limit_s": 30,
            "workers": 4,
        },
    ).json()
    if not out["ok"]:
        pytest.skip(f"re-optimisation not usable here: {out['solver_status']}")

    assert out["run_id"] is not None
    new_run = out["run_id"]

    # It became the schedule the phones read.
    result = client.get(f"/solve/runs/{new_run}/result").json()
    busy = [r for r in result["routes"] if r["visits"]]
    assert busy

    # NOT `assert metrics["valid"]`, and the reason is worth stating.
    #
    # A re-optimised run contains FACTS as well as plans: jobs pinned where
    # they really happened. A technician who genuinely overran an SLA leaves a
    # stored schedule that breaks a hard window, and the independent checker
    # is right to say so -- reading that run back reports `window_late`, and
    # the day did run late.
    #
    # What must never appear is a STRUCTURAL violation: a route with gaps, two
    # jobs on one person at once, a skill mismatch. Those would mean the
    # solver produced nonsense, and they are what the earlier
    # window-widening bug looked like once it compounded. So the assertion is
    # about the KIND of violation, not the absence of one.
    for violation in result["metrics"]["violations"]:
        assert violation.startswith("[window_"), violation

    token = _sign_in(client, busy[0]["technician_id"])
    day = client.get(
        "/field/today",
        params={"day": DAY},
        headers={"Authorization": f"Bearer {token}"},
    ).json()
    assert day["run_id"] == new_run


def test_an_unusable_reoptimisation_is_never_committed(client):
    """An empty schedule is the solver saying it could not build a model, and
    storing it would replace a working day with nothing."""
    run_id = _latest_run(client)
    out = client.post(
        "/solve/reoptimise",
        json={"run_id": run_id, "now": "23:59", "time_limit_s": 15, "workers": 4},
    ).json()
    if out["ok"]:
        pytest.skip("re-planning at 23:59 was still satisfiable here")
    assert out["run_id"] is None


# --- Dispatcher access (field phase 10) --------------------------------------


def test_health_reports_whether_the_console_is_open(client):
    """Readable WITHOUT the token, on purpose. "Is this thing open" is the
    question you most want answerable from a machine that cannot get in."""
    state = client.get("/health/config").json()["dispatch_auth"]
    assert state in ("on", "off", "REQUIRED-BUT-UNSET")


def test_dispatcher_routes_reject_a_wrong_token_when_one_is_set(client):
    """The hole this closes: before phase 10, anyone who could reach the API
    could mint a technician token and read that technician's day."""
    if not DISPATCH_TOKEN:
        pytest.skip("stack under test has no DISPATCH_TOKEN (localhost default)")

    bad = {"Authorization": "Bearer definitely-not-the-token"}
    assert client.get("/jobs", headers=bad).status_code == 401
    assert client.post("/technicians/1/access-code", headers=bad).status_code == 401
    # And with none at all.
    assert httpx.get(f"{BASE}/jobs", timeout=30).status_code == 401


def test_health_is_never_behind_the_token(client):
    """Whatever else is locked, the thing a container healthcheck polls is not.
    Locking it would stop the stack converging on `compose up`."""
    assert httpx.get(f"{BASE}/health", timeout=30).status_code == 200
    assert httpx.get(f"{BASE}/health/config", timeout=30).status_code == 200


def test_the_technician_app_never_needs_the_dispatch_secret(client, field_day):
    """The asymmetry, stated as a test.

    The technician token decides WHOSE data; the dispatch token decides
    WHETHER. A phone holds the first and must never be given the second --
    so every /field route has to work with only a technician token, even on a
    stack where the console is locked.
    """
    _, token, _ = field_day
    tech_only = httpx.Client(base_url=BASE, timeout=60)  # no dispatch header
    try:
        auth = {"Authorization": f"Bearer {token}"}
        assert tech_only.get("/field/today", headers=auth).status_code == 200
        assert tech_only.get("/field/changes", headers=auth).status_code == 200
        assert tech_only.get("/field/me", headers=auth).status_code == 200
    finally:
        tech_only.close()
