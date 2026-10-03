"""add slug, badge emoji and logo fields to curated_lists; link lists to curator accounts

Revision ID: f6a7b8c9d0e1
Revises: e5f6a7b8c9d0
Create Date: 2026-10-03 00:00:00.000000

"""
import re
from typing import Sequence, Union
from urllib.parse import urlparse

from alembic import op
import sqlalchemy as sa
import sqlmodel


# revision identifiers, used by Alembic.
revision: str = 'f6a7b8c9d0e1'
down_revision: Union[str, None] = 'e5f6a7b8c9d0'
branch_labels: Union[Sequence[str], None] = None
depends_on: Union[Sequence[str], None] = None


def _slugify(text: str) -> str:
    return re.sub(r'[^a-z0-9]+', '-', text.lower()).strip('-')[:64] or "list"


def upgrade() -> None:
    with op.batch_alter_table('curated_lists') as batch:
        batch.add_column(sa.Column(
            'slug', sqlmodel.sql.sqltypes.AutoString(), nullable=True))
        batch.add_column(sa.Column(
            'badge_emoji', sqlmodel.sql.sqltypes.AutoString(), nullable=True))
        batch.add_column(sa.Column(
            'image_url', sqlmodel.sql.sqltypes.AutoString(), nullable=True))
        batch.add_column(sa.Column(
            'image_filename', sqlmodel.sql.sqltypes.AutoString(), nullable=True))
        batch.add_column(sa.Column(
            'image_updated_at', sa.DateTime(), nullable=True))
    op.create_index('ix_curated_lists_slug', 'curated_lists', ['slug'], unique=True)

    # Backfill: stable unique slugs, and link already-imported lists to a known
    # curator account when their Letterboxd URL belongs to one.
    conn = op.get_bind()
    accounts = {
        row.username: row.id
        for row in conn.execute(sa.text("SELECT id, username FROM curated_source_accounts"))
    }
    taken: set[str] = set()
    rows = conn.execute(sa.text(
        "SELECT id, preset_key, url, title, source_account_id FROM curated_lists ORDER BY created_at, id"
    )).all()
    for row in rows:
        segments = [part for part in urlparse(row.url).path.split('/') if part]
        base = _slugify(row.preset_key or (segments[-1] if segments else row.title))
        slug, n = base, 2
        while slug in taken:
            slug = f"{base}-{n}"
            n += 1
        taken.add(slug)
        owner = accounts.get(segments[0].lower()) if segments else None
        conn.execute(
            sa.text("UPDATE curated_lists SET slug = :slug, source_account_id = COALESCE(source_account_id, :owner) "
                    "WHERE id = :id"),
            {"slug": slug, "owner": owner, "id": row.id},
        )


def downgrade() -> None:
    op.drop_index('ix_curated_lists_slug', table_name='curated_lists')
    with op.batch_alter_table('curated_lists') as batch:
        batch.drop_column('image_updated_at')
        batch.drop_column('image_filename')
        batch.drop_column('image_url')
        batch.drop_column('badge_emoji')
        batch.drop_column('slug')
