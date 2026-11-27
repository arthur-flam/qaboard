"""OAuth 2.1 authorization server (backend/oauth.py): new tables for the apps admins register, what users allowed
them, and the flow's codes and refresh tokens; tokens.oauth_grant_id links the apps' access tokens to their grant.
Also declared in the models, for databases created by the app. Older versions of QA-Board ignore the tables and the
column; the apps' access tokens are ordinary tokens (kind "oauth") that keep working there until they expire, an
hour at most.

Revision ID: d6f8a0c2e4b7
Revises: c4e6a8b0d2f3
Create Date: 2026-11-27 10:00:00.000000

"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


# revision identifiers, used by Alembic.
revision = 'd6f8a0c2e4b7'
down_revision = 'c4e6a8b0d2f3'
branch_labels = None
depends_on = None


def has_table(table):
  return sa.inspect(op.get_bind()).has_table(table)


def has_column(table, column):
  return any(c['name'] == column for c in sa.inspect(op.get_bind()).get_columns(table))


def upgrade():
  if not has_table('oauth_clients'):
    op.create_table(
      'oauth_clients',
      sa.Column('id', sa.Integer(), primary_key=True),
      sa.Column('client_id', sa.String(), nullable=False, unique=True),
      sa.Column('secret_hash', sa.String(64), nullable=False),
      sa.Column('secret_prefix', sa.String(), nullable=False, server_default=''),
      sa.Column('name', sa.String(), nullable=False),
      sa.Column('redirect_uris', postgresql.JSONB(), nullable=False, server_default='[]'),
      sa.Column('created_by_id', sa.Integer(), sa.ForeignKey('users.id', ondelete='SET NULL'), nullable=True),
      sa.Column('created_at', sa.DateTime(), nullable=False),
      sa.Column('revoked_at', sa.DateTime(), nullable=True),
      sa.Column('revoked_by_id', sa.Integer(), sa.ForeignKey('users.id', ondelete='SET NULL'), nullable=True),
    )
  if not has_table('oauth_grants'):
    op.create_table(
      'oauth_grants',
      sa.Column('id', sa.Integer(), primary_key=True),
      sa.Column('oauth_client_id', sa.Integer(), sa.ForeignKey('oauth_clients.id', ondelete='CASCADE'), nullable=False, index=True),
      sa.Column('user_id', sa.Integer(), sa.ForeignKey('users.id', ondelete='CASCADE'), nullable=False, index=True),
      sa.Column('scopes', postgresql.JSONB(), nullable=False, server_default='[]'),
      sa.Column('resource', sa.Text(), nullable=True),
      sa.Column('created_at', sa.DateTime(), nullable=False),
      sa.Column('refreshed_at', sa.DateTime(), nullable=False),
      sa.Column('revoked_at', sa.DateTime(), nullable=True),
      sa.Column('revoked_reason', sa.String(), nullable=True),
    )
  if not has_table('oauth_authorization_codes'):
    op.create_table(
      'oauth_authorization_codes',
      sa.Column('id', sa.Integer(), primary_key=True),
      sa.Column('code_hash', sa.String(64), nullable=False, unique=True),
      sa.Column('oauth_client_id', sa.Integer(), sa.ForeignKey('oauth_clients.id', ondelete='CASCADE'), nullable=False, index=True),
      sa.Column('user_id', sa.Integer(), sa.ForeignKey('users.id', ondelete='CASCADE'), nullable=False, index=True),
      sa.Column('redirect_uri', sa.Text(), nullable=False),
      sa.Column('code_challenge', sa.String(), nullable=False),
      sa.Column('scopes', postgresql.JSONB(), nullable=False, server_default='[]'),
      sa.Column('resource', sa.Text(), nullable=True),
      sa.Column('created_at', sa.DateTime(), nullable=False),
      sa.Column('expires_at', sa.DateTime(), nullable=False),
      sa.Column('used_at', sa.DateTime(), nullable=True),
      sa.Column('grant_id', sa.Integer(), sa.ForeignKey('oauth_grants.id', ondelete='SET NULL'), nullable=True, index=True),
    )
  if not has_table('oauth_refresh_tokens'):
    op.create_table(
      'oauth_refresh_tokens',
      sa.Column('id', sa.Integer(), primary_key=True),
      sa.Column('token_hash', sa.String(64), nullable=False, unique=True),
      sa.Column('grant_id', sa.Integer(), sa.ForeignKey('oauth_grants.id', ondelete='CASCADE'), nullable=False, index=True),
      sa.Column('created_at', sa.DateTime(), nullable=False),
      sa.Column('used_at', sa.DateTime(), nullable=True),
    )
  if not has_column('tokens', 'oauth_grant_id'):
    op.add_column('tokens', sa.Column('oauth_grant_id', sa.Integer(), sa.ForeignKey('oauth_grants.id', ondelete='CASCADE'), nullable=True))
    op.create_index('ix_tokens_oauth_grant_id', 'tokens', ['oauth_grant_id'])


def downgrade():
  op.drop_index('ix_tokens_oauth_grant_id', table_name='tokens')
  op.drop_column('tokens', 'oauth_grant_id')
  op.drop_table('oauth_refresh_tokens')
  op.drop_table('oauth_authorization_codes')
  op.drop_table('oauth_grants')
  op.drop_table('oauth_clients')
