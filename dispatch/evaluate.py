"""Score an LLM backend against a fixed set of dispatcher phrasings.

Why this exists as a committed module rather than a scratch script: the claim
"llama3.2 scored 4/7, DeepSeek scored N/7" is only worth anything if anyone
can re-run it. The phrasings, the expected changes, and the scoring rule are
all here, so swapping the provider is a flag and the comparison stays honest.

This is NOT a pytest test, deliberately. It costs money, needs a credential,
and its result is a measurement rather than a pass/fail -- a model scoring 6/7
instead of 7/7 is information, not a broken build. The deterministic half of
the parser (JSON extraction, schema validation, resolution, apply) is what
tests/test_dispatch.py covers, and that runs with no model at all.

    docker compose exec api python -m dispatch.evaluate                # .env provider
    docker compose exec api python -m dispatch.evaluate --provider ollama
    docker compose exec api python -m dispatch.evaluate --preview      # also re-solve

Scoring is per-case and strict: the kind must match, and every field listed in
`expect` must match. A case that resolves to the right technician by name when
the model returned a ref (or vice versa) still counts, because resolution is
downstream and deterministic -- what is being measured is whether the model
mapped the sentence onto the right change.
"""

from __future__ import annotations

import argparse
import asyncio
import dataclasses
import os
from dataclasses import dataclass, field
from datetime import date

from api.tables import JOB_STATUSES
from api import repo, service
from api.db import SessionFactory
from dispatch.apply import apply_change
from dispatch.parse import parse
from solver.model import SolverConfig

DAY = date(2026, 9, 3)


@dataclass(frozen=True)
class Case:
    """One dispatcher sentence and what it should turn into.

    `expect_kind=None` means the sentence is deliberately outside the schema
    and the RIGHT answer is a refusal. A model that confidently invents a
    change here is worse than one that says it does not understand, so this
    case is scored like any other.
    """

    text: str
    expect_kind: str | None
    expect: dict = field(default_factory=dict)
    note: str = ""
    # When set, `text` is a format string taking {customer}, filled at runtime
    # with a customer that actually exists in the seeded day, and the expected
    # job id is that job's. A hardcoded name goes stale the moment anyone
    # reseeds -- and a stale name makes a CORRECT refusal look like a miss,
    # which is the worst failure mode an eval can have.
    needs_real_job: bool = False


# The seven. Chosen to cover every supported kind, plus one that must be
# refused and one that is ambiguous on purpose.
CASES: list[Case] = [
    Case(
        "Ahmad called in sick, redistribute his jobs",
        "remove_technician",
        {"technician": "Ahmad"},
        "the canonical one; name not ref",
    ),
    Case(
        "Siti has to leave at 4pm today",
        "change_shift",
        {"technician": "Siti", "new_shift_end": "16:00"},
        "'4pm' -> 24h clock",
    ),
    Case(
        "job 12 is going to take an extra hour",
        "extend_duration",
        {"job": 12, "minutes": 60},
        "'an extra hour' -> 60",
    ),
    Case(
        "cancel the {customer} job, customer rescheduled",
        "cancel_job",
        {},
        "resolve a customer name to a job id",
        needs_real_job=True,
    ),
    Case(
        "make job 8 top priority",
        "change_priority",
        {"job": 8, "priority": 1},
        "'top' -> 1, not 3",
    ),
    Case(
        "add a new job for Menara ABC at 3.15, 101.71, 90 minutes",
        "add_job",
        {"customer": "Menara ABC"},
        "parses, then apply refuses it as unsupported",
    ),
    Case(
        "what's the weather like in Klang today",
        None,
        {},
        "outside the schema; must be refused, not guessed",
    ),
]


def grade(case: Case, result, technicians: list[dict]) -> tuple[bool, str]:
    """Return (passed, explanation).

    `technicians` is needed to resolve a ref back to a name: a model that
    answers "T1" for "Ahmad" is CORRECT, and grading it wrong would measure
    the grader's preference for one identifier over another rather than the
    model's comprehension.
    """
    by_ref = {f"T{t['id']}".lower(): t["name"].lower() for t in technicians}
    if case.expect_kind is None:
        if result.understood:
            return False, f"invented {result.change.kind} for an unrelated sentence"
        return True, "correctly refused"

    if not result.understood:
        return False, f"refused: {result.error}"

    c = result.change
    if c.kind != case.expect_kind:
        return False, f"kind {c.kind}, expected {case.expect_kind}"

    for key, want in case.expect.items():
        if key == "technician":
            ref = (c.technician_ref or "").lower()
            got = f"{ref} {c.technician_name or ''} {by_ref.get(ref, '')}".strip()
            if want.lower() not in got:
                return False, f"technician {got!r} is not {want!r}"
        elif key == "job":
            got_id = c.job_id
            if got_id is None and c.job_ref:
                got_id = int("".join(ch for ch in c.job_ref if ch.isdigit()) or -1)
            if got_id != want:
                return False, f"job {got_id}, expected {want}"
        else:
            got = getattr(c, key, None)
            if got != want:
                return False, f"{key}={got!r}, expected {want!r}"

    return True, "ok"


async def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--provider", default=None, help="deepseek | ollama")
    ap.add_argument("--model", default=None)
    ap.add_argument("--preview", action="store_true",
                    help="also re-solve each understood change and show the diff")
    ap.add_argument("--time-limit", type=float, default=15.0)
    args = ap.parse_args()

    async with SessionFactory() as session:
        run_id = await repo.latest_run_id(session, DAY)
        if run_id is None:
            print("No solve run for", DAY, "-- solve first.")
            return 1
        problem, tech_ids, job_ids = await service.build_problem_for(
            session, DAY, include_statuses=JOB_STATUSES
        )
        current = await repo.load_schedule(session, run_id, problem)
        technicians = [
            {
                "id": tech_ids[t.ref],
                "name": t.name,
                "shift_start": f"{t.shift_start_s // 3600:02d}:00",
                "shift_end": f"{t.shift_end_s // 3600:02d}:00",
                "skills": sorted(t.skills),
            }
            for t in problem.technicians
        ]
        jobs = [{"id": job_ids[j.ref], "customer": j.name} for j in problem.jobs]

    provider = args.provider or os.environ.get("LLM_PROVIDER", "deepseek")
    model = args.model or (
        os.environ.get("DEEPSEEK_MODEL", "deepseek-chat")
        if provider == "deepseek"
        else os.environ.get("OLLAMA_MODEL", "llama3.1")
    )
    print(f"provider : {provider}")
    print(f"model    : {model}")
    print(f"baseline : run #{run_id}, {len(current.visits)} assigned, "
          f"{len(problem.technicians)} technicians\n")

    # Fill the templated case from the day that is actually loaded. Picks a
    # customer whose name occurs exactly once, so a refusal cannot be excused
    # by genuine ambiguity between two identically-named sites.
    counts: dict[str, int] = {}
    for j in jobs:
        counts[j["customer"]] = counts.get(j["customer"], 0) + 1
    unique = next(
        (j for j in jobs if counts[j["customer"]] == 1), jobs[0] if jobs else None
    )

    cases = []
    for case in CASES:
        if not case.needs_real_job:
            cases.append(case)
            continue
        if unique is None:
            print("skipping the customer-name case: no jobs loaded")
            continue
        cases.append(
            dataclasses.replace(
                case,
                text=case.text.format(customer=unique["customer"]),
                expect={**case.expect, "job": unique["id"]},
            )
        )

    passed = 0
    for i, case in enumerate(cases, 1):
        result = await parse(
            case.text, technicians, jobs, provider=provider, model=model
        )
        ok, why = grade(case, result, technicians)
        passed += ok
        mark = "PASS" if ok else "FAIL"
        print(f"{i}. [{mark}] {case.text!r}")
        if result.understood:
            c = result.change
            fields = c.model_dump(exclude_none=True, exclude_defaults=True)
            print(f"        parsed  : {fields}")
        else:
            print(f"        refused : {result.error}")
        if not ok:
            print(f"        why     : {why}")

        if args.preview and result.understood:
            v, res = apply_change(
                problem, current, result.change, 12 * 3600,
                SolverConfig(time_limit_s=args.time_limit,
                             workers=int(os.environ.get("SOLVER_WORKERS", "4"))),
            )
            if not v.ok:
                print(f"        preview : REFUSED -- {v.reason}")
            else:
                print(f"        preview : {v.description}")
                print(f"                  travel {res.travel_delta_s // 60:+d}min, "
                      f"unassigned {res.unassigned_delta:+d}, "
                      f"{res.churn} customer call(s), valid={res.valid}")
        print()

    print(f"score: {passed}/{len(cases)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
