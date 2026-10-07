"""Persist every curated-list matching outcome for review."""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "c4d5e6f7a8b9"
down_revision: str | None = "b3c4d5e6f7a8"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "curated_list_entries",
        sa.Column("id", sa.String(), primary_key=True),
        sa.Column("list_id", sa.String(), sa.ForeignKey("curated_lists.id"), nullable=False),
        sa.Column("position", sa.Integer(), nullable=False),
        sa.Column("slug", sa.String(), nullable=False),
        sa.Column("title", sa.String(), nullable=False),
        sa.Column("year", sa.Integer()),
        sa.Column("imdb_id", sa.String()),
        sa.Column("tmdb_id", sa.Integer()),
        sa.Column("match_tier", sa.String()),
        sa.Column("status", sa.String(), nullable=False),
        sa.Column("reason", sa.String()),
        sa.Column("attempted_at", sa.DateTime(), nullable=False),
        sa.UniqueConstraint("list_id", "position", name="uq_curated_entry_position"),
        sa.CheckConstraint(
            "status IN ('matched', 'unmatched', 'tv_title', 'ambiguous')",
            name="ck_curated_entry_status",
        ),
    )
    op.create_index("ix_curated_list_entries_list_id", "curated_list_entries", ["list_id"])


def downgrade() -> None:
    op.drop_table("curated_list_entries")
