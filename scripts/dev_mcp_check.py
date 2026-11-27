#!/usr/bin/env -S uv run --script
# /// script
# requires-python = ">=3.11"
# dependencies = ["requests>=2.31", "mcp>=2.3"]
# ///
"""
Checks QA-Board's remote MCP server end to end, the way Open WebUI connects to it: discovery from /mcp alone, a
client an admin registers, a user's consent, the authorization code with PKCE, MCP tool calls with the user's token,
refresh, revocation, and a replayed code. Against the development server:

  uv run scripts/dev.py up --mcp && uv run scripts/dev.py seed
  uv run scripts/dev_mcp_check.py

It registers a client named "Open WebUI (dev_mcp_check)" each time (revoked at the end). Exits 1 at the first failure.
"""
import argparse
import asyncio
import base64
import hashlib
import json
import re
import secrets
import sys
from pathlib import Path
from urllib.parse import parse_qs, urlsplit

import requests

REPO = Path(__file__).resolve().parent.parent
REDIRECT = 'https://openwebui.example.com/oauth/clients/mcp:qaboard/callback'


def check(condition: bool, what: str):
  print(f"{'ok  ' if condition else 'FAIL'} {what}")
  if not condition:
    sys.exit(1)


def pkce():
  verifier = secrets.token_urlsafe(48)
  return verifier, base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest()).rstrip(b'=').decode()


async def call_tools(web: str, token: str, calls):
  """The tools' answers to `calls` [(name, arguments)], over Streamable HTTP with the user's token"""
  import httpx2
  from mcp import Client
  from mcp.client.streamable_http import streamable_http_client
  async with httpx2.AsyncClient(headers={'Authorization': f"Bearer {token}"}, timeout=300) as http:
    async with Client(streamable_http_client(f"{web}/mcp", http_client=http)) as client:
      return [await client.list_tools()] + [await client.call_tool(name, arguments) for name, arguments in calls]


def discover(web: str) -> tuple:
  """What Open WebUI finds from the MCP server's URL alone: the protected resource, then its authorization server"""
  r = requests.post(f"{web}/mcp", json={'jsonrpc': '2.0', 'method': 'initialize', 'params': {}, 'id': 1})
  check(r.status_code == 401, f"/mcp without a token: {r.status_code}")
  found = re.search(r'resource_metadata="?([^",\s]+)', r.headers.get('WWW-Authenticate', ''))
  check(bool(found), "WWW-Authenticate names the protected resource metadata")
  resource = requests.get(found.group(1)).json()
  check(resource.get('resource') == f"{web}/mcp" and bool(resource.get('authorization_servers')), f"protected resource: {resource}")
  issuer = resource['authorization_servers'][0].rstrip('/')
  meta = requests.get(f"{issuer}/.well-known/oauth-authorization-server").json()
  check(meta.get('issuer') == issuer and 'S256' in meta.get('code_challenge_methods_supported', []), f"authorization server: {issuer}")
  return resource, meta


def allow(web: str, browser: requests.Session, meta: dict, client_id: str, challenge: str, resource: str = None) -> dict:
  """A signed-in user goes through the authorization endpoint and allows the app: the redirect's parameters"""
  state = secrets.token_urlsafe(8)
  params = {'response_type': 'code', 'client_id': client_id, 'redirect_uri': REDIRECT, 'code_challenge': challenge,
            'code_challenge_method': 'S256', 'state': state, 'scope': 'read', **({'resource': resource} if resource else {})}
  r = browser.get(meta['authorization_endpoint'], params=params, allow_redirects=False)
  consent = r.headers.get('Location', '')
  check(r.status_code in (302, 303) and '/oauth/consent' in consent, "the authorization endpoint sends to the consent page")
  query = urlsplit(consent).query
  shown = browser.get(f"{web}/api/v1/oauth/consent?{query}", headers={'Origin': web}).json()
  check('dev_mcp_check' in json.dumps(shown), "the consent page names the app")
  answer = browser.post(f"{web}/api/v1/oauth/consent", json={'request': query, 'approve': True}, headers={'Origin': web}).json()
  back = parse_qs(urlsplit(answer['redirect_to']).query)
  check(answer['redirect_to'].startswith(REDIRECT) and back.get('state') == [state] and back.get('iss') == [meta['issuer']],
        "allowed: back to the app with a code, the state and the issuer")
  return back


def main():
  parser = argparse.ArgumentParser(description=__doc__.split('\n\n')[0])
  parser.add_argument('--web', default='http://localhost:3000', help="the web app's URL: the OAuth issuer and the MCP server's public URL")
  parser.add_argument('--user', default='admin')
  parser.add_argument('--password', default='qaboard-dev')
  parser.add_argument('--admin-token', default=None, help="default: the dev server's (.dev/token)")
  args = parser.parse_args()
  web = args.web.rstrip('/')
  admin = {'Authorization': f"Bearer {args.admin_token or (REPO / '.dev' / 'token').read_text().strip()}"}

  resource, meta = discover(web)

  r = requests.post(f"{web}/api/v1/admin/oauth/clients", json={'name': 'Open WebUI (dev_mcp_check)', 'redirect_uris': [REDIRECT]}, headers=admin)
  check(r.status_code == 201, f"an admin registers the client: {r.status_code}")
  registered = r.json()
  client_id, client_secret = registered['client_id'], registered['client_secret']
  token_endpoint = lambda data: requests.post(meta['token_endpoint'], data=data, auth=(client_id, client_secret))  # noqa: E731

  try:
    browser = requests.Session()
    check(browser.post(f"{web}/api/v1/user/auth/", data={'username': args.user, 'password': args.password}, headers={'Origin': web}).ok, "the user signs in")
    verifier, challenge = pkce()
    code = allow(web, browser, meta, client_id, challenge, resource['resource'])['code'][0]
    r = token_endpoint({'grant_type': 'authorization_code', 'code': code, 'redirect_uri': REDIRECT, 'code_verifier': verifier, 'resource': resource['resource']})
    tokens = r.json()
    check(r.status_code == 200 and tokens.get('scope') == 'read', f"the client gets tokens: scope {tokens.get('scope')}, {tokens.get('expires_in')} s")

    tools, found = asyncio.run(call_tools(web, tokens['access_token'], [('find_results', {})]))
    names = sorted(t.name for t in tools.tools)
    check('compare' in names and 'check_gate' not in names, f"tools: {', '.join(names)}")
    check(not found.is_error, "find_results, as the user")
    print('\n'.join('     ' + line for line in found.content[0].text.splitlines()[:6]))

    r = token_endpoint({'grant_type': 'refresh_token', 'refresh_token': tokens['refresh_token']})
    refreshed = r.json()
    check(r.status_code == 200 and refreshed.get('refresh_token') != tokens['refresh_token'], "refreshed: the refresh token rotated")
    me = requests.get(f"{web}/api/v1/user/me/", headers={'Authorization': f"Bearer {refreshed['access_token']}"}).json()
    check(me.get('user_name') == args.user and me.get('token', {}).get('scopes') == ['read'], "the new access token reads as the user")
    me = requests.get(f"{web}/api/v1/user/me/", headers={'Authorization': f"Bearer {tokens['access_token']}"}).json()
    check(me.get('user_name') == args.user, "the previous access token works until it expires (calls in progress)")

    check(token_endpoint({'grant_type': 'refresh_token', 'refresh_token': tokens['refresh_token']}).status_code == 400,
          "the old refresh token is refused")
    me = requests.get(f"{web}/api/v1/user/me/", headers={'Authorization': f"Bearer {refreshed['access_token']}"}).json()
    check(not me.get('is_authenticated'), "and its reuse revoked what the grant gave")

    verifier, challenge = pkce()
    code = allow(web, browser, meta, client_id, challenge)['code'][0]
    first = token_endpoint({'grant_type': 'authorization_code', 'code': code, 'redirect_uri': REDIRECT, 'code_verifier': verifier}).json()
    check(requests.post(meta['revocation_endpoint'], data={'token': first['refresh_token']}, auth=(client_id, client_secret)).ok, "the client revokes")
    me = requests.get(f"{web}/api/v1/user/me/", headers={'Authorization': f"Bearer {first['access_token']}"}).json()
    check(not me.get('is_authenticated'), "revoked tokens stop working")
  finally:
    requests.delete(f"{web}/api/v1/admin/oauth/clients/{registered['id']}", headers=admin)
  print("The remote MCP server works with OAuth, like Open WebUI uses it.")


if __name__ == '__main__':
  main()
