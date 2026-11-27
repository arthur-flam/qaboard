"""
QA-Board as an OAuth 2.1 authorization server (backend/oauth.py), for MCP clients like Open WebUI:
- its metadata (RFC 8414) at /.well-known/oauth-authorization-server;
- the protocol's endpoints, that the apps call: /api/v1/oauth/authorize, token, revoke. Public, also when the server
  requires signing in (backend/actor.py: PUBLIC_ENDPOINTS): the apps authenticate with their secret;
- the web app's consent page (/oauth/consent) reads and answers /api/v1/oauth/consent, from a browser session;
- users list and revoke the apps they allowed, admins register the apps.
"""
from urllib.parse import urlencode, urlsplit, parse_qsl
import html

from flask import request, jsonify, g, redirect, Response
from werkzeug.datastructures import MultiDict

from backend import app, db_session
from ..models import OAuthClient, OAuthGrant
from .. import oauth
from .auth import login_required, session_required, admin_required
from .tokens import public_url


def no_store(response):
  # RFC 6749 §5.1: tokens aren't cached
  response.headers['Cache-Control'] = 'no-store'
  response.headers['Pragma'] = 'no-cache'
  return response


def oauth_error(e: oauth.OAuthError):
  response = jsonify({"error": e.error, "error_description": e.description})
  response.status_code = e.status
  if e.basic:
    response.headers['WWW-Authenticate'] = 'Basic realm="QA-Board"'
  return no_store(response)


ERROR_PAGE = """<!doctype html>
<html lang="en">
<head><meta charset="utf-8"><meta name="viewport" content="width=device-width"><title>QA-Board can't sign you in to this app</title></head>
<body style="font-family: system-ui, sans-serif; max-width: 560px; margin: 64px auto; padding: 0 16px; line-height: 1.5">
  <h1 style="font-size: 20px">QA-Board can't sign you in to this app</h1>
  <p>{description}</p>
  <p style="color: #5f6b7c">Error: <code>{error}</code></p>
  <p><a href="/">Go to QA-Board</a></p>
</body>
</html>
"""


def error_page(e: oauth.ClientError):
  """For an unknown app or redirect URI: users see why, the browser isn't sent anywhere (RFC 6749 §4.1.2.1)"""
  page = ERROR_PAGE.format(description=html.escape(e.description), error=html.escape(e.error))
  return Response(page, status=400, mimetype='text/html')


@app.route('/.well-known/oauth-authorization-server', methods=['GET'])
@app.route('/.well-known/oauth-authorization-server/<path:issuer_path>', methods=['GET'])
def oauth_metadata(issuer_path=None):
  """For an issuer with a path, e.g. QABOARD_URL=https://example.com/qaboard, also at .../oauth-authorization-server/qaboard"""
  issuer = public_url()
  if issuer_path is not None and issuer_path.strip('/') != urlsplit(issuer).path.strip('/'):
    return jsonify({"error": f"This server's issuer is {issuer}."}), 404
  return jsonify(oauth.metadata(issuer))


@app.route('/api/v1/oauth/authorize', methods=['GET'])
def oauth_authorize():
  """Apps send users here. Checked now, so that apps learn about errors at once; the consent page checks again."""
  try:
    oauth.authorization_request(request.args)
  except oauth.ClientError as e:
    return error_page(e)
  except oauth.RedirectError as e:
    return redirect(e.url(public_url()))
  # the web app signs users in first if needed (/login?next=)
  return redirect(f"/oauth/consent?{urlencode(list(request.args.items(multi=True)))}")


@app.route('/api/v1/oauth/token', methods=['POST'])
def oauth_token():
  try:
    client = oauth.authenticate_client(request.headers.get('Authorization'), request.form)
    return no_store(jsonify(oauth.token(client, request.form)))
  except oauth.OAuthError as e:
    db_session.rollback()
    return oauth_error(e)


@app.route('/api/v1/oauth/revoke', methods=['POST'])
def oauth_revoke():
  try:
    client = oauth.authenticate_client(request.headers.get('Authorization'), request.form)
    oauth.revoke_token(client, request.form.get('token'))
  except oauth.OAuthError as e:
    db_session.rollback()
    return oauth_error(e)
  db_session.commit()
  return no_store(Response(status=200))


# ==========================================
# The consent page
# ==========================================

def consent_error(e: oauth.OAuthError):
  """The consent page shows why; for errors the app should learn about, it offers to go back to it"""
  body = {"error": e.description, "reason": e.error}
  if isinstance(e, oauth.RedirectError):
    body["redirect_to"] = e.url(public_url())
  return jsonify(body), 400


@app.route('/api/v1/oauth/consent', methods=['GET'])
@session_required
def get_oauth_consent():
  """?<the authorization request's parameters>: the app, what it asks, where users go back"""
  try:
    req = oauth.authorization_request(request.args)
  except oauth.OAuthError as e:
    return consent_error(e)
  return jsonify(oauth.serialize_request(req))


@app.route('/api/v1/oauth/consent', methods=['POST'])
@session_required
def answer_oauth_consent():
  """
  {request: "<the authorization request's query string>", approve: true|false} => {redirect_to}, where the browser goes
  back to the app. Only from the web app: a session (tokens can't allow apps), from our origin (backend/actor.py).
  """
  body = request.get_json(silent=True) or {}
  if not isinstance(body.get('approve'), bool) or not isinstance(body.get('request'), str):
    return jsonify({"error": "Send the authorization request's query string as `request`, and `approve`: true or false."}), 400
  try:
    req = oauth.authorization_request(MultiDict(parse_qsl(body['request'].lstrip('?'), keep_blank_values=True)))
  except oauth.OAuthError as e:
    return consent_error(e)
  redirect_to = oauth.answer(req, g.actor.user, body['approve'], public_url())
  db_session.commit()
  return jsonify({"redirect_to": redirect_to})


# ==========================================
# The apps users allowed
# ==========================================

@app.route('/api/v1/user/apps', methods=['GET'])
@login_required
def list_my_apps():
  return jsonify({"apps": [oauth.serialize_grant(grant) for grant in oauth.active_grants(user_id=g.actor.user.id)]})


@app.route('/api/v1/user/apps/<int:grant_id>', methods=['DELETE'])
@login_required
def revoke_my_app(grant_id):
  user = g.actor.user
  grant = OAuthGrant.query.filter(OAuthGrant.id == grant_id, OAuthGrant.user_id == user.id).one_or_none()
  if grant is None:
    return jsonify({"error": "You didn't allow this app."}), 404
  oauth.revoke_grant(grant, reason='user', by=user)
  db_session.commit()
  return jsonify(oauth.serialize_grant(grant))


# ==========================================
# Admins register the apps
# ==========================================

def serialize_client(client) -> dict:
  return oauth.serialize_client(client, nb_users=len({grant.user_id for grant in oauth.active_grants(oauth_client_id=client.id)}))


def get_client_or_404(client_id):
  client = OAuthClient.query.filter(OAuthClient.id == client_id).one_or_none()
  if client is None:
    return None, (jsonify({"error": "This app isn't registered."}), 404)
  return client, None


@app.route('/api/v1/admin/oauth/clients', methods=['GET'])
@admin_required
def admin_oauth_clients():
  clients = OAuthClient.query.order_by(OAuthClient.revoked_at.isnot(None), OAuthClient.created_at.desc()).all()
  return jsonify({"clients": [serialize_client(c) for c in clients]})


@app.route('/api/v1/admin/oauth/clients', methods=['POST'])
@admin_required
def admin_register_oauth_client():
  """{name, redirect_uris} => the app, with its `client_secret`, only returned now"""
  body = request.get_json(silent=True) or {}
  try:
    name, redirect_uris = oauth.clean_name(body.get('name')), oauth.clean_redirect_uris(body.get('redirect_uris'))
  except ValueError as e:
    return jsonify({"error": str(e)}), 400
  client, secret = oauth.register_client(name, redirect_uris, created_by=g.actor.user)
  db_session.commit()
  return jsonify({**oauth.serialize_client(client), "client_secret": secret}), 201


@app.route('/api/v1/admin/oauth/clients/<int:client_id>', methods=['PATCH'])
@admin_required
def admin_update_oauth_client(client_id):
  """{name?, redirect_uris?}"""
  client, error = get_client_or_404(client_id)
  if error:
    return error
  body = request.get_json(silent=True) or {}
  try:
    changes = {
      **({"name": oauth.clean_name(body['name'])} if 'name' in body else {}),
      **({"redirect_uris": oauth.clean_redirect_uris(body['redirect_uris'])} if 'redirect_uris' in body else {}),
    }
  except ValueError as e:
    return jsonify({"error": str(e)}), 400
  oauth.update_client(client, changes)
  db_session.commit()
  return jsonify(serialize_client(client))


@app.route('/api/v1/admin/oauth/clients/<int:client_id>', methods=['DELETE'])
@admin_required
def admin_revoke_oauth_client(client_id):
  """The app can't get tokens anymore, those it has stop working. Kept, revoked, for the audit log"""
  client, error = get_client_or_404(client_id)
  if error:
    return error
  oauth.revoke_client(client, by=g.actor.user)
  db_session.commit()
  return jsonify(serialize_client(client))
