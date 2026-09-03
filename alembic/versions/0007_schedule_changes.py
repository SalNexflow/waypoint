"""What changed on a technician's day after they had already seen it.

`/field/today` reads the latest succeeded solve run, so a re-optimisation
rewrites what somebody is looking at without saying so. This table is the
record of that delta, written by `api/schedule_changes.py` whenever a run
supersedes another, and read by the phone as a full-screen interrupt.

`detail` is JSONB and holds structured fields, not a rendered sentence. A
sentence stored here would have baked in a timezone and a wording at write
time, and the phone is what knows how much room it has to say it in.

Revision ID: 0007
Revises: 0006
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0007"
down_revision: str | None = "0006"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "schedule_changes",
        sa.Column("id", sa.BigInteger(), autoincrement=True, nullable=False),
        sa.Column("technician_id", sa.BigInteger(), nullable=False),
        sa.Column("job_id", sa.BigInteger(), nullable=False),
        sa.Column("kind", sa.String(length=20), nullable=False),
        sa.Column(
            "detail",
            postgresql.JSONB(astext_type=sa.Text()),
            server_default=sa.text("'{}'::jsonb"),
            nullable=False,
        ),
        # Which solve did this, so a run can be traced back to what it did to
        # people rather than only to its objective value. SET NULL rather than
        # CASCADE: deleting an old run must not erase the record that somebody
        # was told their afternoon had moved.
        sa.Column("run_id", sa.BigInteger(), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column("acknowledged_at", sa.DateTime(timezone=True), nullable=True),
        sa.ForeignKeyConstraint(
            ["technician_id"], ["technicians.id"], ondelete="CASCADE"
        ),
        sa.ForeignKeyConstraint(["job_id"], ["jobs.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["run_id"], ["solve_runs.id"], ondelete="SET NULL"),
        sa.PrimaryKeyConstraint("id"),
        sa.CheckConstraint(
            "kind IN ('assigned','removed','retimed','cancelled')",
            name="ck_schedule_changes_kind",
        ),
    )

    # The only read path: unacknowledged changes for one technician. PARTIAL,
    # because an acknowledged change is never fetched again -- the index stays
    # the size of what is outstanding rather than of everything that ever
    # happened.
    op.create_index(
        "ix_schedule_changes_unacked",
        "schedule_changes",
        ["technician_id"],
        postgresql_where=sa.text("acknowledged_at IS NULL"),
    )


def downgrade() -> None:
    op.drop_index("ix_schedule_changes_unacked", table_name="schedule_changes")
    op.drop_table("schedule_changes")
