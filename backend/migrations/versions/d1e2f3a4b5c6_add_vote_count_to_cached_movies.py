"""Add TMDB vote counts to cached movies for rating verification.

Revision ID: d1e2f3a4b5c6
Revises: c2d3e4f5a6b7
Create Date: 2026-10-05

"""
from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "d1e2f3a4b5c6"
down_revision: str | None = "c2d3e4f5a6b7"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    with op.batch_alter_table("cached_movies") as batch_op:
        batch_op.add_column(sa.Column("vote_count", sa.Integer(), nullable=True))


def downgrade() -> None:
    with op.batch_alter_table("cached_movies") as batch_op:
        batch_op.drop_column("vote_count")
