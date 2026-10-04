"""heal curated lists whose scraped title was stored blank

Revision ID: f2a3b4c5d6e7
Revises: e1f2a3b4c5d6
Create Date: 2026-10-04 01:00:00.000000

"""
import re
from typing import Sequence, Union
from urllib.parse import urlparse

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'f2a3b4c5d6e7'
down_revision: Union[str, None] = 'e1f2a3b4c5d6'
branch_labels: Union[Sequence[str], None] = None
depends_on: Union[Sequence[str], None] = None


def _title_from_url(url: str) -> str:
    slug = urlparse(url).path.strip('/').split('/')[-1]
    words = [w for w in re.split(r'[-_\s]+', slug) if w]
    return " ".join(w if w.isdigit() else w.capitalize() for w in words) or url


def upgrade() -> None:
    # Discovery used to pick up the (text-less) poster-overlay link as the title.
    # A re-discovery replaces these with the real title; until then show a readable one.
    conn = op.get_bind()
    rows = conn.execute(sa.text(
        "SELECT id, url FROM curated_lists WHERE title IS NULL OR TRIM(title) = ''")).fetchall()
    for row_id, url in rows:
        conn.execute(
            sa.text("UPDATE curated_lists SET title = :title WHERE id = :id"),
            {"title": _title_from_url(url), "id": row_id})


def downgrade() -> None:
    pass
