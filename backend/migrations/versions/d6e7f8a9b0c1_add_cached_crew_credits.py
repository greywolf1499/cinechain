"""add cached_crew_credits, cached_crew_people and cached_movies.crew_fetched_at (Crew & Craft Trail)

Revision ID: d6e7f8a9b0c1
Revises: c5d6e7f8a9b0
Create Date: 2026-10-04 15:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
import sqlmodel


# revision identifiers, used by Alembic.
revision: str = 'd6e7f8a9b0c1'
down_revision: Union[str, None] = 'c5d6e7f8a9b0'
branch_labels: Union[Sequence[str], None] = None
depends_on: Union[Sequence[str], None] = None


def upgrade() -> None:
    op.add_column('cached_movies', sa.Column('crew_fetched_at', sa.DateTime(), nullable=True))
    op.create_table(
        'cached_crew_credits',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('movie_id', sa.Integer(), nullable=False),
        sa.Column('person_id', sa.Integer(), nullable=False),
        sa.Column('person_name', sqlmodel.sql.sqltypes.AutoString(), nullable=False),
        sa.Column('job', sqlmodel.sql.sqltypes.AutoString(), nullable=False),
        sa.Column('department', sqlmodel.sql.sqltypes.AutoString(), nullable=False),
        sa.Column('profile_path', sqlmodel.sql.sqltypes.AutoString(), nullable=True),
        sa.ForeignKeyConstraint(['movie_id'], ['cached_movies.tmdb_id']),
        sa.PrimaryKeyConstraint('id'),
        sa.UniqueConstraint('movie_id', 'person_id', 'job'),
    )
    op.create_index(op.f('ix_cached_crew_credits_movie_id'), 'cached_crew_credits', ['movie_id'])
    op.create_index(op.f('ix_cached_crew_credits_person_id'), 'cached_crew_credits', ['person_id'])
    op.create_table(
        'cached_crew_people',
        sa.Column('person_id', sa.Integer(), nullable=False),
        sa.Column('name', sqlmodel.sql.sqltypes.AutoString(), nullable=False),
        sa.Column('credits_fetched_at', sa.DateTime(), nullable=False),
        sa.PrimaryKeyConstraint('person_id'),
    )


def downgrade() -> None:
    op.drop_table('cached_crew_people')
    op.drop_index(op.f('ix_cached_crew_credits_person_id'), table_name='cached_crew_credits')
    op.drop_index(op.f('ix_cached_crew_credits_movie_id'), table_name='cached_crew_credits')
    op.drop_table('cached_crew_credits')
    with op.batch_alter_table('cached_movies') as batch:
        batch.drop_column('crew_fetched_at')
