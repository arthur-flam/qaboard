"""Namespaces, repository links and the projects' lifecycle (docs/design/user-management.md, Appendix G)

- `namespaces`: one per first segment of the existing projects' ids, as organizations flagged `legacy`, without
  members: the grants on these prefixes keep working as before, and nobody gets a new right.
- `projects`: + namespace_id (filled), created_at, created_by_id, created_via, archived_at, archived_by_id (empty).
- `project_repos`: the repository of each project with 2 segments or more, from data.git (GitLab's
  path_with_namespace, the host the git hosts' migration found) or else its id: `group/repo/sub` is `sub` in
  `group/repo`. Webhooks find projects through them.

No setting is written: the defaults of projects.create and namespaces.create are today's behaviour (any project can
be created). New servers get the recommended ones from their setup page (backend/api/setup.py).
Also declared in the models, for databases created by the app. Older versions ignore the tables and columns.

Revision ID: e2f4a6c8b0d1
Revises: d6f8a0c2e4b7
Create Date: 2026-11-28 10:00:00.000000
"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


# revision identifiers, used by Alembic.
revision = 'e2f4a6c8b0d1'
down_revision = 'd6f8a0c2e4b7'
branch_labels = None
depends_on = None


def has_table(table):
  return sa.inspect(op.get_bind()).has_table(table)


def has_column(table, column):
  return any(c['name'] == column for c in sa.inspect(op.get_bind()).get_columns(table))


def has_index(table, name):
  return any(i['name'] == name for i in sa.inspect(op.get_bind()).get_indexes(table))


def upgrade():
  if not has_table('namespaces'):
    op.create_table(
      'namespaces',
      sa.Column('id', sa.Integer(), primary_key=True),
      sa.Column('path', sa.String(), nullable=False, unique=True),
      sa.Column('kind', sa.String(), nullable=False, server_default='org'),
      sa.Column('display_name', sa.String(), nullable=True),
      sa.Column('user_id', sa.Integer(), sa.ForeignKey('users.id', ondelete='SET NULL'), nullable=True),
      sa.Column('created_by_id', sa.Integer(), sa.ForeignKey('users.id', ondelete='SET NULL'), nullable=True),
      sa.Column('created_at', sa.DateTime(), nullable=False),
      sa.Column('legacy', sa.Boolean(), nullable=False, server_default='false'),
      sa.Column('data', postgresql.JSONB(), nullable=False, server_default='{}'),
    )
  for name, column in [
    ('namespace_id', sa.Column('namespace_id', sa.Integer(), sa.ForeignKey('namespaces.id', ondelete='SET NULL'), nullable=True)),
    ('created_at', sa.Column('created_at', sa.DateTime(), nullable=True)),
    ('created_by_id', sa.Column('created_by_id', sa.Integer(), sa.ForeignKey('users.id', ondelete='SET NULL'), nullable=True)),
    ('created_via', sa.Column('created_via', sa.String(), nullable=True)),
    ('archived_at', sa.Column('archived_at', sa.DateTime(), nullable=True)),
    ('archived_by_id', sa.Column('archived_by_id', sa.Integer(), sa.ForeignKey('users.id', ondelete='SET NULL'), nullable=True)),
  ]:
    if not has_column('projects', name):
      op.add_column('projects', column)
  if not has_index('projects', 'ix_projects_namespace_id'):
    op.create_index('ix_projects_namespace_id', 'projects', ['namespace_id'])
  if not has_table('project_repos'):
    op.create_table(
      'project_repos',
      sa.Column('id', sa.Integer(), primary_key=True),
      sa.Column('project_id', sa.String(), sa.ForeignKey('projects.id', ondelete='CASCADE'), nullable=False, index=True),
      sa.Column('git_host', sa.String(), nullable=False, server_default=''),
      sa.Column('repo_path', sa.String(), nullable=False, index=True),
      sa.Column('subdir', sa.String(), nullable=False, server_default=''),
      sa.Column('created_at', sa.DateTime(), nullable=False),
      sa.UniqueConstraint('project_id', 'git_host', 'repo_path', name='uq_project_repos_project_repo'),
    )

  connection = op.get_bind()
  # one legacy organization per first segment, then each project's
  connection.execute(sa.text("""
    INSERT INTO namespaces (path, kind, created_at, legacy, data)
    SELECT DISTINCT split_part(trim(both '/' from id), '/', 1), 'org', now() at time zone 'utc', true, '{}'::jsonb
    FROM projects
    WHERE split_part(trim(both '/' from id), '/', 1) <> ''
    ON CONFLICT (path) DO NOTHING
  """))
  connection.execute(sa.text("""
    UPDATE projects SET namespace_id = namespaces.id
    FROM namespaces
    WHERE projects.namespace_id IS NULL AND namespaces.path = split_part(trim(both '/' from projects.id), '/', 1)
  """))
  # repository links: data.git's path_with_namespace when it's a prefix of the id (sub-projects: the rest is their folder)
  connection.execute(sa.text("""
    INSERT INTO project_repos (project_id, git_host, repo_path, subdir, created_at)
    SELECT id, git_host, repo_path, trim(both '/' from substr(id, length(repo_path) + 1)), now() at time zone 'utc'
    FROM (
      SELECT id,
             coalesce(lower(data->'git'->>'host'), '') AS git_host,
             CASE WHEN data->'git'->>'path_with_namespace' IS NOT NULL
                       AND (id = data->'git'->>'path_with_namespace'
                            OR left(id, length(data->'git'->>'path_with_namespace') + 1) = (data->'git'->>'path_with_namespace') || '/')
                  THEN data->'git'->>'path_with_namespace'
                  ELSE split_part(id, '/', 1) || '/' || split_part(id, '/', 2) END AS repo_path
      FROM projects
      WHERE split_part(id, '/', 2) <> ''
    ) AS repos
    WHERE NOT EXISTS (SELECT 1 FROM project_repos r WHERE r.project_id = repos.id)
  """))


def downgrade():
  op.drop_table('project_repos')
  for name in ('archived_by_id', 'archived_at', 'created_via', 'created_by_id', 'created_at'):
    op.drop_column('projects', name)
  op.drop_index('ix_projects_namespace_id', table_name='projects')
  op.drop_column('projects', 'namespace_id')
  op.drop_table('namespaces')
