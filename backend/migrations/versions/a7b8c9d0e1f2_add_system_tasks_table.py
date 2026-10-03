"""add system_tasks table (zero-daemon task runner)

Revision ID: a7b8c9d0e1f2
Revises: f6a7b8c9d0e1
Create Date: 2026-10-03 00:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
import sqlmodel


# revision identifiers, used by Alembic.
revision: str = 'a7b8c9d0e1f2'
down_revision: Union[str, None] = 'f6a7b8c9d0e1'
branch_labels: Union[Sequence[str], None] = None
depends_on: Union[Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        'system_tasks',
        sa.Column('id', sqlmodel.sql.sqltypes.AutoString(), nullable=False),
        sa.Column('name', sqlmodel.sql.sqltypes.AutoString(), nullable=False),
        sa.Column('status', sqlmodel.sql.sqltypes.AutoString(), nullable=False),
        sa.Column('progress_data', sa.JSON(), nullable=True),
        sa.Column('error', sqlmodel.sql.sqltypes.AutoString(), nullable=True),
        sa.Column('user_id', sqlmodel.sql.sqltypes.AutoString(), nullable=True),
        sa.Column('dedupe_key', sqlmodel.sql.sqltypes.AutoString(), nullable=True),
        sa.Column('created_at', sa.DateTime(), nullable=False),
        sa.Column('updated_at', sa.DateTime(), nullable=False),
        sa.ForeignKeyConstraint(['user_id'], ['users.id']),
        sa.PrimaryKeyConstraint('id'),
    )
    op.create_index(op.f('ix_system_tasks_name'), 'system_tasks', ['name'])
    op.create_index(op.f('ix_system_tasks_status'), 'system_tasks', ['status'])
    op.create_index(op.f('ix_system_tasks_user_id'), 'system_tasks', ['user_id'])
    op.create_index(op.f('ix_system_tasks_dedupe_key'), 'system_tasks', ['dedupe_key'])
    op.create_index(op.f('ix_system_tasks_updated_at'), 'system_tasks', ['updated_at'])


def downgrade() -> None:
    op.drop_index(op.f('ix_system_tasks_updated_at'), table_name='system_tasks')
    op.drop_index(op.f('ix_system_tasks_dedupe_key'), table_name='system_tasks')
    op.drop_index(op.f('ix_system_tasks_user_id'), table_name='system_tasks')
    op.drop_index(op.f('ix_system_tasks_status'), table_name='system_tasks')
    op.drop_index(op.f('ix_system_tasks_name'), table_name='system_tasks')
    op.drop_table('system_tasks')
