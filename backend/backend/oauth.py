"""
QA-Board as an OAuth 2.1 authorization server: MCP clients like Open WebUI get read-only tokens of each user, to call
QA-Board's MCP server (`qa mcp --http`, at /mcp) and the API. Endpoints: backend/api/oauth.py. Admins' docs:
website/docs/backend-admin/mcp-clients.mdx.

- Admins register the apps (OAuthClient): a name, a secret shown once, the exact URIs users are sent back to. Dynamic
  client registration (RFC 7591) would add clients without a creator, with the same columns.
- The authorization code flow, with PKCE (S256, required) and the `resource` the app asks for (RFC 8707, recorded).
  Users allow the app on the web app's consent page (/oauth/consent), signed in.
- Access tokens are ordinary API tokens (kind "oauth", the "read" scope, an hour): the whole API accepts them, and
  their `data` says which app and grant they are for. One per grant: each refresh gives it a new secret (tokens.renew).
- Refresh tokens rotate. One that comes twice was stolen, like a code that comes twice: the grant is revoked.
- Revoking the grant (Settings → Connected apps), its token (Admin → Tokens) or the app's registration (Admin →
  Connected apps) stops both its access token and its refresh tokens.
"""
import re
import hmac
import base64
import hashlib
import secrets
import datetime
from dataclasses import dataclass
from typing import List, Optional
from urllib.parse import urlsplit, urlencode, unquote_plus

from . import tokens as tokens_lib


CLIENT_ID_PREFIX = 'qab_app_'
CLIENT_SECRET_PREFIX = 'qab_sec_'
REFRESH_TOKEN_PREFIX = 'qab_rft_'
CLIENT_ID_LENGTH = 24
# What apps can ask for, as the consent page says it. Only reading for now.
SCOPES = {'read': "Read your projects and results"}
CODE_LIFETIME = datetime.timedelta(minutes=2)
ACCESS_TOKEN_LIFETIME = datetime.timedelta(hours=1)
MAX_NAME_LENGTH = 100
MAX_REDIRECT_URIS = 10
# RFC 7636 §4.1, and the base64url of a SHA-256 without padding (§4.2)
CODE_VERIFIER_RE = re.compile(r'^[A-Za-z0-9._~-]{43,128}$')
CODE_CHALLENGE_RE = re.compile(r'^[A-Za-z0-9_-]{43}$')
# Each may only be sent once (RFC 6749 §3.1)
SINGLE_PARAMETERS = ('response_type', 'code_challenge', 'code_challenge_method', 'scope', 'state')


class OAuthError(Exception):
  """An OAuth error answer: of the token and revocation endpoints (RFC 6749 §5.2), or sent back to the app (§4.1.2.1)"""
  def __init__(self, error: str, description: str, status: int = 400, basic: bool = False):
    super().__init__(description)
    self.error, self.description, self.status = error, description, status
    # the app authenticated with HTTP Basic: the 401 says how to (WWW-Authenticate)
    self.basic = basic


class ClientError(OAuthError):
  """An unknown app or redirect_uri: nobody to send the error back to, users see it (RFC 6749 §4.1.2.1)"""


class RedirectError(OAuthError):
  """An error the browser brings back to the app, at its redirect_uri"""
  def __init__(self, error: str, description: str, redirect_uri: str, state: Optional[str]):
    super().__init__(error, description)
    self.redirect_uri, self.state = redirect_uri, state

  def url(self, issuer: str) -> str:
    return redirect_url(self.redirect_uri, error=self.error, error_description=self.description, state=self.state, iss=issuer)


def utcnow() -> datetime.datetime:
  return tokens_lib.utcnow()


def metadata(issuer: str) -> dict:
  """The authorization server's metadata (RFC 8414). `issuer`: QA-Board's public URL"""
  endpoints = f"{issuer}/api/v1/oauth"
  auth_methods = ["client_secret_basic", "client_secret_post"]
  return {
    "issuer": issuer,
    "authorization_endpoint": f"{endpoints}/authorize",
    "token_endpoint": f"{endpoints}/token",
    "revocation_endpoint": f"{endpoints}/revoke",
    "response_types_supported": ["code"],
    "grant_types_supported": ["authorization_code", "refresh_token"],
    "code_challenge_methods_supported": ["S256"],
    "token_endpoint_auth_methods_supported": auth_methods,
    "revocation_endpoint_auth_methods_supported": auth_methods,
    "scopes_supported": list(SCOPES),
    # RFC 9207: the redirects say who sends them, against mix-up attacks
    "authorization_response_iss_parameter_supported": True,
  }


# ==========================================
# Apps, registered by admins
# ==========================================

def clean_name(name) -> str:
  if not isinstance(name, str) or not name.strip():
    raise ValueError("Name the app, e.g. \"Open WebUI\": users see it when they allow it.")
  if len(name.strip()) > MAX_NAME_LENGTH:
    raise ValueError(f"App names have at most {MAX_NAME_LENGTH} characters.")
  return name.strip()


def redirect_uri_problem(uri: str) -> Optional[str]:
  """Why users can't be sent back to this URI, or None. Compared exactly (OAuth 2.1): no patterns"""
  parts = urlsplit(uri)
  if parts.scheme not in ('https', 'http') or not parts.hostname:
    return f"{uri} isn't an http(s) URL."
  if '#' in uri:
    return f"{uri} has a fragment (#...): redirect URIs can't."
  if parts.username or parts.password:
    return f"{uri} has credentials: redirect URIs can't."
  return None


def clean_redirect_uris(uris) -> List[str]:
  if isinstance(uris, str):
    uris = uris.split()
  if not isinstance(uris, list) or not uris or not all(isinstance(u, str) for u in uris):
    raise ValueError("Give the URIs where the app receives users back, e.g. https://openwebui.example.com/oauth/clients/mcp:qaboard/callback.")
  cleaned = []
  for uri in (u.strip() for u in uris):
    if uri and uri not in cleaned:
      if problem := redirect_uri_problem(uri):
        raise ValueError(problem)
      cleaned.append(uri)
  if not cleaned or len(cleaned) > MAX_REDIRECT_URIS:
    raise ValueError(f"Give between 1 and {MAX_REDIRECT_URIS} redirect URIs.")
  return cleaned


def register_client(name: str, redirect_uris: List[str], created_by):
  """Registers an app, added to the database session (the caller commits) and audited. Returns (client, secret)."""
  from backend import db_session
  from .models import OAuthClient
  from .audit import audit
  secret = tokens_lib.generate(CLIENT_SECRET_PREFIX)
  client = OAuthClient(
    client_id=CLIENT_ID_PREFIX + ''.join(secrets.choice(tokens_lib.BASE62) for _ in range(CLIENT_ID_LENGTH)),
    secret_hash=tokens_lib.hash_token(secret),
    secret_prefix=tokens_lib.display_prefix(secret),
    name=name,
    redirect_uris=redirect_uris,
    created_by_id=created_by.id,
    created_at=utcnow(),
  )
  db_session.add(client)
  db_session.flush()
  audit('oauth_client.create', 'oauth_client', client.id, name, meta={"client_id": client.client_id, "redirect_uris": redirect_uris})
  return client, secret


def update_client(client, changes: dict):
  """Renames the app or changes its redirect URIs. Audited, the caller commits"""
  from .audit import audit
  changes = {key: value for key, value in changes.items() if getattr(client, key) != value}
  if not changes:
    return
  audit('oauth_client.update', 'oauth_client', client.id, client.name, changes={key: {"from": getattr(client, key), "to": value} for key, value in changes.items()})
  for key, value in changes.items():
    setattr(client, key, value)


def revoke_client(client, by=None) -> bool:
  """The app can't get tokens anymore, and those it got stop working. Audited, the caller commits"""
  from .models import OAuthGrant
  from .audit import audit
  if client.revoked_at is not None:
    return False
  client.revoked_at, client.revoked_by_id = utcnow(), by.id if by is not None else None
  audit('oauth_client.revoke', 'oauth_client', client.id, client.name, meta={"client_id": client.client_id})
  for grant in OAuthGrant.query.filter(OAuthGrant.oauth_client_id == client.id, OAuthGrant.revoked_at.is_(None)).all():
    revoke_grant(grant, reason='client_revoked', by=by)
  return True


def find_client(client_id):
  """The registered app, unless its registration was revoked"""
  from .models import OAuthClient
  if not isinstance(client_id, str) or not client_id:
    return None
  return OAuthClient.query.filter(OAuthClient.client_id == client_id, OAuthClient.revoked_at.is_(None)).one_or_none()


def client_credentials(authorization: Optional[str], form) -> tuple:
  """(client_id, secret, whether with HTTP Basic) from client_secret_basic or client_secret_post (RFC 6749 §2.3.1)"""
  if not (authorization or '').lower().startswith('basic '):
    return form.get('client_id'), form.get('client_secret'), False
  try:
    client_id, separator, secret = base64.b64decode(authorization[6:].strip(), validate=True).decode().partition(':')
  except ValueError:
    raise OAuthError('invalid_client', "The Authorization header isn't valid HTTP Basic credentials.", 401, basic=True)
  if not separator:
    raise OAuthError('invalid_client', "The Authorization header has no client secret.", 401, basic=True)
  if form.get('client_secret'):
    raise OAuthError('invalid_request', "Send the client's credentials one way: in the Authorization header, or in the form.")
  client_id, secret = unquote_plus(client_id), unquote_plus(secret)
  # some apps also send their id in the form
  if form.get('client_id') not in (None, client_id):
    raise OAuthError('invalid_client', "The client_id of the form isn't the one of the Authorization header.", 401, basic=True)
  return client_id, secret, True


def authenticate_client(authorization: Optional[str], form):
  """The app calling the token or revocation endpoint, authenticated by its secret. Raises OAuthError."""
  client_id, secret, basic = client_credentials(authorization, form)
  client = find_client(client_id) if secret else None
  if client is None or not hmac.compare_digest(client.secret_hash, tokens_lib.hash_token(secret)):
    raise OAuthError('invalid_client', "Unknown app, wrong secret, or the app's registration was revoked.", 401, basic=basic)
  return client


# ==========================================
# Authorization requests, and users' answers
# ==========================================

@dataclass
class AuthorizationRequest:
  client: object
  redirect_uri: str
  scopes: List[str]
  code_challenge: str
  state: Optional[str]
  resource: Optional[str]


def redirect_url(redirect_uri: str, **params) -> str:
  """The redirect URI, with the parameters added to its query (registered URIs have no fragment)"""
  query = urlencode({k: v for k, v in params.items() if v is not None})
  return f"{redirect_uri}{'&' if urlsplit(redirect_uri).query else '?'}{query}"


def parse_scope(value: Optional[str]) -> List[str]:
  """
  The scopes granted for those asked (RFC 6749 §3.3), "read" when none are. Others are ignored (e.g. offline_access:
  apps get refresh tokens anyway), unless that leaves nothing.
  """
  asked = (value or '').split()
  granted = [s for s in SCOPES if s in asked] if asked else ['read']
  if not granted:
    raise OAuthError('invalid_scope', f"Apps can ask for: {', '.join(SCOPES)}.")
  return granted


def clean_resource(values: List[str]) -> Optional[str]:
  """The resource the app asks a token for (RFC 8707), e.g. QA-Board's MCP server: recorded, the token works on the whole API"""
  if not values:
    return None
  if len(values) > 1:
    raise OAuthError('invalid_target', "Ask for one resource at a time.")
  parts = urlsplit(values[0])
  if not parts.scheme or not parts.netloc or '#' in values[0]:
    raise OAuthError('invalid_target', "The resource must be an absolute URI without a fragment, e.g. https://qaboard.example.com/mcp.")
  return values[0]


def authorization_request(params) -> AuthorizationRequest:
  """
  Checks the parameters of an authorization request (RFC 6749 §4.1.1, PKCE: RFC 7636, resource: RFC 8707), a
  MultiDict. Raises ClientError for an unknown app or redirect_uri, else RedirectError.
  """
  client_ids, redirect_uris = params.getlist('client_id'), params.getlist('redirect_uri')
  client = find_client(client_ids[0]) if len(client_ids) == 1 else None
  if client is None:
    raise ClientError('invalid_client', "This app isn't registered in QA-Board, or its registration was revoked: tell a QA-Board admin.")
  if len(redirect_uris) != 1 or redirect_uris[0] not in (client.redirect_uris or []):
    raise ClientError('invalid_request', f"The redirect_uri isn't one of those registered for {client.name}: a QA-Board admin can add it in Admin → Connected apps.")
  redirect_uri = redirect_uris[0]
  state = params.get('state')

  def check(condition, error, description):
    if not condition:
      raise RedirectError(error, description, redirect_uri, state)

  check(all(len(params.getlist(name)) <= 1 for name in SINGLE_PARAMETERS), 'invalid_request', "Send each parameter once.")
  check(params.get('response_type') == 'code', 'unsupported_response_type', "QA-Board only supports response_type=code.")
  check(params.get('code_challenge'), 'invalid_request', "PKCE is required: send a code_challenge, with code_challenge_method=S256.")
  check(params.get('code_challenge_method') == 'S256', 'invalid_request', "Only the S256 code_challenge_method is supported.")
  check(CODE_CHALLENGE_RE.match(params['code_challenge']), 'invalid_request', "The code_challenge must be the base64url of a SHA-256: 43 characters.")
  try:
    scopes, resource = parse_scope(params.get('scope')), clean_resource(params.getlist('resource'))
  except OAuthError as e:
    raise RedirectError(e.error, e.description, redirect_uri, state)
  return AuthorizationRequest(client, redirect_uri, scopes, params['code_challenge'], state, resource)


def serialize_request(req: AuthorizationRequest) -> dict:
  """What the consent page shows"""
  return {
    "client": {"id": req.client.id, "name": req.client.name, "client_id": req.client.client_id},
    "redirect_uri": req.redirect_uri,
    "redirect_host": urlsplit(req.redirect_uri).netloc,
    "scopes": [{"id": s, "description": SCOPES[s]} for s in req.scopes],
    "resource": req.resource,
  }


def answer(req: AuthorizationRequest, user, approve: bool, issuer: str) -> str:
  """Where the browser goes with the user's answer: back to the app with a code, or with access_denied. Audited."""
  from backend import db_session
  from .models import OAuthAuthorizationCode
  from .audit import audit
  meta = {"client_id": req.client.client_id, "scopes": req.scopes, "resource": req.resource, "redirect_uri": req.redirect_uri}
  audit('oauth.approve' if approve else 'oauth.deny', 'oauth_client', req.client.id, req.client.name, meta=meta)
  if not approve:
    return redirect_url(req.redirect_uri, error='access_denied', error_description="The user didn't allow the app.", state=req.state, iss=issuer)
  code = secrets.token_urlsafe(32)
  now = utcnow()
  db_session.add(OAuthAuthorizationCode(
    code_hash=tokens_lib.hash_token(code), oauth_client_id=req.client.id, user_id=user.id, redirect_uri=req.redirect_uri,
    code_challenge=req.code_challenge, scopes=req.scopes, resource=req.resource, created_at=now, expires_at=now + CODE_LIFETIME,
  ))
  return redirect_url(req.redirect_uri, code=code, state=req.state, iss=issuer)


# ==========================================
# Tokens
# ==========================================

def pkce_matches(verifier, challenge: str) -> bool:
  """RFC 7636 §4.6: base64url(sha256(code_verifier)) is the code_challenge"""
  if not isinstance(verifier, str) or not CODE_VERIFIER_RE.match(verifier):
    return False
  computed = base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest()).rstrip(b'=').decode()
  return hmac.compare_digest(computed, challenge)


def check_resource(form, resource: Optional[str]):
  """The token request may repeat the grant's resource, not ask for another"""
  asked = form.getlist('resource')
  if asked and asked != [resource]:
    raise OAuthError('invalid_target', f"This grant is for {resource or 'no resource'}: ask the user again for another.")


def check_user(user):
  if user is None or user.status != 'active':
    raise OAuthError('invalid_grant', "The user's account can't use QA-Board anymore.")


def token(client, form) -> dict:
  """The token endpoint, for an authenticated app (RFC 6749 §3.2). The caller rolls back on OAuthError."""
  grant_type = form.get('grant_type')
  if grant_type == 'authorization_code':
    return exchange_code(client, form)
  if grant_type == 'refresh_token':
    return refresh(client, form)
  raise OAuthError('unsupported_grant_type', "QA-Board supports the grant types authorization_code and refresh_token.")


def exchange_code(client, form) -> dict:
  from backend import db_session
  from .models import OAuthAuthorizationCode, OAuthGrant
  secret = form.get('code')
  if not secret:
    raise OAuthError('invalid_request', "Missing the code.")
  code = OAuthAuthorizationCode.query.filter(OAuthAuthorizationCode.code_hash == tokens_lib.hash_token(secret)).with_for_update().one_or_none()
  if code is None or code.oauth_client_id != client.id:
    raise OAuthError('invalid_grant', "This code is unknown.")
  if code.used_at is not None:
    # RFC 6749 §4.1.2: someone else has the code, what it gave is revoked
    if code.grant is not None:
      revoke_grant(code.grant, reason='code_reused')
      db_session.commit()
    raise OAuthError('invalid_grant', "This code was already used.")
  now = utcnow()
  if now >= code.expires_at:
    raise OAuthError('invalid_grant', "This code expired: ask the user again.")
  if form.get('redirect_uri') != code.redirect_uri:
    raise OAuthError('invalid_grant', "The redirect_uri isn't the one of the authorization request.")
  if not pkce_matches(form.get('code_verifier'), code.code_challenge):
    raise OAuthError('invalid_grant', "The code_verifier doesn't match the code_challenge.")
  check_resource(form, code.resource)
  check_user(code.user)
  grant = OAuthGrant(client=client, user=code.user, scopes=code.scopes, resource=code.resource, created_at=now, refreshed_at=now)
  db_session.add(grant)
  db_session.flush()
  code.used_at, code.grant_id = now, grant.id
  response = issue_tokens(grant, 'authorization_code')
  db_session.commit()
  return response


def refresh(client, form) -> dict:
  from backend import db_session
  from .models import OAuthRefreshToken
  secret = form.get('refresh_token')
  if not secret:
    raise OAuthError('invalid_request', "Missing the refresh_token.")
  row = OAuthRefreshToken.query.filter(OAuthRefreshToken.token_hash == tokens_lib.hash_token(secret)).with_for_update().one_or_none()
  if row is None or row.grant.oauth_client_id != client.id:
    raise OAuthError('invalid_grant', "This refresh token is unknown.")
  grant = row.grant
  if row.used_at is not None:
    # RFC 9700 §4.14.2: refresh tokens are used once. Someone else has it: the grant is revoked
    revoke_grant(grant, reason='refresh_token_reused')
    db_session.commit()
    raise OAuthError('invalid_grant', "This refresh token was already used: the user must allow the app again.")
  status = grant_status(grant)
  if status != 'active':
    raise OAuthError('invalid_grant', "The user revoked the app's access." if status == 'revoked' else "The app wasn't used for too long: the user must allow it again.")
  check_user(grant.user)
  if not set(parse_scope(form.get('scope') or ' '.join(grant.scopes))) <= set(grant.scopes):
    raise OAuthError('invalid_scope', f"This grant only has the scopes: {' '.join(grant.scopes)}.")
  check_resource(form, grant.resource)
  now = utcnow()
  row.used_at = now
  # used refresh tokens are kept to recognize them, as long as the grant would have stayed valid without them
  idle_since = now - idle_expiry()
  OAuthRefreshToken.query.filter(OAuthRefreshToken.grant_id == grant.id, OAuthRefreshToken.used_at < idle_since).delete(synchronize_session=False)
  response = issue_tokens(grant, 'refresh_token')
  db_session.commit()
  return response


def issue_tokens(grant, grant_type: str) -> dict:
  """
  The grant's access token, with a new secret, and a new refresh token: the token answer (RFC 6749 §5.1).
  Audited as the user's, who allowed the app. Added to the database session, the caller commits.
  """
  from backend import db_session
  from .models import OAuthRefreshToken
  from .audit import audit
  from .actor import Actor
  client, user = grant.client, grant.user
  now = utcnow()
  expires_at = now + ACCESS_TOKEN_LIFETIME
  meta = {"client_id": client.client_id, "grant": grant.id, "grant_type": grant_type}
  if grant.token is None:
    # who uses it: an app, for the user (Admin → Tokens shows them)
    data = {"client": client.name, "client_id": client.client_id, "grant": grant.id, "resource": grant.resource}
    grant.token, access_token = tokens_lib.mint(
      user, name=client.name, scopes=grant.scopes, kind='oauth', expires_at=expires_at, created_by=user, data=data,
      actor=Actor(user), audit_action='oauth.token', audit_meta=meta,
    )
  else:
    access_token = tokens_lib.renew(grant.token, expires_at)
    audit('oauth.token', 'token', grant.token.id, grant.token.name, meta={"user": user.user_name, **meta}, actor=Actor(user))
  refresh_token = tokens_lib.generate(REFRESH_TOKEN_PREFIX)
  db_session.add(OAuthRefreshToken(token_hash=tokens_lib.hash_token(refresh_token), grant_id=grant.id, created_at=now))
  grant.refreshed_at = now
  return {
    "access_token": access_token,
    "token_type": "Bearer",
    "expires_in": int(ACCESS_TOKEN_LIFETIME.total_seconds()),
    "refresh_token": refresh_token,
    "scope": ' '.join(grant.scopes),
  }


def revoke_token(client, secret: Optional[str]):
  """
  The revocation endpoint (RFC 7009): the app's refresh token or access token revokes its grant. Tokens that aren't
  the app's are ignored, the app learns nothing about them. The caller commits.
  """
  from .models import OAuthRefreshToken, OAuthGrant, Token
  from .actor import Actor
  if not secret:
    raise OAuthError('invalid_request', "Missing the token.")
  hashed = tokens_lib.hash_token(secret)
  row = OAuthRefreshToken.query.filter(OAuthRefreshToken.token_hash == hashed).one_or_none()
  grant = row.grant if row is not None else OAuthGrant.query.join(Token, OAuthGrant.token_id == Token.id).filter(Token.token_hash == hashed).one_or_none()
  if grant is not None and grant.oauth_client_id == client.id:
    revoke_grant(grant, reason='app', actor=Actor(grant.user))


# ==========================================
# Grants: what users allowed
# ==========================================

def idle_expiry() -> datetime.timedelta:
  from .settings import setting
  return datetime.timedelta(days=setting('oauth.refresh_idle_expiry_days'))


def grant_status(grant, now: Optional[datetime.datetime] = None) -> str:
  """active, revoked (also when its app's registration or its token is), or idle_expired (no token for too long)"""
  now = now or utcnow()
  if grant.revoked_at or grant.client.revoked_at or (grant.token is not None and tokens_lib.status(grant.token) == 'revoked'):
    return 'revoked'
  if now >= grant.refreshed_at + idle_expiry():
    return 'idle_expired'
  return 'active'


def revoke_grant(grant, reason: str, by=None, actor=None) -> bool:
  """Revokes what the user allowed the app, and its access token. Audited, the caller commits"""
  from .audit import audit
  if grant.revoked_at is not None:
    return False
  grant.revoked_at, grant.revoked_reason = utcnow(), reason
  audit('oauth_grant.revoke', 'oauth_grant', grant.id, grant.client.name, meta={
    "user": grant.user.user_name, "client_id": grant.client.client_id, "reason": reason,
  }, actor=actor)
  if grant.token is not None:
    tokens_lib.revoke(grant.token, by=by, reason=f"oauth: {reason}", actor=actor)
  return True


def active_grants(**filters) -> list:
  """The grants that work, e.g. active_grants(user_id=3) or active_grants(oauth_client_id=1), newest first"""
  from .models import OAuthGrant
  grants = OAuthGrant.query.filter_by(revoked_at=None, **filters).order_by(OAuthGrant.created_at.desc()).all()
  now = utcnow()
  return [grant for grant in grants if grant_status(grant, now) == 'active']


def serialize_grant(grant) -> dict:
  token = grant.token
  # the app's last API call, or its last refresh: it refreshes when it needs to call
  last_used = max(filter(None, [grant.refreshed_at, token.last_used_at if token is not None else None]))
  return {
    "id": grant.id,
    "client": {"id": grant.client.id, "name": grant.client.name, "client_id": grant.client.client_id},
    "scopes": [{"id": s, "description": SCOPES.get(s, s)} for s in grant.scopes or []],
    "resource": grant.resource,
    "created_at": tokens_lib.iso(grant.created_at),
    "last_used_at": tokens_lib.iso(last_used),
    "status": grant_status(grant),
  }


def serialize_client(client, nb_users: int = 0) -> dict:
  creator = client.created_by
  return {
    "id": client.id,
    "client_id": client.client_id,
    "name": client.name,
    "redirect_uris": client.redirect_uris or [],
    "secret_prefix": client.secret_prefix,
    "created_at": tokens_lib.iso(client.created_at),
    "created_by": {"id": creator.id, "user_name": creator.user_name} if creator is not None else None,
    "revoked_at": tokens_lib.iso(client.revoked_at),
    "status": 'revoked' if client.revoked_at else 'active',
    # who allowed it, and can use it now
    "users": nb_users,
  }
