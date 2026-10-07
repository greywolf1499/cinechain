"""Add cooperative cancellation and navigation links to tasks."""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "b3c4d5e6f7a8"
down_revision: str | None = "a2b3c4d5e6f7"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    with op.batch_alter_table("system_tasks") as batch:
        batch.add_column(
            sa.Column("cancel_requested", sa.Boolean(), nullable=False, server_default=sa.false())
        )
        batch.add_column(sa.Column("link", sa.String(), nullable=True))


def downgrade() -> None:
    with op.batch_alter_table("system_tasks") as batch:
        batch.drop_column("link")
        batch.drop_column("cancel_requested")
