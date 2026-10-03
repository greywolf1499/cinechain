"""add cached_movie_directors and cached_movies.directors_fetched_at

Revision ID: b8c9d0e1f2a3
Revises: a7b8c9d0e1f2
Create Date: 2026-10-03 00:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
import sqlmodel


# revision identifiers, used by Alembic.
revision: str = 'b8c9d0e1f2a3'
down_revision: Union[str, None] = 'a7b8c9d0e1f2'
branch_labels: Union[Sequence[str], None] = None
depends_on: Union[Sequence[str], None] = None


def upgrade() -> None:
    op.add_column('cached_movies', sa.Column('directors_fetched_at', sa.DateTime(), nullable=True))
    op.create_table(
        'cached_movie_directors',
        sa.Column('movie_id', sa.Integer(), nullable=False),
        sa.Column('person_id', sa.Integer(), nullable=False),
        sa.Column('name', sqlmodel.sql.sqltypes.AutoString(), nullable=False),
        sa.ForeignKeyConstraint(['movie_id'], ['cached_movies.tmdb_id']),
        sa.PrimaryKeyConstraint('movie_id', 'person_id'),
    )
    op.create_index(op.f('ix_cached_movie_directors_person_id'), 'cached_movie_directors', ['person_id'])


def downgrade() -> None:
    op.drop_index(op.f('ix_cached_movie_directors_person_id'), table_name='cached_movie_directors')
    op.drop_table('cached_movie_directors')
    with op.batch_alter_table('cached_movies') as batch:
        batch.drop_column('directors_fetched_at')
