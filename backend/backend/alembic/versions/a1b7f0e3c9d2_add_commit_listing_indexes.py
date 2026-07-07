"""Add composite indexes to speed up the paginated commit listing

Revision ID: a1b7f0e3c9d2
Revises: 44c55bb36f57
Create Date: 2026-07-07

The commit list is always filtered by project and sorted by authored date,
often with an extra filter on the branch. Composite indexes let postgres
serve those queries without bitmap-anding the single-column indexes.
"""
from alembic import op


# revision identifiers, used by Alembic.
revision = 'a1b7f0e3c9d2'
down_revision = '44c55bb36f57'
branch_labels = None
depends_on = None


def upgrade():
    op.create_index(
        'idx_ci_commits_project_authored',
        'ci_commits',
        ['project_id', 'authored_datetime'],
    )
    op.create_index(
        'idx_ci_commits_project_branch_authored',
        'ci_commits',
        ['project_id', 'branch', 'authored_datetime'],
    )


def downgrade():
    op.drop_index('idx_ci_commits_project_authored', table_name='ci_commits')
    op.drop_index('idx_ci_commits_project_branch_authored', table_name='ci_commits')
