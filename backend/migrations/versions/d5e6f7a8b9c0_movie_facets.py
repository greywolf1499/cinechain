"""Typed, indexed facets and their raw production inputs."""

import sqlalchemy as sa
from alembic import op

revision = "d5e6f7a8b9c0"
down_revision = "c4d5e6f7a8b9"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "movie_facets",
        sa.Column("facet_id", sa.String(), nullable=False),
        sa.Column("value_text", sa.String(), nullable=False),
        sa.Column("value_num", sa.Float(), nullable=False),
        sa.Column("movie_id", sa.Integer(), sa.ForeignKey("cached_movies.tmdb_id"), nullable=False),
        sa.Column("source", sa.String(), nullable=False),
        sa.Column("confidence", sa.Float(), nullable=False),
        sa.PrimaryKeyConstraint("facet_id", "value_text", "value_num", "movie_id"),
        sqlite_with_rowid=False,
    )
    op.create_index("ix_movie_facets_movie_family", "movie_facets", ["movie_id", "facet_id"])
    op.create_table(
        "movie_facet_status",
        sa.Column("movie_id", sa.Integer(), sa.ForeignKey("cached_movies.tmdb_id"), nullable=False),
        sa.Column("family", sa.String(), nullable=False),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.Column("status", sa.String(), nullable=False),
        sa.Column("computed_at", sa.DateTime(), nullable=False),
        sa.PrimaryKeyConstraint("movie_id", "family"),
        sqlite_with_rowid=False,
    )
    for name in ("budget", "revenue", "collection_id"):
        op.add_column("cached_movies", sa.Column(name, sa.Integer()))
    for table in ("cached_actors", "cached_directors"):
        op.add_column(table, sa.Column("deathday", sa.String()))


def downgrade() -> None:
    op.drop_table("movie_facet_status")
    op.drop_table("movie_facets")
    for table in ("cached_actors", "cached_directors"):
        with op.batch_alter_table(table) as batch:
            batch.drop_column("deathday")
    with op.batch_alter_table("cached_movies") as batch:
        for name in ("budget", "revenue", "collection_id"):
            batch.drop_column(name)
