"""Add IMDb ids to cached movies without backfilling external data."""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "a2b3c4d5e6f7"
down_revision: str | None = "d1e2f3a4b5c6"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    with op.batch_alter_table("cached_movies") as batch_op:
        batch_op.add_column(sa.Column("imdb_id", sa.String(), nullable=True))


def downgrade() -> None:
    with op.batch_alter_table("cached_movies") as batch_op:
        batch_op.drop_column("imdb_id")
