"""Technician authentication: access codes, bearer tokens, and scoping.

The shape, in one paragraph. A dispatcher issues a short **access code** for
one technician and reads it to them. The technician types it into the PWA
once; the API exchanges it for a long random **bearer token**, which is what
the phone stores and sends on every `/field/*` request. The code is single-use
and expires. The token does not expire and is revocable.

Why two steps rather than one
-----------------------------
A code short enough to say down a phone line is short enough to guess, and a
credential the phone keeps forever needs to be neither. Splitting them lets
each be the right length for its job: the code carries ~40 bits and lives for
a day, the token carries 256 bits and lives until revoked.

Why sha256 and not bcrypt/argon2
--------------------------------
Slow password hashes exist to make *low-entropy human-chosen* secrets
expensive to guess. These are machine-generated random secrets, so there is
nothing to grind: an attacker who cannot brute-force 256 bits is not helped by
the hash being slow, and every legitimate request would pay the cost. Hashing
at rest is still worth doing -- it means a leaked backup or a stray log line
is not a set of live credentials -- and sha256 is the right tool for that.

Lookups are BY hash, never a comparison against a fetched row, so there is no
timing side channel to worry about either.
"""

from __future__ import annotations

import hashlib
import secrets
from datetime import UTC, datetime, timedelta

from fastapi import Depends, HTTPException, Request
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from api.config import get_settings
from api.db import get_session
from api.tables import Technician, TechnicianAccessCode, TechnicianToken

# Crockford's Base32 alphabet. Not invented here: it deliberately omits I, L,
# O and U, which kills the two failure modes that matter when a code is read
# aloud (1/I/l and 0/O look alike; U turns ordinary strings into accidental
# profanity). Codes are normalised on the way in, so a technician who types
# "O" where the screen showed "0" still gets in.
_ALPHABET = "0123456789ABCDEFGHJKMNPQRSTVWXYZ"
_CODE_LEN = 8

# What a mistyped character becomes during normalisation.
_CONFUSIONS = str.maketrans({"O": "0", "I": "1", "L": "1", "U": "V"})

# 401 for a missing or bad token, and the client is told how to fix it.
# auto_error=False because HTTPBearer's own error is a 403 with an unhelpful
# body; this file raises its own.
_bearer = HTTPBearer(auto_error=False, scheme_name="TechnicianToken")


# --- Secret generation ------------------------------------------------------


def new_access_code() -> str:
    """A fresh code, formatted for reading out: "K7M4-XQ2R".

    secrets.choice, not random.choice. The `random` module is a Mersenne
    Twister seeded from the clock -- reproducible, and reconstructible from a
    handful of outputs. `secrets` draws from the OS CSPRNG. They have nearly
    identical APIs, which is exactly why the wrong one gets used.
    """
    raw = "".join(secrets.choice(_ALPHABET) for _ in range(_CODE_LEN))
    return f"{raw[:4]}-{raw[4:]}"


def normalise_code(raw: str) -> str:
    """Anything a human might type -> the 8 canonical characters.

    Accepts lowercase, missing or extra dashes, spaces, and the confusable
    characters. Returns "" for anything that cannot be a code, so callers can
    reject without a second length check.
    """
    cleaned = "".join(ch for ch in raw.upper() if ch.isalnum())
    cleaned = cleaned.translate(_CONFUSIONS)
    if len(cleaned) != _CODE_LEN:
        return ""
    if any(ch not in _ALPHABET for ch in cleaned):
        return ""
    return cleaned


def new_token() -> str:
    """256 bits, URL-safe. 43 characters."""
    return secrets.token_urlsafe(32)


def hash_secret(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def _now() -> datetime:
    """Timezone-aware UTC.

    `datetime.utcnow()` is deprecated and was always a trap: it returns a
    NAIVE datetime holding UTC, which compares wrongly against the aware
    timestamps this database stores. `datetime.now(UTC)` is aware and correct.
    """
    return datetime.now(UTC)


# --- The dispatcher side ----------------------------------------------------

_dispatch_bearer = HTTPBearer(auto_error=False, scheme_name="DispatchToken")


async def require_dispatcher(
    credentials: HTTPAuthorizationCredentials | None = Depends(_dispatch_bearer),
) -> None:
    """Guard every dispatcher-side route with a shared secret.

    NOT per-user, and not pretending to be. There is one dispatch team sharing
    one console, and a shared token is the honest shape of that: it answers
    "is this the office" and nothing more. Per-dispatcher identity would need
    accounts, and the thing that actually needed closing was a network-level
    hole, not an attribution gap.

    Compared with the technician side (which IS per-person, because Ahmad must
    see Ahmad's day and not everyone's), this is deliberately cruder. The
    asymmetry is the point: the technician token decides WHOSE data, this one
    decides WHETHER.

    Disabled when no token is set, so a fresh clone runs with no setup. That
    exemption disappears the moment CORS trusts a non-local origin -- see
    Settings.dispatch_auth_required -- because that is exactly when "anyone
    who can reach the API" stops meaning "whoever is sitting at this machine".

    Compared with `secrets.compare_digest`: used here rather than `==` because
    this IS a comparison against a stored value, unlike the technician tokens
    which are looked up by hash. A timing side channel on a shared secret is
    remote but free to close.
    """
    settings = get_settings()
    if not settings.dispatch_auth_required:
        return

    if not settings.dispatch_token:
        # Exposed beyond localhost with no token configured. Refusing is the
        # only safe answer: the alternative is serving an open console to a
        # network on the strength of a warning in a log.
        raise HTTPException(
            status_code=503,
            detail=(
                "DISPATCH_TOKEN is not set and CORS_ORIGINS trusts a "
                "non-local origin. Set DISPATCH_TOKEN before exposing this "
                "API beyond localhost."
            ),
        )

    presented = credentials.credentials if credentials else ""
    if not secrets.compare_digest(presented, settings.dispatch_token):
        raise HTTPException(
            status_code=401,
            detail="dispatcher token missing or wrong",
            headers={"WWW-Authenticate": "Bearer"},
        )


# --- Issuing and revoking (dispatcher side) ---------------------------------


async def issue_access_code(
    session: AsyncSession, technician_id: int
) -> tuple[str, TechnicianAccessCode]:
    """Mint a code for one technician. Returns (plaintext, row).

    The plaintext is returned exactly once, to be shown once. Nothing stores
    it, so a lost code is reissued rather than looked up.

    Any earlier unredeemed code for this technician is revoked first. Two live
    codes for one person is a support call waiting to happen ("it says invalid"
    -- because they are reading the older one).
    """
    now = _now()
    await session.execute(
        update(TechnicianAccessCode)
        .where(TechnicianAccessCode.technician_id == technician_id)
        .where(TechnicianAccessCode.redeemed_at.is_(None))
        .where(TechnicianAccessCode.revoked_at.is_(None))
        .values(revoked_at=now)
    )

    plaintext = new_access_code()
    row = TechnicianAccessCode(
        technician_id=technician_id,
        code_hash=hash_secret(normalise_code(plaintext)),
        expires_at=now + timedelta(hours=get_settings().access_code_ttl_hours),
    )
    session.add(row)
    await session.flush()
    return plaintext, row


async def revoke_technician_access(
    session: AsyncSession, technician_id: int
) -> tuple[int, int]:
    """Revoke every live code and every live token. Returns (codes, tokens).

    Both halves, always. Revoking only the code would stop future logins and
    leave every phone already holding a token working exactly as before, which
    is not what anybody means by the word.
    """
    now = _now()

    codes = await session.execute(
        update(TechnicianAccessCode)
        .where(TechnicianAccessCode.technician_id == technician_id)
        .where(TechnicianAccessCode.revoked_at.is_(None))
        .where(TechnicianAccessCode.redeemed_at.is_(None))
        .values(revoked_at=now)
    )
    tokens = await session.execute(
        update(TechnicianToken)
        .where(TechnicianToken.technician_id == technician_id)
        .where(TechnicianToken.revoked_at.is_(None))
        .values(revoked_at=now)
    )
    return codes.rowcount or 0, tokens.rowcount or 0


# --- Redeeming (technician side) --------------------------------------------


class InvalidCode(Exception):
    """Raised for every rejection reason, with one message.

    Deliberately undifferentiated. "Expired" and "already used" and "no such
    code" are three different facts, and telling an unauthenticated caller
    which one applies turns the endpoint into an oracle for enumerating valid
    codes. The technician's fix is the same in all three cases: ask dispatch
    for a new one.
    """


async def redeem_code(session: AsyncSession, raw_code: str) -> tuple[str, Technician]:
    """Exchange a code for a token. Returns (plaintext token, technician)."""
    normalised = normalise_code(raw_code)
    if not normalised:
        raise InvalidCode

    now = _now()

    # Mark it spent CONDITIONALLY, and let the database decide the winner.
    #
    # The obvious version -- SELECT, check redeemed_at, then UPDATE -- has a
    # race: two requests carrying the same code can both pass the check before
    # either writes, and both get a token. Folding the check into the UPDATE's
    # WHERE clause makes the row lock do the arbitration, and RETURNING tells
    # us which caller won. Exactly one gets a row back.
    result = await session.execute(
        update(TechnicianAccessCode)
        .where(TechnicianAccessCode.code_hash == hash_secret(normalised))
        .where(TechnicianAccessCode.redeemed_at.is_(None))
        .where(TechnicianAccessCode.revoked_at.is_(None))
        .where(TechnicianAccessCode.expires_at > now)
        .values(redeemed_at=now)
        .returning(TechnicianAccessCode.id, TechnicianAccessCode.technician_id)
    )
    row = result.first()
    if row is None:
        raise InvalidCode

    code_id, technician_id = row

    technician = await session.get(Technician, technician_id)
    if technician is None:
        # The FK cascades on delete, so this means the technician was removed
        # between issue and redeem. Same undifferentiated answer.
        raise InvalidCode

    plaintext = new_token()
    session.add(
        TechnicianToken(
            technician_id=technician_id,
            token_hash=hash_secret(plaintext),
            access_code_id=code_id,
        )
    )
    return plaintext, technician


# --- The dependency every /field route depends on ---------------------------


async def current_technician(
    request: Request,
    credentials: HTTPAuthorizationCredentials | None = Depends(_bearer),
    session: AsyncSession = Depends(get_session),
) -> Technician:
    """Resolve the bearer token to a Technician, or 401.

    Every `/field/*` route takes this as a dependency, and the technician it
    returns is what scopes the query. The rule for the routes that follow: put
    the technician in the WHERE clause, never in an `if` after the fetch. A
    query that cannot return another technician's row cannot be forgotten to
    check, and "return 404, not 403" then falls out for free rather than being
    a thing each handler has to remember.
    """
    if credentials is None or not credentials.credentials:
        raise HTTPException(
            status_code=401,
            detail="missing bearer token",
            headers={"WWW-Authenticate": "Bearer"},
        )

    technician = (
        await session.execute(
            select(Technician)
            .join(TechnicianToken, TechnicianToken.technician_id == Technician.id)
            .where(TechnicianToken.token_hash == hash_secret(credentials.credentials))
            .where(TechnicianToken.revoked_at.is_(None))
        )
    ).scalar_one_or_none()

    if technician is None:
        raise HTTPException(
            status_code=401,
            detail="invalid or revoked token",
            headers={"WWW-Authenticate": "Bearer"},
        )

    # Stashed so a route or a log line can reach it without re-querying.
    request.state.technician = technician
    return technician
