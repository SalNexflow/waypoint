"""Technicians: CRUD, skills, shifts, van stock."""

from __future__ import annotations

from datetime import UTC, datetime, time

from fastapi import APIRouter, Depends, HTTPException, Response
from geoalchemy2.shape import to_shape
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from api import auth
from api.db import get_session
from api.models import (
    AccessCodeOut,
    AccessStatusOut,
    RevokeAccessOut,
    TechnicianIn,
    TechnicianOut,
)
from api.tables import Technician, TechnicianAccessCode, TechnicianToken
from data.seed.persist import point

router = APIRouter(prefix="/technicians", tags=["technicians"])


def _hhmm(t: time) -> str:
    return f"{t.hour:02d}:{t.minute:02d}"


def _parse(s: str) -> time:
    h, m = s.split(":")
    return time(int(h), int(m))


def _out(row: Technician) -> TechnicianOut:
    p = to_shape(row.home_location)
    return TechnicianOut(
        id=row.id,
        name=row.name,
        skills=list(row.skills or []),
        shift_start=_hhmm(row.shift_start),
        shift_end=_hhmm(row.shift_end),
        lat=p.y,
        lon=p.x,
        van_stock=dict(row.van_stock or {}),
        max_jobs=row.max_jobs,
    )


@router.get("", response_model=list[TechnicianOut])
async def list_technicians(
    session: AsyncSession = Depends(get_session),
) -> list[TechnicianOut]:
    rows = (
        (await session.execute(select(Technician).order_by(Technician.id)))
        .scalars()
        .all()
    )
    return [_out(r) for r in rows]


# --- Technician access codes (field phase 2) --------------------------------
#
# SECURITY, STATED PLAINLY: these three routes are UNAUTHENTICATED. Anyone who
# can reach this API can mint a token for any technician and read that
# technician's day. That is the same posture as the rest of the dispatcher
# console -- which has no auth either -- and it is only tolerable while the
# API is bound to localhost.
#
# It stops being tolerable the moment this is reachable from anywhere else,
# and it is the next thing to fix, before the LAN address goes into
# CORS_ORIGINS for device testing. The technician half of the system is now
# authenticated; the dispatcher half is the remaining hole.


@router.get("/access", response_model=list[AccessStatusOut])
async def access_status(
    session: AsyncSession = Depends(get_session),
) -> list[AccessStatusOut]:
    """Every technician with their current access state.

    One query per fact rather than a three-way outer join: the join would
    multiply rows across codes and tokens and need de-duplicating, and at
    twenty technicians the clarity is worth more than the round trips.
    """
    now = datetime.now(UTC)

    technicians = (
        (await session.execute(select(Technician).order_by(Technician.name)))
        .scalars()
        .all()
    )

    live_codes = dict(
        (
            await session.execute(
                select(
                    TechnicianAccessCode.technician_id,
                    func.max(TechnicianAccessCode.expires_at),
                )
                .where(TechnicianAccessCode.redeemed_at.is_(None))
                .where(TechnicianAccessCode.revoked_at.is_(None))
                .where(TechnicianAccessCode.expires_at > now)
                .group_by(TechnicianAccessCode.technician_id)
            )
        ).all()
    )

    device_counts = dict(
        (
            await session.execute(
                select(TechnicianToken.technician_id, func.count())
                .where(TechnicianToken.revoked_at.is_(None))
                .group_by(TechnicianToken.technician_id)
            )
        ).all()
    )

    return [
        AccessStatusOut(
            technician_id=t.id,
            technician_name=t.name,
            has_live_code=t.id in live_codes,
            code_expires_at=live_codes.get(t.id),
            active_devices=device_counts.get(t.id, 0),
        )
        for t in technicians
    ]


@router.post("/{tech_id}/access-code", response_model=AccessCodeOut, status_code=201)
async def issue_access_code(
    tech_id: int, session: AsyncSession = Depends(get_session)
) -> AccessCodeOut:
    """Issue a fresh access code. The plaintext is in this response and
    nowhere else, ever again."""
    row = await session.get(Technician, tech_id)
    if row is None:
        raise HTTPException(404, f"no technician {tech_id}")

    code, code_row = await auth.issue_access_code(session, tech_id)
    await session.commit()
    return AccessCodeOut(
        technician_id=row.id,
        technician_name=row.name,
        code=code,
        expires_at=code_row.expires_at,
    )


@router.delete("/{tech_id}/access", response_model=RevokeAccessOut)
async def revoke_access(
    tech_id: int, session: AsyncSession = Depends(get_session)
) -> RevokeAccessOut:
    """Revoke this technician's unredeemed codes AND every live token.

    Both, because revoking only the code would leave every phone already
    holding a token working exactly as before.
    """
    row = await session.get(Technician, tech_id)
    if row is None:
        raise HTTPException(404, f"no technician {tech_id}")

    codes, tokens = await auth.revoke_technician_access(session, tech_id)
    await session.commit()
    return RevokeAccessOut(
        technician_id=tech_id, codes_revoked=codes, tokens_revoked=tokens
    )


@router.get("/{tech_id}", response_model=TechnicianOut)
async def get_technician(
    tech_id: int, session: AsyncSession = Depends(get_session)
) -> TechnicianOut:
    row = await session.get(Technician, tech_id)
    if row is None:
        raise HTTPException(404, f"no technician {tech_id}")
    return _out(row)


@router.post("", response_model=TechnicianOut, status_code=201)
async def create_technician(
    payload: TechnicianIn, session: AsyncSession = Depends(get_session)
) -> TechnicianOut:
    row = Technician(
        name=payload.name,
        skills=payload.skills,
        shift_start=_parse(payload.shift_start),
        shift_end=_parse(payload.shift_end),
        home_location=point(payload.lat, payload.lon),
        van_stock=payload.van_stock,
        max_jobs=payload.max_jobs,
    )
    session.add(row)
    await session.commit()
    await session.refresh(row)
    return _out(row)


@router.patch("/{tech_id}", response_model=TechnicianOut)
async def update_technician(
    tech_id: int,
    payload: TechnicianIn,
    session: AsyncSession = Depends(get_session),
) -> TechnicianOut:
    row = await session.get(Technician, tech_id)
    if row is None:
        raise HTTPException(404, f"no technician {tech_id}")
    row.name = payload.name
    row.skills = payload.skills
    row.shift_start = _parse(payload.shift_start)
    row.shift_end = _parse(payload.shift_end)
    row.home_location = point(payload.lat, payload.lon)
    row.van_stock = payload.van_stock
    row.max_jobs = payload.max_jobs
    await session.commit()
    await session.refresh(row)
    return _out(row)


@router.delete("/{tech_id}", status_code=204, response_class=Response)
async def delete_technician(
    tech_id: int, session: AsyncSession = Depends(get_session)
) -> Response:
    row = await session.get(Technician, tech_id)
    if row is None:
        raise HTTPException(404, f"no technician {tech_id}")
    await session.delete(row)
    await session.commit()
    return Response(status_code=204)
