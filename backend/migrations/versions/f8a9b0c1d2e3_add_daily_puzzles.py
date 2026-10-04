"""add daily_puzzles and daily_puzzle_attempts (The Daily Bridge)

Revision ID: f8a9b0c1d2e3
Revises: e7f8a9b0c1d2
Create Date: 2026-10-04 16:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'f8a9b0c1d2e3'
down_revision: Union[str, None] = 'e7f8a9b0c1d2'
branch_labels: Union[Sequence[str], None] = None
depends_on: Union[Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        'daily_puzzles',
        sa.Column('puzzle_date', sa.String(), nullable=False),
        sa.Column('puzzle_number', sa.Integer(), nullable=False),
        sa.Column('start_movie_id', sa.Integer(), nullable=False),
        sa.Column('target_movie_id', sa.Integer(), nullable=False),
        sa.Column('par_hops', sa.Integer(), nullable=False),
        sa.Column('optimal_path', sa.JSON(), nullable=True),
        sa.Column('created_at', sa.DateTime(), nullable=False),
        sa.PrimaryKeyConstraint('puzzle_date'),
    )
    op.create_table(
        'daily_puzzle_attempts',
        sa.Column('puzzle_date', sa.String(), nullable=False),
        sa.Column('user_id', sa.String(), nullable=False),
        sa.Column('status', sa.String(), nullable=False),
        sa.Column('chain', sa.JSON(), nullable=True),
        sa.Column('grades', sa.JSON(), nullable=True),
        sa.Column('run_id', sa.String(), nullable=True),
        sa.Column('started_at', sa.DateTime(), nullable=False),
        sa.Column('finished_at', sa.DateTime(), nullable=True),
        sa.ForeignKeyConstraint(['puzzle_date'], ['daily_puzzles.puzzle_date']),
        sa.ForeignKeyConstraint(['user_id'], ['users.id']),
        sa.PrimaryKeyConstraint('puzzle_date', 'user_id'),
    )


def downgrade() -> None:
    op.drop_table('daily_puzzle_attempts')
    op.drop_table('daily_puzzles')
