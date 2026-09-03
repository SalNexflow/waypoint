"""Application settings, loaded once from the environment.

pydantic-settings reads each field from an environment variable of the same
name (case-insensitive), coerces it to the annotated type, and raises at
startup if something required is missing or the wrong shape. There is no
JavaScript equivalent -- it is `process.env` plus zod plus a singleton,
validated before the app is allowed to start rather than at first use.
"""

from functools import lru_cache

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    database_url: str
    timezone: str = "Asia/Kuala_Lumpur"
    log_level: str = "INFO"

    # --- Browser origins allowed to call this API ---------------------------
    # Comma-separated, because pydantic-settings JSON-decodes complex types
    # from the environment: annotate this `list[str]` and `CORS_ORIGINS=a,b`
    # fails at startup with a JSON parse error, while `["a","b"]` in a .env
    # file works. Taking a plain string and splitting it ourselves is the
    # predictable option, and `cors_origin_list` below is the accessor.
    #
    # A phone on the LAN is a DIFFERENT origin from localhost -- it reaches
    # the API at http://192.168.x.x:8000 and is rejected by a list that only
    # names localhost. Add the machine's LAN address here before device
    # testing; that is the whole reason this is configurable.
    cors_origins: str = (
        "http://localhost:3000,http://127.0.0.1:3000,"
        "http://localhost:3002,http://127.0.0.1:3002"
    )

    # --- Dispatcher access (field phase 10) ---------------------------------
    # A shared secret every dispatcher-side route requires as a bearer token.
    #
    # The console had no auth at all, which was fine while the API was bound
    # to localhost and stopped being fine the moment phase 10 put a LAN
    # address into CORS_ORIGINS: anyone on the wifi could mint a technician
    # token, read anybody's day, or delete a job.
    #
    # Empty means no check, which keeps `docker compose up` on a fresh clone
    # working with no setup -- but see `dispatch_auth_required` below: leaving
    # it empty stops being allowed as soon as a non-local origin is trusted.
    dispatch_token: str = ""

    # --- Technician access (field phase 2) ----------------------------------
    # How long a dispatcher-issued code stays redeemable. Long enough to hand
    # over at the end of a shift, short enough that a code on a whiteboard
    # stops working.
    access_code_ttl_hours: int = 24

    # --- Completion photos (field phase 7) ----------------------------------
    # A directory on a mounted volume, not object storage. There is no S3 or
    # MinIO in this stack and the spec names none, so adding a container and a
    # client library to hold a few hundred kilobytes per job would be the
    # larger decision, not the smaller one. `photo_key` is a key into a store;
    # swapping the store for S3 later touches one module.
    photo_dir: str = "/app/data/photos"

    # Cap on a decoded photo. The client downscales to roughly 300KB before
    # sending, so anything near this is a client that did not, or is not ours.
    max_photo_bytes: int = 5 * 1024 * 1024

    # --- Schedule changes (field phase 8) -----------------------------------
    # How far a job has to move before the technician is interrupted about it.
    #
    # Deliberately much larger than solver/reoptimise.py's RETIME_NOISE_S of
    # 60. That number decides what to show a dispatcher reviewing a delta;
    # this one decides what is worth taking over a technician's screen for.
    # A re-solve that shifts a 14:00 job to 14:02 changes nothing anyone does,
    # and using the dispatcher's threshold here would make the interrupt fire
    # on almost every solve -- at which point people learn to dismiss it
    # without reading, and the one that mattered goes with the rest.
    schedule_change_retime_minutes: int = 15

    # Echo every SQL statement to stdout. Useful while learning SQLAlchemy,
    # far too noisy once the seed generator lands in phase 2.
    sql_echo: bool = False

    # --- Routing (phase 3) ---
    # "osrm" requires OSRM, "haversine" never touches it, "auto" prefers OSRM
    # and falls back with a loud warning. Anything produced by the fallback is
    # marked not-reportable, so it cannot leak into a benchmark result.
    routing_provider: str = "auto"
    osrm_url: str = "http://osrm:5000"
    routing_cache_path: str = "/app/.cache/routing.json"
    # Where routing_provider="frozen" reads its precomputed matrix. Shipped in
    # the image rather than mounted: it is a build artefact of a known OSRM
    # graph, and a deployment whose travel times could change under it without
    # a rebuild would be worse than one that cannot change them at all.
    frozen_matrix_path: str = "/app/data/frozen-matrix.json"
    haversine_speed_kmh: float = 22.0
    haversine_detour_factor: float = 1.35

    # --- Solver ---
    # CP-SAT keeps a full copy of its search state PER WORKER. Measured on one
    # 80-job / 15-technician solve at a 120s limit: 4 workers reached 70/80 at
    # 747MB peak RSS, 8 workers reached the same 70/80 at 1616MB. Requests
    # share this container with the web process and everything else, so the
    # default is 4 rather than "however many cores are available".
    solver_workers: int = 4

    @property
    def exposed_beyond_localhost(self) -> bool:
        """Is any non-local origin trusted?

        The one condition that turns the unauthenticated console from a
        development convenience into a hole somebody can walk through.
        """
        local = ("localhost", "127.0.0.1", "[::1]")
        for origin in self.cors_origin_list:
            host = origin.split("//", 1)[-1].split(":", 1)[0]
            if host not in local:
                return True
        return False

    @property
    def dispatch_auth_required(self) -> bool:
        """Whether dispatcher routes demand a token.

        True whenever one is configured. Also true, and unsatisfiable, when
        the API is exposed beyond localhost without one -- which is how the
        app refuses to start serving an open console to a network rather than
        warning about it in a log nobody reads.
        """
        return bool(self.dispatch_token) or self.exposed_beyond_localhost

    @property
    def cors_origin_list(self) -> list[str]:
        """cors_origins parsed. Blank entries dropped so a trailing comma
        does not become an empty origin, which CORS would never match."""
        return [o.strip() for o in self.cors_origins.split(",") if o.strip()]


@lru_cache
def get_settings() -> Settings:
    """Return the one Settings instance for this process.

    @lru_cache makes this a memoised singleton: the first call constructs and
    validates Settings, every later call returns the same object. Using a
    function rather than a module-level constant is what lets FastAPI inject
    it (and lets tests override it) instead of hard-wiring it at import time.
    """
    return Settings()  # type: ignore[call-arg]
