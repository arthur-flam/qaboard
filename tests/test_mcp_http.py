"""
Tests of `qa mcp --http` (qaboard/mcp_http.py): the MCP server for remote clients, an OAuth protected resource whose
tool calls use the caller's QA-Board token. A real uvicorn on a free port, the SDK's Streamable HTTP client, and the fake
QA-Board of tests/fake_qaboard.py, which also answers GET /api/v1/user/me/ here. Needs the `mcp` extra: skipped without.
"""
import os
import sys
import json
import socket
import asyncio
import threading
import unittest
from http.server import ThreadingHTTPServer
from pathlib import Path
from unittest import mock
from urllib.parse import urlsplit

sys.path.insert(0, str(Path(__file__).resolve().parent))
import fake_qaboard as fake  # noqa: E402

try:
  import mcp  # noqa: F401
  has_mcp = True
except ImportError:
  has_mcp = False


PUBLIC_URL = 'https://qaboard.example.com'
# token -> its scopes. Others are unknown to the fake QA-Board
TOKENS = {'alice-token': ['read'], 'bob-token': ['read', 'act'], 'act-only': ['act']}


class Handler(fake.Handler):
  """
  The fake QA-Board, which also says whose a token is (backend/backend/api/auth.py get_current_user), and lists a
  project of the token's user only
  """
  checked: list = []

  def do_GET(self):
    path = urlsplit(self.path).path
    token = (self.headers.get('Authorization') or '').removeprefix('Bearer ')
    if path == '/api/v1/projects' and token in TOKENS:
      fake.Handler.requests_seen.append(('GET', path, {}, self.headers.get('Authorization')))
      return self.answer(200, {f"{user(token)}/project": {'latest_output_datetime': '2026-11-12T10:00:00'}})
    if path != '/api/v1/user/me/':
      return super().do_GET()
    Handler.checked.append(token)
    if not token:
      return self.answer(200, {'is_authenticated': False, 'is_anonymous': True, 'is_active': False})
    if token not in TOKENS:
      return self.answer(401, {'error': "Unknown token: sign in again.", 'reason': 'invalid_token'})
    self.answer(200, {'is_authenticated': True, 'is_anonymous': False, 'is_active': True, 'user_name': user(token),
                      'token': {'id': list(TOKENS).index(token), 'kind': 'oauth', 'scopes': TOKENS[token], 'projects': [], 'expires_at': None}})


def user(token: str) -> str:
  return token.split('-')[0]


def free_socket() -> socket.socket:
  sock = socket.socket()
  sock.bind(('127.0.0.1', 0))
  return sock


@unittest.skipUnless(has_mcp, 'needs the mcp extra')
class TestHttpServer(unittest.TestCase):
  @classmethod
  def setUpClass(cls):
    import uvicorn
    from qaboard.mcp_http import create_app
    cls.qaboard = ThreadingHTTPServer(('127.0.0.1', 0), Handler)
    threading.Thread(target=cls.qaboard.serve_forever, daemon=True).start()
    cls.qaboard_url = f"http://127.0.0.1:{cls.qaboard.server_address[1]}"
    cls.socket = free_socket()
    cls.url = f"http://127.0.0.1:{cls.socket.getsockname()[1]}"
    # requests go to the fake QA-Board's address, links name the public one
    cls.uvicorn = uvicorn.Server(uvicorn.Config(create_app(cls.qaboard_url, PUBLIC_URL), log_level='warning'))
    cls.thread = threading.Thread(target=cls.uvicorn.run, kwargs={'sockets': [cls.socket]}, daemon=True)
    cls.thread.start()
    while not cls.uvicorn.started:
      cls.thread.join(0.05)

  @classmethod
  def tearDownClass(cls):
    cls.uvicorn.should_exit = True
    cls.thread.join(10)
    cls.socket.close()
    cls.qaboard.shutdown()
    cls.qaboard.server_close()

  def setUp(self):
    fake.Handler.requests_seen.clear()
    Handler.checked.clear()
    # this machine's credentials must never be used: a token in the environment, and one `qa auth login` saved
    patcher = mock.patch.dict(os.environ, {'QABOARD_TOKEN': 'machine-token', 'QABOARD_URL': self.qaboard_url})
    patcher.start()
    self.addCleanup(patcher.stop)

  def get(self, path: str, token: str | None = None):
    import httpx2
    headers = {'Authorization': f"Bearer {token}"} if token else {}
    return httpx2.get(f"{self.url}{path}", headers=headers)

  def post_initialize(self, token: str | None = None):
    """What Open WebUI sends first to discover the server's authorization (open_webui/utils/oauth.py)"""
    import httpx2
    headers = {'Content-Type': 'application/json', **({'Authorization': f"Bearer {token}"} if token else {})}
    return httpx2.post(f"{self.url}/mcp", headers=headers, json={'jsonrpc': '2.0', 'method': 'initialize', 'params': {}, 'id': 1})

  def session(self, token: str, work):
    """Runs `work(client)` with an MCP client over Streamable HTTP that sends `token`"""
    import httpx2
    from mcp import Client
    from mcp.client.streamable_http import streamable_http_client

    async def main():
      async with httpx2.AsyncClient(headers={'Authorization': f"Bearer {token}"}) as http:
        async with Client(streamable_http_client(f"{self.url}/mcp", http_client=http)) as client:
          return await work(client)
    return asyncio.run(main())

  def test_protected_resource_metadata_names_the_public_urls(self):
    expected = {'resource': f"{PUBLIC_URL}/mcp", 'authorization_servers': [PUBLIC_URL], 'scopes_supported': ['read']}
    for path in ('/.well-known/oauth-protected-resource/mcp', '/.well-known/oauth-protected-resource'):
      r = self.get(path)
      self.assertEqual(r.status_code, 200, path)
      self.assertLessEqual(expected.items(), r.json().items(), path)

  def test_without_a_token_401_says_where_the_metadata_is(self):
    r = self.post_initialize()
    self.assertEqual(r.status_code, 401)
    self.assertIn(f'resource_metadata="{PUBLIC_URL}/.well-known/oauth-protected-resource/mcp"', r.headers['WWW-Authenticate'])
    self.assertEqual(fake.Handler.requests_seen, [])

  def test_unknown_tokens_and_tokens_that_cant_read_are_refused(self):
    for token in ('act-only', 'not-a-token'):
      r = self.post_initialize(token)
      self.assertEqual(r.status_code, 401, token)
      self.assertIn('resource_metadata=', r.headers['WWW-Authenticate'])
    self.assertEqual(Handler.checked, ['act-only', 'not-a-token'])

  def test_answers_with_the_callers_token_and_public_links(self):
    async def work(client):
      tools = await client.list_tools()
      found = await client.call_tool('find_results', {'project': fake.PROJECT})
      compared = await client.call_tool('compare', {'url': f"{PUBLIC_URL}/{fake.PROJECT}/commit/{fake.NEW}?batch=default"})
      return tools, found, compared
    tools, found, compared = self.session('alice-token', work)
    names = {t.name for t in tools.tools}
    self.assertIn('compare', names)
    # it runs `qa gate` in a checkout: not remotely
    self.assertNotIn('check_gate', names)
    for result in (found, compared):
      self.assertFalse(result.is_error, result.content[0].text)
    self.assertIn(f"{PUBLIC_URL}/{fake.PROJECT}/commit/{fake.NEW}", json.dumps(found.structured_content))
    self.assertEqual(compared.structured_content['server'], PUBLIC_URL)
    self.assertIn(f"{PUBLIC_URL}/{fake.PROJECT}/commit/{fake.NEW}", json.dumps(compared.structured_content['links']))
    self.assertNotIn(self.qaboard_url, compared.content[0].text)
    seen = fake.Handler.requests_seen
    self.assertTrue(seen)
    self.assertEqual({authorization for *_, authorization in seen}, {'Bearer alice-token'})
    # one check for the whole session
    self.assertEqual(Handler.checked, ['alice-token'])

  def test_callers_dont_see_each_others_token(self):
    async def work(client):
      return await client.call_tool('find_results', {})

    async def together():
      # at the same time: each call is answered with its own token, so with the projects of its user only
      return await asyncio.gather(*(asyncio.to_thread(self.session, token, work) for token in tokens))
    tokens = ['alice-token', 'bob-token'] * 4
    for token, result in zip(tokens, asyncio.run(together())):
      self.assertFalse(result.is_error, result.content[0].text)
      other = 'bob' if user(token) == 'alice' else 'alice'
      self.assertIn(f"{user(token)}/project", result.content[0].text)
      self.assertNotIn(f"{other}/project", result.content[0].text)
    seen = {authorization for *_, authorization in fake.Handler.requests_seen}
    self.assertEqual(seen, {'Bearer alice-token', 'Bearer bob-token'})

  def test_urls_of_other_servers_are_refused(self):
    async def work(client):
      return await client.call_tool('compare', {'url': f"https://other.example.com/{fake.PROJECT}/commit/{fake.NEW}"})
    with mock.patch('qaboard.evidence.find_login', return_value=(None, {'token': 'saved-login'})):
      result = self.session('alice-token', work)
    self.assertTrue(result.is_error)
    self.assertIn('only reads the QA-Board it belongs to', result.content[0].text)
    self.assertEqual(fake.Handler.requests_seen, [])

  def test_health(self):
    self.assertEqual(self.get('/health').text, 'ok')


@unittest.skipUnless(has_mcp, 'needs the mcp extra')
class TestTokens(unittest.TestCase):
  def test_a_tool_call_without_a_token_is_an_error_not_a_fallback(self):
    from qaboard.mcp_http import MissingToken, caller_api
    with mock.patch.dict(os.environ, {'QABOARD_TOKEN': 'machine-token'}):
      with self.assertRaises(MissingToken):
        caller_api('http://qaboard', PUBLIC_URL)

  def test_access_tokens(self):
    from qaboard.mcp_http import access_token
    me = {'is_authenticated': True, 'user_name': 'alice', 'token': {'id': 3, 'scopes': ['read'], 'expires_at': '2030-01-01T00:00:00Z'}}
    access = access_token('t', me)
    assert access is not None
    self.assertEqual((access.subject, access.scopes, access.expires_at), ('alice', ['read'], 1893456000))
    self.assertIsNone(access_token('t', {**me, 'token': {'id': 3, 'scopes': ['act']}}))
    # a session, not a token: no scopes to check
    self.assertIsNone(access_token('t', {'is_authenticated': True, 'user_name': 'alice'}))
    self.assertIsNone(access_token('t', {'is_authenticated': False}))

  def test_answers_are_cached_by_the_tokens_hash_for_a_while(self):
    from qaboard.mcp_http import QABoardTokens
    tokens = QABoardTokens('http://qaboard', ttl=60)
    me = {'is_authenticated': True, 'user_name': 'alice', 'token': {'id': 1, 'scopes': ['read']}}
    with mock.patch('qaboard.mcp_http.request_json', return_value=me) as asked:
      for _ in range(3):
        self.assertIsNotNone(asyncio.run(tokens.verify_token('secret')))
    self.assertEqual(asked.call_count, 1)
    self.assertNotIn('secret', repr(tokens._answers))
    tokens.ttl = 0
    with mock.patch('qaboard.mcp_http.request_json', return_value=me) as asked:
      asyncio.run(tokens.verify_token('other'))
      asyncio.run(tokens.verify_token('other'))
    self.assertEqual(asked.call_count, 2)

  def test_qaboard_down_is_not_a_bad_token(self):
    from qaboard.http_client import ResultsError
    from qaboard.mcp_http import QABoardTokens
    tokens = QABoardTokens('http://qaboard')
    with mock.patch('qaboard.mcp_http.request_json', side_effect=ResultsError('502', status=502)):
      with self.assertRaises(ResultsError):
        asyncio.run(tokens.verify_token('secret'))
    self.assertEqual(tokens._answers, {})


@unittest.skipUnless(has_mcp, 'needs the mcp extra')
class TestCommand(unittest.TestCase):
  def test_http_options_need_http(self):
    from click.testing import CliRunner
    from qaboard.mcp_server import mcp_command
    result = CliRunner().invoke(mcp_command, ['--public-url', PUBLIC_URL])
    self.assertEqual(result.exit_code, 2)
    self.assertIn('options of --http', result.output)

  def test_http_serves_with_the_options(self):
    from click.testing import CliRunner
    from qaboard.mcp_server import mcp_command
    with mock.patch('qaboard.mcp_http.serve') as serve:
      result = CliRunner().invoke(mcp_command, ['--http', '--server', 'http://proxy', '--public-url', PUBLIC_URL, '--host', '0.0.0.0'])
    self.assertEqual(result.exit_code, 0, result.output)
    serve.assert_called_once_with('http://proxy', PUBLIC_URL, host='0.0.0.0', port=8000)


if __name__ == '__main__':
  unittest.main()
