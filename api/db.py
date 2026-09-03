"""Async engine, session factory, and the FastAPI session dependency."""

from collections.abc import AsyncGenerator

from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)

from api.config import get_settings

_settings = get_settings()

# One engine per process. It owns the connection pool, so it must never be
# created per-request -- that is the async equivalent of opening a new pg
# Pool on every Express handler.
engine: AsyncEngine = create_async_engine(
    _settings.database_url,
    echo=_settings.sql_echo,
    pool_size=5,
    max_overflow=5,
    pool_pre_ping=True,  # cheap liveness check; survives Postgres restarts
)

SessionFactory = async_sessionmaker(
    bind=engine,
    class_=AsyncSession,
    expire_on_commit=False,
)


async def get_session() -> AsyncGenerator[AsyncSession, None]:
    """FastAPI dependency yielding one session per request.

    Two Python idioms here with no clean JS equivalent:

    1. `async with` is an *async context manager*. It calls the object's
       __aenter__ on the way in and __aexit__ on the way out, and __aexit__
       runs even if the body raises -- so the session is always closed. It is
       try/finally with the cleanup written once, on the object, instead of at
       every call site.

    2. This is a generator dependency: `yield` (not `return`) hands the session
       to the route handler and *pauses* here. When the response is finished,
       FastAPI resumes the generator so the `async with` can unwind. Anything
       after the yield is teardown. That is how FastAPI does scoped dependency
       injection -- a route just annotates `session: AsyncSession = Depends(
       get_session)` and gets a live, request-scoped session with guaranteed
       cleanup, without ever touching the engine.
    """
    async with SessionFactory() as session:
        yield session
