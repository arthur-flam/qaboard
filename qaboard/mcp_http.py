"""
`qa mcp --http`: the MCP server (qaboard/mcp_server.py) for remote clients, e.g. Open WebUI, over Streamable HTTP at
/mcp. It runs next to QA-Board's backend (its image, behind the same proxy) and is stateless: replicas need nothing
shared. Design: docs/rfcs/mcp-server.md, "Deployment".

It is an OAuth protected resource (RFC 9728) and QA-Board is its authorization server: clients find QA-Board in
/.well-known/oauth-protected-resource, get a token from it for the person using them, and send it with each request.
Tokens are ordinary QA-Board API tokens: QA-Board says whether one is good (GET /api/v1/user/me/), and every request a
tool call makes to QA-Board carries the caller's token, never the credentials of the machine this runs on (`qa auth
login`, QABOARD_TOKEN). QA-Board checks each of them: what a caller sees is what their token can read.

Needs the `mcp` extra: pip install "qaboard[mcp]".
"""
import time
import hashlib
import datetime
import logging
from typing import Any, Dict, Optional, Tuple

import anyio.to_thread
from mcp.server.auth.middleware.auth_context import get_access_token
from mcp.server.auth.provider import AccessToken
from mcp.server.auth.routes import create_protected_resource_routes
from mcp.server.auth.settings import AuthSettings
from mcp.server.transport_security import TransportSecuritySettings
from pydantic import AnyHttpUrl
from starlette.applications import Starlette
from starlette.requests import Request
from starlette.responses import PlainTextResponse, Response
from starlette.routing import Route

from . import evidence
from .credentials import same_server
from .http_client import ResultsError, request_json
from .mcp_server import build_server
from .results import parse_url


logger = logging.getLogger(__name__)

MCP_PATH = '/mcp'
# RFC 9728 puts the metadata at this path + the resource's path; some clients also try it alone
PROTECTED_RESOURCE_PATH = '/.well-known/oauth-protected-resource'
SCOPE = 'read'
# How long QA-Board's word on a token is trusted. Revoking a token takes effect at once anyway: QA-Board checks it
# again on each request tool calls make
TOKEN_CACHE_SECONDS = 60.0
TOKEN_CACHE_SIZE = 10_000


class MissingToken(Exception):
  """A tool call without the caller's token: never answered with other credentials"""


class CallerApi(evidence.Api):
  """QA-Board's API with the token of who called the tool, for this call only"""

  def __init__(self, server: str, token: str, public_url: str):
    if not token:
      # evidence.Api would use this machine's credentials without one
      raise MissingToken("No access token: sign in to QA-Board again.")
    super().__init__(server, token, public_url)

  def for_url(self, url: Optional[str]) -> evidence.Api:
    """Only URLs of our QA-Board: evidence.Api would use this machine's logins (`qa auth login`) for others"""
    if url and not any(same_server(parse_url(url).server, ours) or url.startswith(ours + '/') for ours in (self.server, self.public_url)):
      raise evidence.EvidenceError(f"{url} is not on {self.public_url}: this MCP server only reads the QA-Board it belongs to.")
    return self


def caller_api(server: str, public_url: str) -> CallerApi:
  """The API for the tool call being answered, with the token the request was authenticated with"""
  access = get_access_token()
  if access is None:
    raise MissingToken("No access token: this MCP server answers only requests authenticated with a QA-Board token.")
  return CallerApi(server, access.token, public_url)


class QABoardTokens:
  """
  The SDK's TokenVerifier: a token is good when QA-Board says whose it is and that it can read. Answers are kept a
  little while (TOKEN_CACHE_SECONDS), by the token's hash and without the token: a client sends the same token with
  each request.
  """

  def __init__(self, server: str, ttl: float = TOKEN_CACHE_SECONDS):
    self.server = server
    self.ttl = ttl
    self._answers: Dict[str, Tuple[float, Optional[AccessToken]]] = {}

  async def verify_token(self, token: str) -> Optional[AccessToken]:
    key = hashlib.sha256(token.encode()).hexdigest()
    now = time.monotonic()
    known = self._answers.get(key)
    if known is not None and known[0] > now:
      access = known[1]
    else:
      access = await anyio.to_thread.run_sync(self.ask_qaboard, token)
      if len(self._answers) >= TOKEN_CACHE_SIZE:
        self._answers = {k: v for k, v in self._answers.items() if v[0] > now}
      self._answers[key] = (now + self.ttl, access.model_copy(update={'token': ''}) if access else None)
    return access.model_copy(update={'token': token}) if access else None

  def ask_qaboard(self, token: str) -> Optional[AccessToken]:
    try:
      me = request_json(self.server, 'user/me/', token=token)
    except ResultsError as e:
      if e.status in (401, 403):
        return None
      # QA-Board is down or unreachable: a server error, not a bad token (and not remembered)
      raise
    return access_token(token, me)


def access_token(token: str, me: Dict[str, Any]) -> Optional[AccessToken]:
  """QA-Board's answer about a token (GET /api/v1/user/me/) as the SDK's AccessToken, if it's a token that can read"""
  about = me.get('token') or {}
  scopes = [str(s) for s in about.get('scopes') or []]
  if not me.get('is_authenticated') or SCOPE not in scopes:
    return None
  return AccessToken(token=token, client_id=f"qaboard-token-{about.get('id')}", scopes=scopes,
                     expires_at=timestamp(about.get('expires_at')), subject=me.get('user_name'))


def timestamp(iso: Optional[str]) -> Optional[int]:
  if not iso:
    return None
  when = datetime.datetime.fromisoformat(iso)
  if when.tzinfo is None:
    when = when.replace(tzinfo=datetime.timezone.utc)
  return int(when.timestamp())


async def health(request: Request) -> Response:
  return PlainTextResponse('ok')


def create_app(server: str, public_url: Optional[str] = None) -> Starlette:
  """
  The ASGI app. `server`: where requests to QA-Board go, can be an internal address (e.g. http://proxy).
  `public_url`: QA-Board as users and clients see it (default: `server`): links, the OAuth metadata and the resource
  (<public_url>/mcp) name it.
  """
  server = server.rstrip('/')
  public_url = (public_url or server).rstrip('/')
  resource = AnyHttpUrl(f"{public_url}{MCP_PATH}")
  # validated from a string, the issuer keeps its spelling ("https://host", not "https://host/"): clients compare it
  # with the authorization server's metadata as a string (RFC 8414)
  auth = AuthSettings.model_validate({
    'issuer_url': public_url, 'resource_server_url': resource, 'required_scopes': [SCOPE],
    # QA-Board's tokens aren't bound to an audience: the whole API accepts them, and QA-Board checks them on each request
    'validate_token_resource': False,
  })
  mcp = build_server(lambda: caller_api(server, public_url), gate=False, auth=auth, token_verifier=QABoardTokens(server))
  # for the container's health check, not routed by the proxy
  mcp.custom_route('/health', methods=['GET'])(health)
  app = mcp.streamable_http_app(
    streamable_http_path=MCP_PATH, stateless_http=True,
    # Every request needs a token, which a DNS rebinding page can't have; and behind a proxy, the Host is the public one
    transport_security=TransportSecuritySettings(enable_dns_rebinding_protection=False),
  )
  [metadata] = create_protected_resource_routes(resource_url=resource, authorization_servers=[auth.issuer_url], scopes_supported=[SCOPE])
  app.router.routes.append(Route(PROTECTED_RESOURCE_PATH, endpoint=metadata.endpoint, methods=['GET', 'OPTIONS']))
  return app


def serve(server: str, public_url: Optional[str], host: str, port: int) -> None:
  import uvicorn
  app = create_app(server, public_url)
  logger.info("Serving QA-Board's MCP server at http://%s:%d%s, for %s", host, port, MCP_PATH, public_url or server)
  uvicorn.run(app, host=host, port=port, server_header=False)
