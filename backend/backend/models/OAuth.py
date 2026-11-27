"""
QA-Board as an OAuth 2.1 authorization server (backend/oauth.py): the apps admins register (MCP clients like Open
WebUI), what users allowed them, and the codes and refresh tokens of the flow. We only store hashes of the secrets.
"""
from backend import clock

from sqlalchemy import Column, Integer, String, Text, DateTime, ForeignKey
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import relationship

from backend.models import Base


class OAuthClient(Base):
  """An app that may ask users for tokens, registered by an admin, with the exact URIs it may send users back to"""
  __tablename__ = 'oauth_clients'

  id = Column(Integer, primary_key=True)
  # public, e.g. "qab_app_..."
  client_id = Column(String(), nullable=False, unique=True)
  secret_hash = Column(String(64), nullable=False)
  # shown to recognize the secret, e.g. "qab_sec_Xk3f"
  secret_prefix = Column(String(), nullable=False, default='', server_default='')
  name = Column(String(), nullable=False)
  redirect_uris = Column(JSONB(), nullable=False, default=list, server_default='[]')
  created_by_id = Column(Integer, ForeignKey('users.id', ondelete='SET NULL'), nullable=True)
  created_by = relationship("User", foreign_keys=[created_by_id])
  created_at = Column(DateTime, nullable=False, default=clock.utcnow)
  revoked_at = Column(DateTime, nullable=True)
  revoked_by_id = Column(Integer, ForeignKey('users.id', ondelete='SET NULL'), nullable=True)


class OAuthGrant(Base):
  """
  What a user allowed an app: its scopes, for a resource (RFC 8707, e.g. QA-Board's MCP server), with one access
  token (a QA-Board API token of kind "oauth", whose secret changes at each refresh) and a chain of refresh tokens.
  """
  __tablename__ = 'oauth_grants'

  id = Column(Integer, primary_key=True)
  oauth_client_id = Column(Integer, ForeignKey('oauth_clients.id', ondelete='CASCADE'), nullable=False, index=True)
  client = relationship("OAuthClient")
  user_id = Column(Integer, ForeignKey('users.id', ondelete='CASCADE'), nullable=False, index=True)
  user = relationship("User", foreign_keys=[user_id])
  scopes = Column(JSONB(), nullable=False, default=list, server_default='[]')
  resource = Column(Text(), nullable=True)
  token_id = Column(Integer, ForeignKey('tokens.id', ondelete='SET NULL'), nullable=True)
  token = relationship("Token")
  created_at = Column(DateTime, nullable=False, default=clock.utcnow)
  # when the app last got a token: the refresh tokens expire after oauth.refresh_idle_expiry_days without it
  refreshed_at = Column(DateTime, nullable=False, default=clock.utcnow)
  revoked_at = Column(DateTime, nullable=True)
  # e.g. user, admin, client_revoked, code_reused, refresh_token_reused
  revoked_reason = Column(String(), nullable=True)


class OAuthAuthorizationCode(Base):
  """A code users' browsers bring back to the app, single-use and short-lived, bound to the PKCE challenge"""
  __tablename__ = 'oauth_authorization_codes'

  id = Column(Integer, primary_key=True)
  code_hash = Column(String(64), nullable=False, unique=True)
  oauth_client_id = Column(Integer, ForeignKey('oauth_clients.id', ondelete='CASCADE'), nullable=False)
  client = relationship("OAuthClient")
  user_id = Column(Integer, ForeignKey('users.id', ondelete='CASCADE'), nullable=False)
  user = relationship("User", foreign_keys=[user_id])
  redirect_uri = Column(Text(), nullable=False)
  code_challenge = Column(String(), nullable=False)
  scopes = Column(JSONB(), nullable=False, default=list, server_default='[]')
  resource = Column(Text(), nullable=True)
  created_at = Column(DateTime, nullable=False, default=clock.utcnow)
  expires_at = Column(DateTime, nullable=False)
  used_at = Column(DateTime, nullable=True)
  # what it was exchanged for: revoked if the code comes again
  grant_id = Column(Integer, ForeignKey('oauth_grants.id', ondelete='SET NULL'), nullable=True)
  grant = relationship("OAuthGrant")


class OAuthRefreshToken(Base):
  """Refresh tokens rotate: each is used once. A used one that comes again was stolen, its grant is revoked."""
  __tablename__ = 'oauth_refresh_tokens'

  id = Column(Integer, primary_key=True)
  token_hash = Column(String(64), nullable=False, unique=True)
  grant_id = Column(Integer, ForeignKey('oauth_grants.id', ondelete='CASCADE'), nullable=False, index=True)
  grant = relationship("OAuthGrant")
  created_at = Column(DateTime, nullable=False, default=clock.utcnow)
  used_at = Column(DateTime, nullable=True)
