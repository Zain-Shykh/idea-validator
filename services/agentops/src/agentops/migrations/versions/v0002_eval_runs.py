"""eval_runs (architecture §8.3)

Revision ID: 0002
Revises: 0001
"""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "0002"
down_revision = "0001"
branch_labels = None
depends_on = None

_JSON = sa.JSON().with_variant(postgresql.JSONB(), "postgresql")


def upgrade() -> None:
    op.create_table(
        "eval_runs",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("kind", sa.String(16), nullable=False),
        sa.Column("git_sha", sa.String(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("metrics", _JSON, nullable=False),
        sa.Column("path", sa.String(), nullable=True),
    )
    op.create_index("ix_eval_runs_kind_created_at", "eval_runs", ["kind", "created_at"])


def downgrade() -> None:
    op.drop_index("ix_eval_runs_kind_created_at", table_name="eval_runs")
    op.drop_table("eval_runs")
