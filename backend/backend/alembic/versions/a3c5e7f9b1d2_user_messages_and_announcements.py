"""Tips emails and announcements (backend/models/Communication.py)

- users.tips_started_at: when a user started getting the tips emails
- user_messages: the tips each user got, by key, so that editing the sequence never sends one twice
- announcements: banners in the web app, e.g. scheduled maintenance

Revision ID: a3c5e7f9b1d2
Revises: d9f1b3c5e7a4
Create Date: 2026-11-09 10:00:00.000000

"""
from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = 'a3c5e7f9b1d2'
down_revision = 'd9f1b3c5e7a4'
branch_labels = None
depends_on = None


def has_table(table):
  return sa.inspect(op.get_bind()).has_table(table)


def has_column(table, column):
  return any(c['name'] == column for c in sa.inspect(op.get_bind()).get_columns(table))


def upgrade():
  if not has_column('users', 'tips_started_at'):
    op.add_column('users', sa.Column('tips_started_at', sa.DateTime(), nullable=True))
  if not has_table('user_messages'):
    op.create_table(
      'user_messages',
      sa.Column('id', sa.Integer(), primary_key=True),
      sa.Column('user_id', sa.Integer(), sa.ForeignKey('users.id', ondelete='CASCADE'), nullable=False, index=True),
      sa.Column('sequence', sa.String(), nullable=False, server_default='tips'),
      sa.Column('key', sa.String(), nullable=False, index=True),
      sa.Column('status', sa.String(), nullable=False),
      sa.Column('attempts', sa.Integer(), nullable=False, server_default='0'),
      sa.Column('recipient', sa.String(), nullable=True),
      sa.Column('created_at', sa.DateTime(), nullable=False, server_default=sa.func.now()),
      sa.Column('sent_at', sa.DateTime(), nullable=True),
      sa.Column('error', sa.Text(), nullable=True),
      sa.UniqueConstraint('user_id', 'sequence', 'key', name='uq_user_messages_user_sequence_key'),
    )
  if not has_table('announcements'):
    op.create_table(
      'announcements',
      sa.Column('id', sa.Integer(), primary_key=True),
      sa.Column('created_at', sa.DateTime(), nullable=False, server_default=sa.func.now()),
      sa.Column('updated_at', sa.DateTime(), nullable=False, server_default=sa.func.now()),
      sa.Column('created_by_id', sa.Integer(), sa.ForeignKey('users.id', ondelete='SET NULL'), nullable=True),
      sa.Column('title', sa.String(), nullable=False),
      sa.Column('message', sa.Text(), nullable=False, server_default=''),
      sa.Column('intent', sa.String(), nullable=False, server_default='primary'),
      sa.Column('link_url', sa.String(), nullable=True),
      sa.Column('link_label', sa.String(), nullable=True),
      sa.Column('starts_at', sa.DateTime(), nullable=False, server_default=sa.func.now()),
      sa.Column('ends_at', sa.DateTime(), nullable=True),
      sa.Column('dismissible', sa.Boolean(), nullable=False, server_default='true'),
      sa.Column('event_starts_at', sa.DateTime(), nullable=True),
      sa.Column('event_ends_at', sa.DateTime(), nullable=True),
      sa.Column('revision', sa.Integer(), nullable=False, server_default='0'),
    )
    op.create_index('idx_announcements_ends_at', 'announcements', ['ends_at'])


def downgrade():
  op.drop_table('announcements')
  op.drop_table('user_messages')
  op.drop_column('users', 'tips_started_at')
