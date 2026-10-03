"""add dominant_color and overview_embedding to cached_movies

Revision ID: e1f2a3b4c5d6
Revises: d0e1f2a3b4c5
Create Date: 2026-10-03 23:50:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
import sqlmodel


# revision identifiers, used by Alembic.
revision: str = 'e1f2a3b4c5d6'
down_revision: Union[str, None] = 'd0e1f2a3b4c5'
branch_labels: Union[Sequence[str], None] = None
depends_on: Union[Sequence[str], None] = None


def upgrade() -> None:
    with op.batch_alter_table('cached_movies') as batch_op:
        batch_op.add_column(
            sa.Column('dominant_color', sqlmodel.sql.sqltypes.AutoString(length=7), nullable=True))
        batch_op.add_column(sa.Column('overview_embedding', sa.LargeBinary(), nullable=True))


def downgrade() -> None:
    with op.batch_alter_table('cached_movies') as batch_op:
        batch_op.drop_column('overview_embedding')
        batch_op.drop_column('dominant_color')
