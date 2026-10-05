"""Persist each user's Letterboxd watchlist sync status.

Revision ID: c2d3e4f5a6b7
Revises: b1c2d3e4f5a6
Create Date: 2026-10-05 15:00:00.000000

"""
from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "c2d3e4f5a6b7"
down_revision: str | None = "b1c2d3e4f5a6"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    with op.batch_alter_table("users") as batch_op:
        batch_op.add_column(sa.Column("letterboxd_username", sa.String(), nullable=True))
        batch_op.add_column(sa.Column("watchlist_synced_at", sa.DateTime(), nullable=True))
        batch_op.add_column(sa.Column("watchlist_sync_error", sa.String(), nullable=True))

    op.execute(
        sa.text(
            """
            UPDATE users
            SET letterboxd_username = (
                SELECT watchlist.letterboxd_username
                FROM letterboxd_watchlists AS watchlist
                WHERE watchlist.user_id = users.id
                ORDER BY watchlist.synced_at DESC
                LIMIT 1
            ),
            watchlist_synced_at = (
                SELECT MAX(watchlist.synced_at)
                FROM letterboxd_watchlists AS watchlist
                WHERE watchlist.user_id = users.id
            )
            WHERE EXISTS (
                SELECT 1
                FROM letterboxd_watchlists AS watchlist
                WHERE watchlist.user_id = users.id
            )
            """
        )
    )


def downgrade() -> None:
    with op.batch_alter_table("users") as batch_op:
        batch_op.drop_column("watchlist_sync_error")
        batch_op.drop_column("watchlist_synced_at")
        batch_op.drop_column("letterboxd_username")
