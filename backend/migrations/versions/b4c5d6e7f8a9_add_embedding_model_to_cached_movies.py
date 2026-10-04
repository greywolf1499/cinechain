"""add overview_embedding_model to cached_movies (which provider/model produced the vector)

Revision ID: b4c5d6e7f8a9
Revises: a3b4c5d6e7f8
Create Date: 2026-10-04 05:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'b4c5d6e7f8a9'
down_revision: Union[str, None] = 'a3b4c5d6e7f8'
branch_labels: Union[Sequence[str], None] = None
depends_on: Union[Sequence[str], None] = None


def upgrade() -> None:
    with op.batch_alter_table('cached_movies') as batch_op:
        batch_op.add_column(sa.Column('overview_embedding_model', sa.String(), nullable=True))


def downgrade() -> None:
    with op.batch_alter_table('cached_movies') as batch_op:
        batch_op.drop_column('overview_embedding_model')
