"""project keywords

Revision ID: 0002_project_keywords
Revises: 0001_initial
Create Date: 2026-10-13 15:00:00+00:00

Adds ``projects.keywords_json``: the project's keywords (a JSON list of strings, empty for existing
projects), which upweight papers that mention them (SPEC §10).
"""
from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op


# revision identifiers, used by Alembic.
revision: str = '0002_project_keywords'
down_revision: str | None = '0001_initial'
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    with op.batch_alter_table('projects', schema=None) as batch_op:
        batch_op.add_column(sa.Column('keywords_json', sa.JSON(), nullable=False, server_default='[]'))


def downgrade() -> None:
    with op.batch_alter_table('projects', schema=None) as batch_op:
        batch_op.drop_column('keywords_json')
