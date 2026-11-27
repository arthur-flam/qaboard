"""QA-Board as an OAuth 2.1 authorization server, for MCP clients (backend/oauth.py): the flow Open WebUI follows"""
import base64
import hashlib
import secrets
import datetime
from urllib.parse import urlsplit, parse_qs, urlencode

import pytest

from backend import clock
from .conftest import bearer, new_token


REDIRECT_URI = "https://openwebui.example.com/oauth/clients/mcp:qaboard/callback"
RESOURCE = "http://localhost/mcp"


def pkce():
  verifier = secrets.token_urlsafe(48)
  challenge = base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest()).rstrip(b'=').decode()
  return verifier, challenge


def query(url: str) -> dict:
  return {k: v[0] for k, v in parse_qs(urlsplit(url).query).items()}


@pytest.fixture
def app_client(logged_in):
  """An admin registers the app: its id and secret, as admins paste them in Open WebUI"""
  admin = logged_in('admin', is_admin=True)
  response = admin.post('/api/v1/admin/oauth/clients', json={"name": "Open WebUI", "redirect_uris": [REDIRECT_URI]})
  assert response.status_code == 201, response.get_json()
  registered = response.get_json()
  assert registered["client_id"].startswith("qab_app_") and registered["client_secret"].startswith("qab_sec_")
  assert registered["status"] == "active" and registered["users"] == 0
  registered["admin"] = admin
  return registered


def authorization(app_client, **params):
  verifier, challenge = pkce()
  return verifier, {
    "client_id": app_client["client_id"], "redirect_uri": REDIRECT_URI, "response_type": "code", "scope": "read",
    "code_challenge": challenge, "code_challenge_method": "S256", "state": "xyz", "resource": RESOURCE, **params,
  }


def allow(browser, app_client, approve=True, **params):
  """The user goes through the authorization endpoint and the consent page: (code_verifier, where they're sent back)"""
  verifier, params = authorization(app_client, **params)
  response = browser.get(f'/api/v1/oauth/authorize?{urlencode(params)}')
  assert response.status_code == 302, response.get_data(as_text=True)
  consent = response.headers['Location']
  assert consent.startswith('/oauth/consent?')
  request = consent.split('?', 1)[1]
  details = browser.get(f'/api/v1/oauth/consent?{request}').get_json()
  assert details["client"]["name"] == "Open WebUI" and details["redirect_host"] == "openwebui.example.com"
  assert details["scopes"] == [{"id": "read", "description": "Read your projects and results"}]
  response = browser.post('/api/v1/oauth/consent', json={"request": request, "approve": approve})
  assert response.status_code == 200, response.get_json()
  return verifier, response.get_json()["redirect_to"]


def exchange(client, app_client, code, verifier, **form):
  return client.post('/api/v1/oauth/token', data={
    "grant_type": "authorization_code", "code": code, "redirect_uri": REDIRECT_URI, "code_verifier": verifier,
    "client_id": app_client["client_id"], "client_secret": app_client["client_secret"], "resource": RESOURCE, **form,
  })


def basic(app_client) -> dict:
  credentials = f"{app_client['client_id']}:{app_client['client_secret']}".encode()
  return {"Authorization": f"Basic {base64.b64encode(credentials).decode()}"}


def refresh(client, app_client, refresh_token):
  return client.post('/api/v1/oauth/token', headers=basic(app_client), data={
    "grant_type": "refresh_token", "refresh_token": refresh_token, "resource": RESOURCE,
  })


def tokens_of(client, app_client, browser):
  verifier, back = allow(browser, app_client)
  response = exchange(client, app_client, query(back)["code"], verifier)
  assert response.status_code == 200, response.get_json()
  return response.get_json()


def test_metadata(client, monkeypatch):
  monkeypatch.setenv('QABOARD_URL', 'https://qa.example.com/')
  metadata = client.get('/.well-known/oauth-authorization-server').get_json()
  assert metadata["issuer"] == "https://qa.example.com"
  assert metadata["authorization_endpoint"] == "https://qa.example.com/api/v1/oauth/authorize"
  assert metadata["token_endpoint"] == "https://qa.example.com/api/v1/oauth/token"
  assert metadata["revocation_endpoint"] == "https://qa.example.com/api/v1/oauth/revoke"
  assert metadata["code_challenge_methods_supported"] == ["S256"] and metadata["scopes_supported"] == ["read"]
  assert metadata["token_endpoint_auth_methods_supported"] == ["client_secret_basic", "client_secret_post"]
  # RFC 8414 §3: the metadata of an issuer with a path is after the well-known path
  assert client.get('/.well-known/oauth-authorization-server/mcp').status_code == 404
  monkeypatch.setenv('QABOARD_URL', 'https://example.com/qaboard')
  assert client.get('/.well-known/oauth-authorization-server/qaboard').get_json()["issuer"] == "https://example.com/qaboard"


def test_the_whole_flow(app_client, logged_in, make_client):
  browser = logged_in('alice')
  verifier, back = allow(browser, app_client)
  assert back.startswith(REDIRECT_URI + '?')
  answer = query(back)
  assert answer["state"] == "xyz" and answer["iss"] == "http://localhost" and answer["code"]

  app = make_client()
  response = exchange(app, app_client, answer["code"], verifier)
  assert response.status_code == 200, response.get_json()
  assert response.headers["Cache-Control"] == "no-store"
  tokens = response.get_json()
  assert tokens["token_type"] == "Bearer" and tokens["expires_in"] == 3600 and tokens["scope"] == "read"
  assert tokens["access_token"].startswith("qab_tmp_") and tokens["refresh_token"].startswith("qab_rft_")

  # the MCP server checks the token like this, then calls the API with it
  me = app.get('/api/v1/user/me/', headers=bearer(tokens["access_token"])).get_json()
  token_id = me["token"]["id"]
  assert me["user_name"] == "alice" and me["token"]["kind"] == "oauth" and me["token"]["scopes"] == ["read"]
  assert me["token"]["name"] == "Open WebUI"
  assert app.get('/api/v1/projects', headers=bearer(tokens["access_token"])).status_code == 200
  # read-only
  response = app.post('/api/v1/user/preferences', json={}, headers=bearer(tokens["access_token"]))
  assert response.status_code == 403 and response.get_json()["reason"] == "missing_scope"
  # it can't create tokens
  assert app.post('/api/v1/user/tokens', json={"name": "x", "scopes": ["read"]}, headers=bearer(tokens["access_token"])).status_code == 403

  # Open WebUI refreshes with HTTP Basic: a new access token. The previous one works until it expires: a long tool
  # call that uses it isn't cut when the app refreshes for another chat
  response = refresh(app, app_client, tokens["refresh_token"])
  assert response.status_code == 200, response.get_json()
  refreshed = response.get_json()
  assert refreshed["refresh_token"] != tokens["refresh_token"] and refreshed["access_token"] != tokens["access_token"]
  assert app.get('/api/v1/user/me/', headers=bearer(tokens["access_token"])).status_code == 200
  me = app.get('/api/v1/user/me/', headers=bearer(refreshed["access_token"])).get_json()
  assert me["user_name"] == "alice" and me["token"]["kind"] == "oauth" and me["token"]["id"] != token_id

  # the user sees the app, and when it was last used
  apps = browser.get('/api/v1/user/apps').get_json()["apps"]
  assert [(a["client"]["name"], a["status"], a["resource"]) for a in apps] == [("Open WebUI", "active", RESOURCE)]
  assert app_client["admin"].get('/api/v1/admin/oauth/clients').get_json()["clients"][0]["users"] == 1

  # the user revokes it: all its tokens stop working
  assert browser.delete(f'/api/v1/user/apps/{apps[0]["id"]}').get_json()["status"] == "revoked"
  for access_token in (tokens["access_token"], refreshed["access_token"]):
    assert app.get('/api/v1/user/me/', headers=bearer(access_token)).get_json()["reason"] == "token_revoked"
  response = refresh(app, app_client, refreshed["refresh_token"])
  assert response.status_code == 400 and response.get_json()["error"] == "invalid_grant"
  assert browser.get('/api/v1/user/apps').get_json()["apps"] == []

  actions = [e["action"] for e in browser.get('/api/v1/user/audit').get_json()["events"]]
  for action in ("oauth.approve", "oauth.token", "oauth_grant.revoke", "token.revoke"):
    assert action in actions, action
  # refreshes are routine: only the first tokens are audited
  assert actions.count("oauth.token") == 1


def grant_tokens():
  """The access tokens of the grants, as (grant id, token status), by token id"""
  from backend.database import db_session
  from backend.models import Token
  from backend.tokens import status
  db_session.expire_all()
  return [(t.oauth_grant_id, status(t)) for t in Token.query.filter(Token.kind == 'oauth').order_by(Token.id)]


def test_expired_access_tokens_are_deleted_at_the_next_refresh(app_client, logged_in, make_client):
  from backend.database import db_session
  from backend.models import Token
  app = make_client()
  tokens = tokens_of(app, app_client, logged_in('alice'))
  refreshed = refresh(app, app_client, tokens["refresh_token"]).get_json()
  grant_id = grant_tokens()[0][0]
  assert grant_id is not None and grant_tokens() == [(grant_id, "active"), (grant_id, "active")]
  Token.query.filter(Token.kind == 'oauth').update({"expires_at": clock.utcnow() - datetime.timedelta(seconds=1)})
  db_session.commit()
  assert app.get('/api/v1/user/me/', headers=bearer(refreshed["access_token"])).status_code == 401
  refreshed = refresh(app, app_client, refreshed["refresh_token"]).get_json()
  # a grant keeps one or two
  assert grant_tokens() == [(grant_id, "active")]
  assert app.get('/api/v1/user/me/', headers=bearer(refreshed["access_token"])).status_code == 200


def test_allowing_the_app_again_replaces_the_grant(app_client, logged_in, make_client):
  from backend.models import OAuthGrant
  app = make_client()
  browser = logged_in('alice')
  first = tokens_of(app, app_client, browser)
  # another user's grant isn't replaced
  bob = tokens_of(app, app_client, logged_in('bob'))
  second = tokens_of(app, app_client, browser)
  assert refresh(app, app_client, first["refresh_token"]).get_json()["error"] == "invalid_grant"
  assert app.get('/api/v1/user/me/', headers=bearer(first["access_token"])).status_code == 401
  assert app.get('/api/v1/user/me/', headers=bearer(second["access_token"])).status_code == 200
  assert app.get('/api/v1/user/me/', headers=bearer(bob["access_token"])).status_code == 200
  assert [a["client"]["name"] for a in browser.get('/api/v1/user/apps').get_json()["apps"]] == ["Open WebUI"]
  assert sorted(g.revoked_reason or '' for g in OAuthGrant.query.all()) == ['', '', 'replaced']


def test_denied(app_client, logged_in):
  browser = logged_in('alice')
  _, back = allow(browser, app_client, approve=False)
  assert query(back) == {"error": "access_denied", "error_description": "The user didn't allow the app.", "state": "xyz", "iss": "http://localhost"}


def test_unknown_apps_and_redirect_uris_get_an_error_page(app_client, client):
  _, params = authorization(app_client)
  for wrong in ({"client_id": "qab_app_nope"}, {"redirect_uri": "https://evil.example.com/callback"}, {"redirect_uri": REDIRECT_URI + "/"}):
    response = client.get(f'/api/v1/oauth/authorize?{urlencode({**params, **wrong})}')
    assert response.status_code == 400 and response.mimetype == "text/html"
    # what Open WebUI's check of the registration looks for
    assert "invalid_client" in response.get_data(as_text=True) or "redirect_uri" in response.get_data(as_text=True)


@pytest.mark.parametrize("params, error", [
  ({"response_type": "token"}, "unsupported_response_type"),
  ({"code_challenge": None}, "invalid_request"),
  ({"code_challenge_method": "plain"}, "invalid_request"),
  ({"scope": "admin"}, "invalid_scope"),
  ({"resource": "mcp"}, "invalid_target"),
])
def test_other_errors_go_back_to_the_app(app_client, client, params, error):
  _, request = authorization(app_client, **params)
  response = client.get(f'/api/v1/oauth/authorize?{urlencode({k: v for k, v in request.items() if v is not None})}')
  assert response.status_code == 302 and response.headers["Location"].startswith(REDIRECT_URI)
  assert query(response.headers["Location"])["error"] == error and query(response.headers["Location"])["state"] == "xyz"


def test_unknown_scopes_are_ignored(app_client, logged_in, make_client):
  browser = logged_in('alice')
  verifier, back = allow(browser, app_client, scope="read offline_access")
  assert exchange(make_client(), app_client, query(back)["code"], verifier).get_json()["scope"] == "read"


def test_codes_are_checked(app_client, logged_in, make_client):
  browser = logged_in('alice')
  app = make_client()
  verifier, back = allow(browser, app_client)
  code = query(back)["code"]
  for form, error in [
    ({"code_verifier": pkce()[0]}, "invalid_grant"),
    ({"redirect_uri": "https://openwebui.example.com/other"}, "invalid_grant"),
    ({"resource": "https://elsewhere.example.com/mcp"}, "invalid_target"),
    ({"client_secret": "qab_sec_wrong"}, "invalid_client"),
  ]:
    response = exchange(app, app_client, code, verifier, **form)
    assert response.get_json()["error"] == error, form
  # none of these used the code
  assert exchange(app, app_client, code, verifier).status_code == 200


def test_a_code_used_twice_revokes_what_it_gave(app_client, logged_in, make_client):
  browser = logged_in('alice')
  app = make_client()
  verifier, back = allow(browser, app_client)
  tokens = exchange(app, app_client, query(back)["code"], verifier).get_json()
  response = exchange(app, app_client, query(back)["code"], verifier)
  assert response.status_code == 400 and response.get_json()["error"] == "invalid_grant"
  assert app.get('/api/v1/user/me/', headers=bearer(tokens["access_token"])).status_code == 401
  assert refresh(app, app_client, tokens["refresh_token"]).get_json()["error"] == "invalid_grant"


def test_codes_expire(app_client, logged_in, make_client):
  from backend.database import db_session
  from backend.models import OAuthAuthorizationCode
  verifier, back = allow(logged_in('alice'), app_client)
  OAuthAuthorizationCode.query.update({"expires_at": clock.utcnow() - datetime.timedelta(seconds=1)})
  db_session.commit()
  assert exchange(make_client(), app_client, query(back)["code"], verifier).get_json()["error"] == "invalid_grant"


def test_a_refresh_token_used_twice_revokes_the_grant(app_client, logged_in, make_client):
  app = make_client()
  tokens = tokens_of(app, app_client, logged_in('alice'))
  refreshed = refresh(app, app_client, tokens["refresh_token"]).get_json()
  assert refresh(app, app_client, tokens["refresh_token"]).get_json()["error"] == "invalid_grant"
  # who stole it, or the app, lost everything
  assert app.get('/api/v1/user/me/', headers=bearer(refreshed["access_token"])).status_code == 401
  assert refresh(app, app_client, refreshed["refresh_token"]).get_json()["error"] == "invalid_grant"


def test_unused_grants_expire(app_client, logged_in, make_client):
  from backend.database import db_session
  from backend.models import OAuthGrant
  app = make_client()
  browser = logged_in('alice')
  tokens = tokens_of(app, app_client, browser)
  OAuthGrant.query.update({"refreshed_at": clock.utcnow() - datetime.timedelta(days=91)})
  db_session.commit()
  assert refresh(app, app_client, tokens["refresh_token"]).get_json()["error"] == "invalid_grant"
  assert browser.get('/api/v1/user/apps').get_json()["apps"] == []


def test_other_apps_cant_use_the_tokens(app_client, logged_in, make_client):
  other = app_client["admin"].post('/api/v1/admin/oauth/clients', json={"name": "Other", "redirect_uris": [REDIRECT_URI]}).get_json()
  app = make_client()
  browser = logged_in('alice')
  verifier, back = allow(browser, app_client)
  assert exchange(app, other, query(back)["code"], verifier).get_json()["error"] == "invalid_grant"
  tokens = exchange(app, app_client, query(back)["code"], verifier).get_json()
  assert refresh(app, other, tokens["refresh_token"]).get_json()["error"] == "invalid_grant"
  # nor revoke them
  assert app.post('/api/v1/oauth/revoke', headers=basic(other), data={"token": tokens["refresh_token"]}).status_code == 200
  assert app.get('/api/v1/user/me/', headers=bearer(tokens["access_token"])).status_code == 200


def test_client_authentication(app_client, client):
  response = client.post('/api/v1/oauth/token', headers={"Authorization": "Basic bm9wZTpub3Bl"}, data={"grant_type": "refresh_token", "refresh_token": "x"})
  assert response.status_code == 401 and response.get_json()["error"] == "invalid_client"
  assert response.headers["WWW-Authenticate"] == 'Basic realm="QA-Board"'
  response = client.post('/api/v1/oauth/token', data={"grant_type": "refresh_token", "refresh_token": "x", "client_id": app_client["client_id"]})
  assert response.status_code == 401 and "WWW-Authenticate" not in response.headers
  # one way at a time
  response = client.post('/api/v1/oauth/token', headers=basic(app_client), data={"grant_type": "refresh_token", "refresh_token": "x", "client_secret": app_client["client_secret"]})
  assert response.get_json()["error"] == "invalid_request"
  response = client.post('/api/v1/oauth/token', headers=basic(app_client), data={"grant_type": "password"})
  assert response.get_json()["error"] == "unsupported_grant_type"


def test_the_app_revokes_its_tokens(app_client, logged_in, make_client):
  app = make_client()
  tokens = tokens_of(app, app_client, logged_in('alice'))
  assert app.post('/api/v1/oauth/revoke', headers=basic(app_client), data={"token": tokens["access_token"]}).status_code == 200
  assert app.get('/api/v1/user/me/', headers=bearer(tokens["access_token"])).status_code == 401
  assert refresh(app, app_client, tokens["refresh_token"]).get_json()["error"] == "invalid_grant"
  # unknown tokens: nothing to say
  assert app.post('/api/v1/oauth/revoke', headers=basic(app_client), data={"token": "nope"}).status_code == 200


def test_admins_revoke_the_app(app_client, logged_in, make_client):
  app = make_client()
  tokens = tokens_of(app, app_client, logged_in('alice'))
  admin = app_client["admin"]
  revoked = admin.delete(f'/api/v1/admin/oauth/clients/{app_client["id"]}').get_json()
  assert revoked["status"] == "revoked" and revoked["users"] == 0
  assert app.get('/api/v1/user/me/', headers=bearer(tokens["access_token"])).status_code == 401
  assert refresh(app, app_client, tokens["refresh_token"]).get_json()["error"] == "invalid_client"
  _, params = authorization(app_client)
  assert app.get(f'/api/v1/oauth/authorize?{urlencode(params)}').status_code == 400


def test_revoking_the_token_in_admin_tokens_revokes_the_app(app_client, logged_in, make_client):
  app = make_client()
  browser = logged_in('alice')
  tokens = tokens_of(app, app_client, browser)
  admin = app_client["admin"]
  listed = [t for t in admin.get('/api/v1/admin/tokens').get_json()["tokens"] if t["kind"] == "oauth"]
  assert [(t["name"], t["user"]["user_name"], t["scopes"], t["data"]["client"]) for t in listed] == [("Open WebUI", "alice", ["read"], "Open WebUI")]
  refreshed = refresh(app, app_client, tokens["refresh_token"]).get_json()
  # the earlier access token: the grant's other one stops too
  admin.delete(f'/api/v1/admin/tokens/{listed[0]["id"]}')
  assert app.get('/api/v1/user/me/', headers=bearer(refreshed["access_token"])).status_code == 401
  assert refresh(app, app_client, refreshed["refresh_token"]).get_json()["error"] == "invalid_grant"
  assert browser.get('/api/v1/user/apps').get_json()["apps"] == []
  from backend.models import OAuthGrant
  assert [g.revoked_reason for g in OAuthGrant.query.all()] == ["token_revoked"]
  revoked = [e for e in admin.get('/api/v1/admin/audit').get_json()["events"] if e["action"] == "oauth_grant.revoke"]
  assert [e["meta"]["reason"] for e in revoked] == ["token_revoked"]


def test_used_codes_and_revoked_refresh_tokens_are_deleted(app_client, logged_in, make_client):
  from backend.database import db_session
  from backend.models import OAuthAuthorizationCode, OAuthRefreshToken
  app = make_client()
  browser = logged_in('alice')
  tokens = tokens_of(app, app_client, browser)
  refresh(app, app_client, tokens["refresh_token"])
  assert OAuthRefreshToken.query.count() == 2
  # kept a day after they expire, to recognize a code that comes again
  OAuthAuthorizationCode.query.update({"expires_at": clock.utcnow() - datetime.timedelta(days=1, seconds=1)})
  db_session.commit()
  allow(browser, app_client)
  db_session.expire_all()
  assert OAuthAuthorizationCode.query.count() == 1
  [grant] = browser.get('/api/v1/user/apps').get_json()["apps"]
  browser.delete(f'/api/v1/user/apps/{grant["id"]}')
  assert OAuthRefreshToken.query.count() == 0


def test_disabled_users_apps_stop(app_client, logged_in, make_client):
  from backend.database import db_session
  from backend.models import User
  app = make_client()
  tokens = tokens_of(app, app_client, logged_in('alice'))
  User.query.filter(User.user_name == 'alice').update({"status": "disabled"})
  db_session.commit()
  assert refresh(app, app_client, tokens["refresh_token"]).get_json()["error"] == "invalid_grant"


def test_only_browser_sessions_answer(app_client, logged_in, make_client):
  browser = logged_in('alice')
  _, params = authorization(app_client)
  request = urlencode(params)
  secret = new_token(browser)["token"]
  response = make_client().post('/api/v1/oauth/consent', json={"request": request, "approve": True}, headers=bearer(secret))
  assert response.status_code == 403 and response.get_json()["reason"] == "session_required"
  assert make_client().post('/api/v1/oauth/consent', json={"request": request, "approve": True}).status_code == 401
  response = browser.post('/api/v1/oauth/consent', json={"request": request, "approve": True}, headers={"Origin": "https://evil.example.com"})
  assert response.status_code == 403 and response.get_json()["reason"] == "cross_site_write"
  assert browser.get('/api/v1/oauth/consent?client_id=nope').get_json()["reason"] == "invalid_client"


def test_it_works_when_sign_in_is_required(app_client, logged_in, make_client, monkeypatch):
  monkeypatch.setenv('QABOARD_LOGIN_REQUIRED', 'true')
  app = make_client()
  _, params = authorization(app_client)
  assert app.get(f'/api/v1/oauth/authorize?{urlencode(params)}').status_code == 302
  tokens = tokens_of(app, app_client, logged_in('alice'))
  assert refresh(app, app_client, tokens["refresh_token"]).status_code == 200


def test_admins_register_apps(logged_in):
  admin = logged_in('admin', is_admin=True)
  for body in ({"name": "x"}, {"name": "", "redirect_uris": [REDIRECT_URI]}, {"name": "x", "redirect_uris": ["javascript:alert(1)"]},
               {"name": "x", "redirect_uris": ["https://a.example.com/cb#frag"]}, {"name": "x", "redirect_uris": ["http://a.example.com/cb"]}):
    assert admin.post('/api/v1/admin/oauth/clients', json=body).status_code == 400, body
  created = admin.post('/api/v1/admin/oauth/clients', json={"name": "Open WebUI", "redirect_uris": [REDIRECT_URI]}).get_json()
  # the secret is only shown once
  assert "client_secret" not in admin.get('/api/v1/admin/oauth/clients').get_json()["clients"][0]
  updated = admin.patch(f'/api/v1/admin/oauth/clients/{created["id"]}', json={"redirect_uris": [REDIRECT_URI, "http://localhost:8080/cb"]}).get_json()
  assert updated["redirect_uris"] == [REDIRECT_URI, "http://localhost:8080/cb"]
  response = admin.patch(f'/api/v1/admin/oauth/clients/{created["id"]}', json={"redirect_uris": ["http://openwebui.example.com/cb"]})
  assert response.status_code == 400 and "must use https" in response.get_json()["error"]
  assert logged_in('bob').get('/api/v1/admin/oauth/clients').status_code == 403
  actions = [e["action"] for e in admin.get('/api/v1/admin/audit').get_json()["events"]]
  assert "oauth_client.create" in actions and "oauth_client.update" in actions
