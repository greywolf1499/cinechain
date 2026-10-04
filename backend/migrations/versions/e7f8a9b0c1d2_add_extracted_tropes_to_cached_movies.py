"""add extracted_tropes to cached_movies (LLM-extracted discrete tropes)

Revision ID: e7f8a9b0c1d2
Revises: d6e7f8a9b0c1
Create Date: 2026-10-04 15:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'e7f8a9b0c1d2'
down_revision: Union[str, None] = 'd6e7f8a9b0c1'
branch_labels: Union[Sequence[str], None] = None
depends_on: Union[Sequence[str], None] = None


def upgrade() -> None:
    with op.batch_alter_table('cached_movies') as batch_op:
        batch_op.add_column(sa.Column('extracted_tropes', sa.JSON(), nullable=True))


def downgrade() -> None:
    with op.batch_alter_table('cached_movies') as batch_op:
        batch_op.drop_column('extracted_tropes')
