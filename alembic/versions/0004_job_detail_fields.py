"""Job detail: what a technician needs on their phone.

`GET /field/today` promises "customer, address, coordinates, window, duration,
parts, notes", and the job detail screen promises a service type, a fault
description and a phone number. The `jobs` table had a customer name and a
PostGIS point. This adds the rest.

None of it is solver input -- CP-SAT never reads a street name -- so nothing
in solver/, routing/ or the checker changes.

All six columns are NULLABLE and no data is backfilled. Rows that predate this
migration genuinely do not have an address, and inventing one would be worse
than showing nothing: a technician who is shown a fabricated address and drives
to it has been actively misled. `data/seed/` populates them for newly seeded
days; re-seed to get a day with detail on it.

DOWNGRADE IS DESTRUCTIVE. Dropping a column drops its data, and there is
nowhere else in the schema this text exists -- `alembic downgrade 0003`
followed by `upgrade head` leaves six NULL columns, not the values that were
there. Re-seed to restore them. Unavoidable rather than a flaw (the columns
ARE the storage), but worth knowing before running it against anything you
care about.

Revision ID: 0004
Revises: 0003
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0004"
down_revision: str | None = "0003"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


# (name, type) -- kept as data so upgrade and downgrade cannot drift apart.
COLUMNS: tuple[tuple[str, sa.types.TypeEngine], ...] = (
    # Short district label for the Today row ("Setapak"). Denormalised rather
    # than parsed back out of `address` at read time.
    ("area", sa.String(length=80)),
    ("address", sa.String(length=300)),
    # E.164, so the tel: link dials from anywhere.
    ("phone", sa.String(length=32)),
    ("service_type", sa.String(length=80)),
    ("fault_description", sa.String(length=500)),
    ("notes", sa.String(length=1000)),
)


def upgrade() -> None:
    for name, type_ in COLUMNS:
        op.add_column("jobs", sa.Column(name, type_, nullable=True))


def downgrade() -> None:
    for name, _ in reversed(COLUMNS):
        op.drop_column("jobs", name)
