"""Allow multiple provenance records for one movie trope."""

import sqlalchemy as sa
from alembic import op

revision = "f7a8b9c0d1e2"
down_revision = "e6f7a8b9c0d1"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("cached_movies", sa.Column("tvtropes_work_url", sa.String(), nullable=True))
    op.create_table(
        "movie_facets_new",
        sa.Column("facet_id", sa.String(), nullable=False),
        sa.Column("value_text", sa.String(), nullable=False),
        sa.Column("value_num", sa.Float(), nullable=False),
        sa.Column("movie_id", sa.Integer(), nullable=False),
        sa.Column("source", sa.String(), nullable=False),
        sa.Column("confidence", sa.Float(), nullable=True),
        sa.Column("source_url", sa.String(), nullable=True),
        sa.ForeignKeyConstraint(["movie_id"], ["cached_movies.tmdb_id"]),
        sa.PrimaryKeyConstraint("facet_id", "value_text", "value_num", "movie_id", "source"),
        sqlite_with_rowid=False,
    )
    op.execute(
        sa.text(
            "INSERT INTO movie_facets_new "
            "(facet_id, value_text, value_num, movie_id, source, confidence) "
            "SELECT facet_id, value_text, value_num, movie_id, source, confidence FROM movie_facets"
        )
    )
    op.drop_index("ix_movie_facets_movie_family", table_name="movie_facets")
    op.drop_table("movie_facets")
    op.rename_table("movie_facets_new", "movie_facets")
    op.create_index("ix_movie_facets_movie_family", "movie_facets", ["movie_id", "facet_id"])


def downgrade() -> None:
    op.create_table(
        "movie_facets_old",
        sa.Column("facet_id", sa.String(), nullable=False),
        sa.Column("value_text", sa.String(), nullable=False),
        sa.Column("value_num", sa.Float(), nullable=False),
        sa.Column("movie_id", sa.Integer(), nullable=False),
        sa.Column("source", sa.String(), nullable=False),
        sa.Column("confidence", sa.Float(), nullable=False),
        sa.ForeignKeyConstraint(["movie_id"], ["cached_movies.tmdb_id"]),
        sa.PrimaryKeyConstraint("facet_id", "value_text", "value_num", "movie_id"),
        sqlite_with_rowid=False,
    )
    op.execute(
        sa.text(
            "INSERT OR REPLACE INTO movie_facets_old "
            "(facet_id, value_text, value_num, movie_id, source, confidence) "
            "SELECT facet_id, value_text, value_num, movie_id, source, COALESCE(confidence, 0) "
            "FROM movie_facets"
        )
    )
    op.drop_index("ix_movie_facets_movie_family", table_name="movie_facets")
    op.drop_table("movie_facets")
    op.rename_table("movie_facets_old", "movie_facets")
    op.create_index("ix_movie_facets_movie_family", "movie_facets", ["movie_id", "facet_id"])
    op.drop_column("cached_movies", "tvtropes_work_url")
