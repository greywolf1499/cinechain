"""add vote_average to cached_movies (TMDB user score, The Rabbit Hole's B-Movie Abyss)

Revision ID: a9b0c1d2e3f4
Revises: f8a9b0c1d2e3
Create Date: 2026-10-04 17:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'a9b0c1d2e3f4'
down_revision: Union[str, None] = 'f8a9b0c1d2e3'
branch_labels: Union[Sequence[str], None] = None
depends_on: Union[Sequence[str], None] = None


def upgrade() -> None:
    with op.batch_alter_table('cached_movies') as batch_op:
        batch_op.add_column(sa.Column('vote_average', sa.Float(), nullable=True))


def downgrade() -> None:
    with op.batch_alter_table('cached_movies') as batch_op:
        batch_op.drop_column('vote_average')
