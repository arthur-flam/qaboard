"""Batches with runs started from code (qaboard.start_run()) say so, in data.sdk, and their projects in
data.has_runs_from_code: those get the Runs page in the sidebar. The server marks them when it gets such runs
(backend/runs_list.py mark_runs_from_code). Here, those that already have some: start_run() shipped in November 2026,
we only read the runs created since October (outputs.created_date is indexed), not the whole table.

Revision ID: c4e6a8b0d2f3
Revises: e3a5c7d9f1b2
Create Date: 2026-11-17 10:00:00.000000

"""
from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = 'c4e6a8b0d2f3'
down_revision = 'e3a5c7d9f1b2'
branch_labels = None
depends_on = None


def upgrade():
  op.execute(sa.text("""
    UPDATE batches SET data = coalesce(data, '{}'::jsonb) || '{"sdk": true}'::jsonb
    WHERE id IN (
      SELECT DISTINCT batch_id FROM outputs
      WHERE created_date >= '2026-10-01' AND (data->>'sdk') IS NOT NULL
    )
    AND NOT (coalesce(data, '{}'::jsonb) ? 'sdk')
  """))
  op.execute(sa.text("""
    UPDATE projects SET data = coalesce(data, '{}'::jsonb) || '{"has_runs_from_code": true}'::jsonb
    WHERE id IN (
      SELECT DISTINCT ci_commits.project_id FROM batches JOIN ci_commits ON ci_commits.id = batches.ci_commit_id
      WHERE batches.data ? 'sdk' AND batches.latest_output_datetime >= '2026-10-01'
    )
    AND NOT (coalesce(data, '{}'::jsonb) ? 'has_runs_from_code')
  """))


def downgrade():
  # the marks are harmless: older versions ignore them
  pass
