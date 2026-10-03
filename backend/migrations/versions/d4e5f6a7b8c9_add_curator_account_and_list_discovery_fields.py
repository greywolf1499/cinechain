"""add curator account profile fields and curated list discovery fields

Revision ID: d4e5f6a7b8c9
Revises: c3d4e5f6a7b8
Create Date: 2026-10-03 12:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
import sqlmodel


# revision identifiers, used by Alembic.
revision: str = 'd4e5f6a7b8c9'
down_revision: Union[str, None] = 'c3d4e5f6a7b8'
branch_labels: Union[Sequence[str], None] = None
depends_on: Union[Sequence[str], None] = None


def upgrade() -> None:
    op.add_column('curated_source_accounts', sa.Column(
        'avatar_url', sqlmodel.sql.sqltypes.AutoString(), nullable=True))
    op.add_column('curated_source_accounts', sa.Column(
        'bio', sqlmodel.sql.sqltypes.AutoString(), nullable=True))
    op.add_column('curated_source_accounts', sa.Column(
        'is_hq', sa.Boolean(), nullable=False, server_default=sa.false()))
    op.add_column('curated_source_accounts', sa.Column(
        'account_tier', sqlmodel.sql.sqltypes.AutoString(), nullable=True))
    op.add_column('curated_source_accounts', sa.Column(
        'total_public_lists', sa.Integer(), nullable=False, server_default='0'))
    op.add_column('curated_source_accounts', sa.Column(
        'last_inspected_at', sa.DateTime(), nullable=True))
    op.add_column('curated_source_accounts', sa.Column(
        'lists_discovered_at', sa.DateTime(), nullable=True))

    op.add_column('curated_lists', sa.Column(
        'is_enabled', sa.Boolean(), nullable=False, server_default=sa.true()))
    op.add_column('curated_lists', sa.Column(
        'film_count', sa.Integer(), nullable=False, server_default='0'))
    op.add_column('curated_lists', sa.Column(
        'description', sqlmodel.sql.sqltypes.AutoString(), nullable=True))
    op.add_column('curated_lists', sa.Column(
        'preview_posters', sqlmodel.sql.sqltypes.AutoString(), nullable=False,
        server_default='[]'))


def downgrade() -> None:
    with op.batch_alter_table('curated_lists') as batch_op:
        batch_op.drop_column('preview_posters')
        batch_op.drop_column('description')
        batch_op.drop_column('film_count')
        batch_op.drop_column('is_enabled')
    with op.batch_alter_table('curated_source_accounts') as batch_op:
        batch_op.drop_column('lists_discovered_at')
        batch_op.drop_column('last_inspected_at')
        batch_op.drop_column('total_public_lists')
        batch_op.drop_column('account_tier')
        batch_op.drop_column('is_hq')
        batch_op.drop_column('bio')
        batch_op.drop_column('avatar_url')
