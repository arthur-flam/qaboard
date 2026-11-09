"""
What QA-Board tells its users, beyond the emails about their own batches (backend/notifications/):
- the tips emails: a welcome at the first sign-in, then one tip a week (backend/notifications/tips_emails.py).
  UserMessage remembers which ones each user got, by their key: editing or reordering the sequence never sends one twice.
- announcements: banners in the web app, e.g. a scheduled maintenance (backend/api/announcements.py).
"""
import datetime

from sqlalchemy import Column, Integer, String, Text, DateTime, ForeignKey, Boolean, UniqueConstraint, Index
from sqlalchemy.orm import relationship

from backend.models import Base


class UserMessage(Base):
  """A message of a sequence that a user got, or that we decided they won't get"""
  __tablename__ = 'user_messages'
  __table_args__ = (UniqueConstraint('user_id', 'sequence', 'key', name='uq_user_messages_user_sequence_key'),)

  id = Column(Integer, primary_key=True)
  user_id = Column(Integer, ForeignKey('users.id', ondelete='CASCADE'), nullable=False, index=True)
  user = relationship("User")
  # only "tips" for now
  sequence = Column(String(), nullable=False, default='tips', server_default='tips')
  # the message's key, from its file (backend/notifications/tips/*.md)
  key = Column(String(), nullable=False, index=True)
  # sending (claimed by a worker), sent, failed (retried, up to MAX_ATTEMPTS), skipped (never sent: e.g. the welcome
  # for people who already used QA-Board when they were enrolled)
  status = Column(String(), nullable=False)
  attempts = Column(Integer, nullable=False, default=0, server_default='0')
  recipient = Column(String(), nullable=True)
  created_at = Column(DateTime, nullable=False, default=datetime.datetime.utcnow)
  sent_at = Column(DateTime, nullable=True)
  error = Column(Text(), nullable=True)


class Announcement(Base):
  """A banner at the top of the web app, between starts_at and ends_at"""
  __tablename__ = 'announcements'
  __table_args__ = (Index('idx_announcements_ends_at', 'ends_at'),)

  id = Column(Integer, primary_key=True)
  created_at = Column(DateTime, nullable=False, default=datetime.datetime.utcnow)
  updated_at = Column(DateTime, nullable=False, default=datetime.datetime.utcnow)
  created_by_id = Column(Integer, ForeignKey('users.id', ondelete='SET NULL'), nullable=True)
  title = Column(String(), nullable=False)
  # plain text: a sentence or two
  message = Column(Text(), nullable=False, default='', server_default='')
  # primary (news), warning (e.g. a coming maintenance), danger (e.g. an ongoing outage), success
  intent = Column(String(), nullable=False, default='primary', server_default='primary')
  # an optional link, e.g. to the details or a status page
  link_url = Column(String(), nullable=True)
  link_label = Column(String(), nullable=True)
  # when the banner shows. Without ends_at it shows until it's removed
  starts_at = Column(DateTime, nullable=False, default=datetime.datetime.utcnow)
  ends_at = Column(DateTime, nullable=True)
  # whether users can hide it. Announcements of outages usually can't be hidden while they're on
  dismissible = Column(Boolean, nullable=False, default=True, server_default='true')
  # optional: the event it's about, e.g. the maintenance window. Users can add it to their calendar (.ics)
  event_starts_at = Column(DateTime, nullable=True)
  event_ends_at = Column(DateTime, nullable=True)
  # bumped at each edit: the .ics files' SEQUENCE, so calendars update the event instead of adding another
  revision = Column(Integer, nullable=False, default=0, server_default='0')
