"""
Serves a test MCP Apps host (host.html: the official AppBridge of @modelcontextprotocol/ext-apps), the app of `qa mcp`
(qaboard/mcp_app.html), and /call: the tools of `qa mcp`, in-process, against the fake QA-Board (tests/fake_qaboard.py).
check.js drives it with Playwright. See README.md.
"""
import sys, json, asyncio, threading
from pathlib import Path
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / 'tests'))
import fake_qaboard as fake
from mcp import Client
from qaboard.mcp_server import create_server

HERE = Path(__file__).parent
_, FAKE = fake.start()
loop = asyncio.new_event_loop()
threading.Thread(target=loop.run_forever, daemon=True).start()

async def call(name, args):
  async with Client(create_server(FAKE)) as c:
    r = await c.call_tool(name, args)
    return r.model_dump(by_alias=True, exclude_none=True, mode='json')

class H(BaseHTTPRequestHandler):
  def log_message(self, *a): pass
  def send(self, body, ctype):
    self.send_response(200); self.send_header('Content-Type', ctype); self.send_header('Content-Length', str(len(body))); self.end_headers(); self.wfile.write(body)
  def do_GET(self):
    path = self.path.split('?')[0]
    if path == '/app.html':
      return self.send((ROOT / 'qaboard' / 'mcp_app.html').read_bytes(), 'text/html')
    if path == '/fake':
      return self.send(FAKE.encode(), 'text/plain')
    f = HERE / path.lstrip('/')
    self.send(f.read_bytes(), 'text/html' if f.suffix == '.html' else 'application/javascript')
  def do_POST(self):
    body = json.loads(self.rfile.read(int(self.headers['Content-Length'])))
    result = asyncio.run_coroutine_threadsafe(call(body['name'], body.get('arguments') or {}), loop).result(60)
    self.send(json.dumps(result).encode(), 'application/json')

server = ThreadingHTTPServer(('127.0.0.1', int(sys.argv[1]) if len(sys.argv) > 1 else 5199), H)
print('ready', FAKE, flush=True)
server.serve_forever()
