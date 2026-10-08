"""Daily outbound provider budgets."""

import sqlalchemy as sa
from alembic import op

revision = "e6f7a8b9c0d1"
down_revision = "d5e6f7a8b9c0"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "provider_budgets",
        sa.Column("provider", sa.String(), nullable=False),
        sa.Column("day", sa.String(), nullable=False),
        sa.Column("used", sa.Integer(), nullable=False, server_default="0"),
        sa.PrimaryKeyConstraint("provider", "day"),
        sa.CheckConstraint("used >= 0", name="ck_provider_budget_used"),
    )


def downgrade() -> None:
    op.drop_table("provider_budgets")
