"""add narrative_year / narrative_era_label to cached_movies (Historical Time-Travel)

Revision ID: b1c2d3e4f5a6
Revises: a9b0c1d2e3f4
Create Date: 2026-10-04 19:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'b1c2d3e4f5a6'
down_revision: Union[str, None] = 'a9b0c1d2e3f4'
branch_labels: Union[Sequence[str], None] = None
depends_on: Union[Sequence[str], None] = None


def upgrade() -> None:
    with op.batch_alter_table('cached_movies') as batch_op:
        batch_op.add_column(sa.Column('narrative_year', sa.Integer(), nullable=True))
        batch_op.add_column(sa.Column('narrative_era_label', sa.String(), nullable=True))


def downgrade() -> None:
    with op.batch_alter_table('cached_movies') as batch_op:
        batch_op.drop_column('narrative_era_label')
        batch_op.drop_column('narrative_year')
