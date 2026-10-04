"""add users.veto_tokens and users.last_veto_reset_at (Golden Veto)

Existing users start with one token and a fresh 30-day window.

Revision ID: c5d6e7f8a9b0
Revises: b4c5d6e7f8a9
Create Date: 2026-10-04 14:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'c5d6e7f8a9b0'
down_revision: Union[str, None] = 'b4c5d6e7f8a9'
branch_labels: Union[Sequence[str], None] = None
depends_on: Union[Sequence[str], None] = None


def upgrade() -> None:
    with op.batch_alter_table('users') as batch_op:
        batch_op.add_column(sa.Column(
            'veto_tokens', sa.Integer(), nullable=False, server_default='1'))
        batch_op.add_column(sa.Column(
            'last_veto_reset_at', sa.DateTime(), nullable=False,
            server_default=sa.func.current_timestamp()))


def downgrade() -> None:
    with op.batch_alter_table('users') as batch_op:
        batch_op.drop_column('last_veto_reset_at')
        batch_op.drop_column('veto_tokens')
