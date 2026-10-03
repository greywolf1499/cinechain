"""add runs.engine_version and runs.status_reason; migrate legacy 'abandoned' runs to 'forfeited'

Existing runs are tagged engine_version = 1 so the V2 rules (locked terminal
runs, win/fail conditions) never apply retroactively; new runs default to 2.

Revision ID: c9d0e1f2a3b4
Revises: b8c9d0e1f2a3
Create Date: 2026-10-03 00:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
import sqlmodel


# revision identifiers, used by Alembic.
revision: str = 'c9d0e1f2a3b4'
down_revision: Union[str, None] = 'b8c9d0e1f2a3'
branch_labels: Union[Sequence[str], None] = None
depends_on: Union[Sequence[str], None] = None


def upgrade() -> None:
    # server_default '1' backfills every pre-existing row as a legacy run.
    with op.batch_alter_table('runs') as batch:
        batch.add_column(sa.Column(
            'engine_version', sa.Integer(), nullable=False, server_default='1'))
        batch.add_column(sa.Column(
            'status_reason', sqlmodel.sql.sqltypes.AutoString(), nullable=True))
    # From here on, rows inserted without an explicit value are V2 runs.
    with op.batch_alter_table('runs') as batch:
        batch.alter_column('engine_version', existing_type=sa.Integer(),
                           existing_nullable=False, server_default='2')

    op.execute("UPDATE runs SET engine_version = 1")
    op.execute("UPDATE runs SET status = 'forfeited' WHERE status = 'abandoned'")


def downgrade() -> None:
    op.execute("UPDATE runs SET status = 'abandoned' WHERE status IN ('forfeited', 'failed')")
    with op.batch_alter_table('runs') as batch:
        batch.drop_column('status_reason')
        batch.drop_column('engine_version')
