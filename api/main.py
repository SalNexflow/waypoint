"""FastAPI application. Routes only -- no business logic lives here."""

import logging
from contextlib import asynccontextmanager

from fastapi import Depends, FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from api.auth import require_dispatcher
from api.config import Settings, get_settings
from api.db import engine, get_session
from api.routes import dispatch, field, jobs, solve, technicians

logging.basicConfig(level=get_settings().log_level)
log = logging.getLogger("waypoint")

# Waypoint's own tables, deliberately excluding alembic_version and anything
# PostGIS installs. Kept here rather than derived from Base.metadata so the
# health check is an independent assertion about the database, not a
# restatement of the models -- the same instinct as the phase 5 checker.
DOMAIN_TABLES = (
    "technicians",
    "depots",
    "jobs",
    "solve_runs",
    "assignments",
    "technician_access_codes",
    "technician_tokens",
    "job_status_events",
    "job_completions",
    "schedule_changes",
)


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Startup and shutdown, written as one function.

    @asynccontextmanager turns a generator into an async context manager:
    everything before `yield` is startup, everything after is shutdown, and
    FastAPI holds the app open at the yield for the process lifetime. The
    engine's connection pool is disposed on the way out so the container
    exits cleanly instead of leaving Postgres sessions hanging.

    Deliberately does NOT connect to the database on startup. The API should
    boot and answer /health even when Postgres is still coming up, so that
    `docker compose up` converges rather than crash-looping.
    """
    log.info("waypoint api starting")
    yield
    await engine.dispose()
    log.info("waypoint api stopped")


app = FastAPI(
    title="Waypoint",
    description="Field service scheduling optimiser",
    version="0.1.0",
    lifespan=lifespan,
)

# Both UIs are served from different origins than the API in development --
# the dispatcher console on 3000, the technician PWA on 3002 -- so the browser
# needs permission to call across.
#
# Configurable rather than hardcoded because a phone is a third origin again:
# it reaches the API at the dev machine's LAN address, not localhost, and a
# hardcoded list makes real-device testing a code change. Set CORS_ORIGINS.
app.add_middleware(
    CORSMiddleware,
    allow_origins=get_settings().cors_origin_list,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Dispatcher-side routers, all behind one shared token.
#
# Applied at the ROUTER, not per route: a guard added per handler is a guard
# somebody forgets on the next handler, and the thing being protected here is
# "everything that is not the technician app or a health check".
#
# /field/* is deliberately NOT included -- it has its own, per-technician
# auth, and a technician's phone has no business holding the dispatch secret.
_dispatcher = [Depends(require_dispatcher)]

app.include_router(jobs.router, dependencies=_dispatcher)
app.include_router(technicians.router, dependencies=_dispatcher)
app.include_router(solve.router, dependencies=_dispatcher)
app.include_router(dispatch.router, dependencies=_dispatcher)
app.include_router(field.router)


@app.get("/health")
async def health() -> dict[str, str]:
    """Liveness. Touches nothing external, so it answers as soon as the
    process is up. This is what the compose healthcheck polls."""
    return {"status": "ok"}


@app.get("/health/db")
async def health_db(session: AsyncSession = Depends(get_session)) -> JSONResponse:
    """Readiness for the database, including PostGIS and the applied schema.

    `session: AsyncSession = Depends(get_session)` is FastAPI's dependency
    injection. FastAPI runs get_session, hands the yielded session in as this
    argument, and resumes the generator to clean up once the response is sent.
    The route never sees the engine or the pool.

    Counts only Waypoint's own tables. An unqualified count over
    information_schema.tables is misleading: it includes PostGIS's
    spatial_ref_sys, alembic_version, and the geography_columns /
    geometry_columns views, so a healthy database reports 9 for 5 real tables.

    Reports rather than raises: a 503 with a readable reason is more useful
    while bringing the stack up than a stack trace.
    """
    try:
        version = (await session.execute(text("SELECT version()"))).scalar_one()
        postgis = (
            await session.execute(text("SELECT PostGIS_Version()"))
        ).scalar_one()

        found = set(
            (
                await session.execute(
                    text(
                        "SELECT table_name FROM information_schema.tables "
                        "WHERE table_schema = 'public' "
                        "AND table_type = 'BASE TABLE' "
                        "AND table_name = ANY(:expected)"
                    ),
                    {"expected": list(DOMAIN_TABLES)},
                )
            )
            .scalars()
            .all()
        )

        # Which migration is actually applied. The most useful single fact in
        # this response: "the container is running last week's schema" is a
        # real failure mode and otherwise invisible.
        revision = (
            await session.execute(text("SELECT version_num FROM alembic_version"))
        ).scalar_one_or_none()
    except Exception as exc:  # noqa: BLE001 - health checks report, never crash
        log.warning("db health check failed: %s", exc)
        return JSONResponse(
            status_code=503,
            content={"status": "unavailable", "reason": str(exc)},
        )

    missing = sorted(set(DOMAIN_TABLES) - found)
    body = {
        "status": "ok" if not missing else "schema_incomplete",
        "postgres": version.split(" on ")[0],
        "postgis": postgis.strip(),
        "domain_tables": f"{len(found)}/{len(DOMAIN_TABLES)}",
        "migration": revision,
    }
    if missing:
        body["missing_tables"] = missing
        # Not ready: the process is alive but cannot serve. Usually means
        # `alembic upgrade head` has not been run.
        return JSONResponse(status_code=503, content=body)

    return JSONResponse(content=body)


@app.get("/health/routing")
async def health_routing(
    settings: Settings = Depends(get_settings),
) -> JSONResponse:
    """Which travel-time provider is actually in use, and is it reportable.

    Worth an endpoint of its own because the failure this guards is silent:
    with routing_provider=auto, a stopped OSRM container degrades to haversine
    and everything keeps working while every duration quietly becomes wrong by
    about a third. This makes that visible without reading logs.
    """
    from routing import build_provider

    try:
        provider = await build_provider(
            settings.routing_provider,
            osrm_url=settings.osrm_url,
            cache_path=settings.routing_cache_path,
            speed_kmh=settings.haversine_speed_kmh,
            detour_factor=settings.haversine_detour_factor,
        )
    except Exception as exc:  # noqa: BLE001 - health checks report, never crash
        return JSONResponse(
            status_code=503,
            content={"status": "unavailable", "reason": str(exc)},
        )

    reportable = provider.source == "osrm"
    body = {
        "status": "ok" if reportable else "degraded",
        "configured": settings.routing_provider,
        "provider": provider.name,
        "source": provider.source,
        "reportable": reportable,
        "cache": provider.cache.stats if hasattr(provider, "cache") else {},
    }
    if not reportable:
        body["warning"] = (
            "Using the haversine fallback. Travel times are optimistic by "
            "roughly a third; no figure derived from them is a result."
        )
    return JSONResponse(content=body)


@app.get("/health/config")
async def health_config(
    settings: Settings = Depends(get_settings),
) -> dict[str, object]:
    """Confirms which settings the process actually loaded.

    Never echoes database_url -- it carries the password.
    """
    return {
        "timezone": settings.timezone,
        "log_level": settings.log_level,
        # Whether the console is protected, and whether it needs to be.
        # Readable without the token on purpose: "is this thing open" is the
        # question you want answerable from a machine that cannot get in.
        "dispatch_auth": (
            "on"
            if settings.dispatch_token
            else ("REQUIRED-BUT-UNSET" if settings.exposed_beyond_localhost else "off")
        ),
        # Listed because a CORS rejection shows up in the browser as an
        # opaque network error with no server-side trace. Being able to read
        # back what the process actually allows turns a twenty-minute
        # head-scratch into a glance.
        "cors_origins": settings.cors_origin_list,
    }
