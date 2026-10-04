"""add gender to cached_movie_directors (Watchlist Bingo "directed by a woman")

Revision ID: a3b4c5d6e7f8
Revises: f2a3b4c5d6e7
Create Date: 2026-10-04 04:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'a3b4c5d6e7f8'
down_revision: Union[str, None] = 'f2a3b4c5d6e7'
branch_labels: Union[Sequence[str], None] = None
depends_on: Union[Sequence[str], None] = None


def upgrade() -> None:
    with op.batch_alter_table('cached_movie_directors') as batch_op:
        batch_op.add_column(sa.Column('gender', sa.Integer(), nullable=True))


def downgrade() -> None:
    with op.batch_alter_table('cached_movie_directors') as batch_op:
        batch_op.drop_column('gender')
