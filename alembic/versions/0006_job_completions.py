"""What was actually done on a job.

`job_id` is the primary key, not a surrogate. One completion per job is the
real constraint, and making it the key means a retried upload from the offline
queue is `ON CONFLICT DO NOTHING` rather than something the client has to
deduplicate for itself.

Photos are NOT in this table. `photo_key` names a file in a directory on a
mounted volume (`PHOTO_DIR`). Bytes in Postgres would bloat every backup and
every `pg_dump` with data that is never queried, and there is no object
storage in this stack to put them in instead -- adding a container and a
client library for a few hundred kilobytes per job would be the larger
decision, not the smaller one.

Revision ID: 0006
Revises: 0005
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0006"
down_revision: str | None = "0005"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "job_completions",
        sa.Column("job_id", sa.BigInteger(), nullable=False),
        sa.Column("technician_id", sa.BigInteger(), nullable=False),
        # Names the photo file, so a retry overwrites the same path instead of
        # leaving an orphan behind.
        sa.Column("client_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column(
            "parts_used",
            postgresql.JSONB(astext_type=sa.Text()),
            server_default=sa.text("'[]'::jsonb"),
            nullable=False,
        ),
        sa.Column("notes", sa.String(length=2000), nullable=True),
        sa.Column("photo_key", sa.String(length=80), nullable=True),
        # The technician's clock, clamped like a status event's.
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("client_completed_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column(
            "recorded_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(["job_id"], ["jobs.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(
            ["technician_id"], ["technicians.id"], ondelete="CASCADE"
        ),
        sa.PrimaryKeyConstraint("job_id"),
    )
    op.create_index(
        "ix_job_completions_technician", "job_completions", ["technician_id"]
    )


def downgrade() -> None:
    # DESTRUCTIVE. Drops what technicians recorded about finished work, and
    # leaves the photo files behind on the volume with nothing referencing
    # them -- so the disk usage stays and the meaning does not.
    op.drop_index("ix_job_completions_technician", table_name="job_completions")
    op.drop_table("job_completions")
