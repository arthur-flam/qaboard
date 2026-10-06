"""
A scripted LLM for videos: an Anthropic-compatible Messages API that answers with a written conversation.

The video shows the real `qa wizard` and its real AI assistant code, talking to this server instead of a real model:
the result is deterministic, costs nothing, and the integration it writes is a reviewed, working one.
(Run the video with a real model instead by pointing QABOARD_LLM_* at it: the storyboard's prompts still work,
only the conversation will differ.)

A conversation script (YAML) lists the assistant's turns; each turn is a list of steps, each step one model
response: tool calls (run by the wizard, for real), or the final Markdown answer:

    turns:
      - - think: 1.2                      # seconds before the response, to see the spinner
          tools:
            - [list_files, {}]
            - [read_file, {path: denoise.py}]
        - tools:
            - edits: reference/edits.json  # expands to edit_file / write_file calls
        - say: |
            I wired `qa/main.py` to `denoise.py`...
"""
import json
import ssl
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any, Dict, List, Optional

import yaml


def load_conversation(path: Path) -> List[List[Dict[str, Any]]]:
  """The turns, with `edits:` references expanded to the wizard's edit_file / write_file tool calls."""
  script = yaml.safe_load(path.read_text())
  turns = []
  for turn in script['turns']:
    steps = []
    for step in turn:
      tools = []
      for call in step.get('tools', []):
        if isinstance(call, dict) and 'edits' in call:
          tools += edits_to_calls(json.loads((path.parent / call['edits']).read_text()))
        else:
          name, args = call
          tools.append([name, args])
      steps.append({**step, 'tools': tools})
    turns.append(steps)
  return turns


def edits_to_calls(edits: Any) -> List[List[Any]]:
  """
  edits.json: a list of {path, old_text, new_text} or {path, content}. Edits marked `optional` are skipped:
  they're for answers the user already gave the wizard (in the video, the inputs folder).
  """
  calls = []
  for edit in edits if isinstance(edits, list) else edits.get('edits', []):
    if edit.get('optional'):
      continue
    if 'content' in edit:
      calls.append(['write_file', {'path': edit['path'], 'content': edit['content']}])
    else:
      calls.append(['edit_file', {'path': edit['path'], 'old_text': edit['old_text'], 'new_text': edit['new_text']}])
  return calls


class FakeLLM:
  def __init__(self, conversation: List[List[Dict[str, Any]]], models=('claude-opus-5-5', 'claude-sonnet-5-5', 'claude-haiku-4-5'),
               host: str = '127.0.0.1', port: int = 0, certfile: Optional[Path] = None, keyfile: Optional[Path] = None):
    self.conversation = conversation
    self.requests: List[Dict[str, Any]] = []
    fake = self

    class Handler(BaseHTTPRequestHandler):
      def log_message(self, *args):
        pass

      def send_json(self, body: Dict[str, Any]):
        data = json.dumps(body).encode()
        self.send_response(200)
        self.send_header('Content-Type', 'application/json')
        self.send_header('Content-Length', str(len(data)))
        self.end_headers()
        self.wfile.write(data)

      def do_GET(self):
        listed = [{'id': m, 'type': 'model', 'display_name': m, 'created_at': '2026-01-01T00:00:00Z'} for m in models]
        self.send_json({'data': listed, 'has_more': False, 'first_id': listed[0]['id'], 'last_id': listed[-1]['id']})

      def do_POST(self):
        body = json.loads(self.rfile.read(int(self.headers['Content-Length'])))
        fake.requests.append(body)
        step = fake.next_step(body['messages'])
        time.sleep(float(step.get('think', 0.8)))
        if step.get('tools'):
          content = [{'type': 'tool_use', 'id': f'toolu_{len(fake.requests)}_{i}', 'name': name, 'input': args}
                     for i, (name, args) in enumerate(step['tools'])]
          stop_reason = 'tool_use'
        else:
          content, stop_reason = [{'type': 'text', 'text': step.get('say', 'Done.')}], 'end_turn'
        message = {'id': f'msg_{len(fake.requests)}', 'type': 'message', 'role': 'assistant', 'model': body['model'],
                   'content': content, 'stop_reason': stop_reason, 'stop_sequence': None,
                   'usage': {'input_tokens': 2400 + 900 * len(body['messages']), 'output_tokens': 180 + 60 * len(content)}}
        if not body.get('stream'):
          self.send_json(message)
          return
        events = [('message_start', {'type': 'message_start', 'message': {**message, 'content': [], 'stop_reason': None}})]
        for i, block in enumerate(content):
          if block['type'] == 'text':
            start, delta = {'type': 'text', 'text': ''}, {'type': 'text_delta', 'text': block['text']}
          else:
            start, delta = {**block, 'input': {}}, {'type': 'input_json_delta', 'partial_json': json.dumps(block['input'])}
          events += [('content_block_start', {'type': 'content_block_start', 'index': i, 'content_block': start}),
                     ('content_block_delta', {'type': 'content_block_delta', 'index': i, 'delta': delta}),
                     ('content_block_stop', {'type': 'content_block_stop', 'index': i})]
        events += [('message_delta', {'type': 'message_delta', 'delta': {'stop_reason': stop_reason, 'stop_sequence': None},
                                      'usage': {'output_tokens': message['usage']['output_tokens']}}),
                   ('message_stop', {'type': 'message_stop'})]
        data = ''.join(f"event: {name}\ndata: {json.dumps(payload)}\n\n" for name, payload in events).encode()
        self.send_response(200)
        self.send_header('Content-Type', 'text/event-stream')
        self.send_header('Content-Length', str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    self.server = ThreadingHTTPServer((host, port), Handler)
    if certfile:
      context = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
      context.load_cert_chain(certfile, keyfile)
      self.server.socket = context.wrap_socket(self.server.socket, server_side=True)
    self.port = self.server.server_address[1]
    threading.Thread(target=self.server.serve_forever, daemon=True).start()

  def next_step(self, messages: List[Dict[str, Any]]) -> Dict[str, Any]:
    """Which step of which turn: user messages that aren't tool results start turns, assistant messages are steps."""
    turn, step = -1, 0
    for message in messages:
      content = message['content']
      is_tool_result = isinstance(content, list) and content and all(c.get('type') == 'tool_result' for c in content)
      if message['role'] == 'user' and not is_tool_result:
        turn, step = turn + 1, 0
      elif message['role'] == 'assistant':
        step += 1
    turns = self.conversation
    if turn >= len(turns) or step >= len(turns[turn]):
      return {'say': "Done. Anything else?", 'think': 0.5}
    return turns[turn][step]

  def close(self):
    self.server.shutdown()
    self.server.server_close()


def self_signed_cert(hostname: str, directory: Path):
  """A certificate for a pretend company gateway (llm.acme.internal), trusted with QABOARD_LLM_VERIFY."""
  import subprocess
  directory.mkdir(parents=True, exist_ok=True)
  cert, key = directory / f'{hostname}.pem', directory / f'{hostname}.key'
  if not cert.exists():
    subprocess.run(['openssl', 'req', '-x509', '-newkey', 'rsa:2048', '-nodes', '-days', '3650', '-subj', f'/CN={hostname}',
                    '-addext', f'subjectAltName=DNS:{hostname}', '-keyout', str(key), '-out', str(cert)],
                   check=True, capture_output=True)
  return cert, key
