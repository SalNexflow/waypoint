"""Priority becomes 1 = highest, and the default becomes 3.

The scale was ambiguous: the spec never states a direction, and the codebase
had picked the opposite of the one the project actually wants. The seed
generator treated 3 as most urgent (it was the rare roll), the column
defaulted to 1, and the LLM prompt told the model "3 is most urgent" -- which
is why a model asked for "top priority" correctly answered 3.

Settled as **1 = highest, 2 = normal, 3 = lowest**, everywhere.

Existing rows are REMAPPED, not reinterpreted. A row stored as 3 meant "most
urgent" under the old scale; leaving it alone would silently demote it to
"least urgent". `4 - priority` flips 1<->3 and leaves 2 alone, preserving what
each row meant rather than what it contained.

Revision ID: 0002
Revises: 0001
"""

from collections.abc import Sequence

from alembic import op

revision: str = "0002"
down_revision: str | None = "0001"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # Data first: while the old default is still in place, so a concurrent
    # insert lands on the old scale and gets flipped with everything else
    # rather than arriving mid-change on the new one.
    op.execute("UPDATE jobs SET priority = 4 - priority WHERE priority BETWEEN 1 AND 3")
    op.execute("ALTER TABLE jobs ALTER COLUMN priority SET DEFAULT 3")


def downgrade() -> None:
    op.execute("ALTER TABLE jobs ALTER COLUMN priority SET DEFAULT 1")
    op.execute("UPDATE jobs SET priority = 4 - priority WHERE priority BETWEEN 1 AND 3")
