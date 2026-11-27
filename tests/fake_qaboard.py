"""
A fake QA-Board server for the tests of `qa mcp` (tests/test_mcp_server.py): the endpoints it reads, answering like
the real ones (backend/backend/api/), for one project with a commit compared with its reference.

    python tests/fake_qaboard.py 5152   # to try `qa mcp --server http://localhost:5152` by hand
"""
import json
import struct
import sys
import threading
import zlib
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import urlsplit, parse_qs, unquote

PROJECT = 'group/repo'
NEW = 'a1b2c3d4' + '0' * 32
REF = 'f9e8d7c6' + '0' * 32
OLDER = '11112222' + '0' * 32


def png(width: int, height: int, value: int = 128) -> bytes:
  """A gray PNG, without Pillow"""
  raw = b''.join(b'\x00' + bytes([value]) * width for _ in range(height))
  def chunk(kind, data):
    return struct.pack('>I', len(data)) + kind + data + struct.pack('>I', zlib.crc32(kind + data) & 0xffffffff)
  return b'\x89PNG\r\n\x1a\n' + chunk(b'IHDR', struct.pack('>IIBBBBB', width, height, 8, 0, 0, 0, 0)) + chunk(b'IDAT', zlib.compress(raw)) + chunk(b'IEND', b'')


def output(id, input, metrics, is_failed=False, commit=NEW, configurations=('base',), data=None):
  return {
    'id': id, 'output_type': '', 'platform': 'linux', 'configurations': list(configurations), 'extra_parameters': {},
    'metrics': metrics, 'is_failed': is_failed, 'is_pending': False, 'is_running': False, 'data': data or {}, 'deleted': False,
    'created_date': '2026-11-10T10:00:00', 'output_dir_url': f"/s/mnt/qaboard/{commit[:8]}/{input.replace('/', '_')}",
    'test_input_database': '/data', 'test_input_path': input, 'test_input_metadata': {},
  }


NEW_OUTPUTS = {
  1: output(1, 'night/01', {'psnr': 30.0, 'latency_ms': 10.0}),
  2: output(2, 'night/02', {'psnr': 25.0, 'latency_ms': 11.0}),
  3: output(3, 'day/01', {'psnr': 35.0, 'latency_ms': 9.0}),
  4: output(4, 'day/02', {'psnr': 36.0, 'latency_ms': 9.5}),
  5: output(5, 'crash/01', {}, is_failed=True, data={'previous_attempts': [{'attempt': 1, 'status': 'failed'}]}),
  6: output(6, 'crash/02', {}, is_failed=True),
  7: output(7, 'oom/01', {}, is_failed=True),
}
REF_OUTPUTS = {
  11: output(11, 'night/01', {'psnr': 29.0, 'latency_ms': 10.0}, commit=REF),
  12: output(12, 'night/02', {'psnr': 28.0, 'latency_ms': 10.5}, commit=REF),
  13: output(13, 'day/01', {'psnr': 34.0, 'latency_ms': 9.0}, commit=REF),
  14: output(14, 'day/02', {'psnr': 35.0, 'latency_ms': 9.5}, commit=REF),
  15: output(15, 'crash/01', {'psnr': 20.0}, commit=REF),
  16: output(16, 'crash/02', {}, is_failed=True, commit=REF),
  17: output(17, 'oom/01', {'psnr': 20.0}, commit=REF),
}
LOGS = {
  5: "loading model\nTraceback (most recent call last):\n  File \"/src/run.py\", line 12, in run\nValueError: bad shape (3, 480, 640)\n[ERROR] Failed run! More info at: /mnt/x/log.txt\n",
  6: "loading model\nTraceback (most recent call last):\n  File \"/src/run.py\", line 12, in run\nValueError: bad shape (3, 720, 1280)\n",
  7: "\x1b[31mloading model\x1b[0m\nKilled: out of memory\n",
}
FIXTURES = Path(__file__).resolve().parent / 'fixtures'


def fixture(path: str) -> str:
  return (FIXTURES / path).read_text(encoding='utf-8')


def benchmarks_of_new() -> str:
  """The reference's benchmarks, one slower, one faster, one more"""
  data = json.loads(fixture('benchmarks/kitefin.benchmarks.json'))
  render, ingest, _ = data['benchmarks']
  render.update(value=5.0, stats={**render['stats'], 'median': 5.0})
  ingest.update(value=200000, stats={**ingest['stats'], 'mean': 200000})
  data['benchmarks'].append({'name': 'export/png', 'value': 1.5, 'unit': 'ms'})
  return json.dumps(data)


def profile_of_new() -> str:
  """The reference's profile, with parse() 3 times slower"""
  lines = []
  for line in fixture('flamegraphs/py-spy.folded').splitlines():
    stack, _, count = line.rpartition(' ')
    lines.append(f"{stack} {int(count) * 3}" if 'parse (gateway.py)' in stack else line)
  return '\n'.join(lines) + '\n'


def series_of_reference() -> str:
  """The new run's series, with a higher loss"""
  data = json.loads(fixture('series/training.expected.json'))
  loss = data['series']['train/loss']
  loss['value'] = [None if v is None else v * 1.5 for v in loss['value']]
  return json.dumps(data)


JUNIT_REFERENCE = """<?xml version="1.0" encoding="utf-8"?>
<testsuite name="pytest">
  <testcase classname="tests.codec.test_crc" name="test_known" time="0.010"><failure message="x">was broken</failure></testcase>
  <testcase classname="tests.codec.test_crc" name="test_marked_flaky" time="0.200"/>
  <testcase classname="tests.codec.test_varint" name="test_passes_alone" time="0.300"/>
  <testcase classname="tests.codec.test_varint" name="test_still_fails" time="0.400"><failure message="assert 3 == 4">always</failure></testcase>
  <testcase classname="tests.client.test_retry" name="test_error_then_fails" time="0.500"/>
  <testcase classname="tests.client.test_retry" name="test_skipped" time="0"><skipped message="later"/></testcase>
  <testcase classname="tests.old" name="test_gone" time="0.1"/>
</testsuite>
"""


def figure(recall) -> str:
  return json.dumps({'data': [{'name': 'recall', 'type': 'scatter', 'x': [0, 1, 2], 'y': recall}, {'type': 'bar', 'y': [3, 4]}],
                     'layout': {'title': {'text': 'Recall per epoch'}}})


FILES = {
  'output.png': {'st_size': 1000, 'md5': 'aaa'},
  'detections.json': {'st_size': 100, 'md5': 'bbb'},
  'same.txt': {'st_size': 10, 'md5': 'same'},
  'kitefin.benchmarks.json': {'st_size': 900, 'md5': 'ccc'},
  'cpu.folded': {'st_size': 1400, 'md5': 'ddd'},
  'profile.speedscope.json': {'st_size': 7800, 'md5': 'eee'},
  'series.json': {'st_size': 1200, 'md5': 'fff'},
  'junit.xml': {'st_size': 1000, 'md5': 'ggg'},
  'media.jsonl': {'st_size': 3000, 'md5': 'hhh'},
  'figure.plotly.json': {'st_size': 200, 'md5': 'iii'},
  # a type given in qaboard.yaml (PROJECT_DATA)
  'timings.txt': {'st_size': 700, 'md5': 'jjj'},
  # like real manifests (outputs_manifest in qaboard/utils.py), without log.txt
}
# (the new runs', the reference runs')
TEXTS = {
  'detections.json': ('{"boxes": 3,\n "score": 0.9}\n', '{"boxes": 2,\n "score": 0.9}\n'),
  'same.txt': ('same\n', 'same\n'),
  'kitefin.benchmarks.json': (benchmarks_of_new(), fixture('benchmarks/kitefin.benchmarks.json')),
  'cpu.folded': (profile_of_new(), fixture('flamegraphs/py-spy.folded')),
  'profile.speedscope.json': (fixture('flamegraphs/py-spy.speedscope.json'),) * 2,
  'series.json': (fixture('series/training.expected.json'), series_of_reference()),
  'junit.xml': (fixture('junit/junit.xml'), JUNIT_REFERENCE),
  # the reference logged fewer steps
  'media.jsonl': (fixture('media/run/media.jsonl'), ''.join(line for line in fixture('media/run/media.jsonl').splitlines(True) if '"_step":20' not in line)),
  'figure.plotly.json': (figure([0.5, 0.6, 0.7]), figure([0.5, 0.6, 0.65])),
  'timings.txt': (fixture('benchmarks/go-bench.txt'),) * 2,
}
PROJECT_DATA = {
  'qatools_metrics': {'main_metrics': ['psnr'], 'available_metrics': {'psnr': {'smaller_is_better': False, 'suffix': ' dB'}}},
  'qatools_config': {'project': {'reference_branch': 'main'},
                     'outputs': {'visualizations': [{'name': 'Timings', 'path': 'timings.txt', 'type': 'benchmarks/go'},
                                                    {'name': 'Frames', 'path': 'frames/:frame/depth.bin', 'type': 'pointcloud/pcd'}]}},
}


def cell(new, ref):
  if new is None or ref is None:
    return {'new': new, 'ref': None, 'delta': None, 'delta_relative': None} if new is not None else None
  return {'new': new, 'ref': ref, 'delta': new - ref, 'delta_relative': (new - ref) / (abs(ref) + 0.00001)}


def compare_answer(reference=None):
  pairs = {1: 11, 2: 12, 3: 13, 4: 14, 5: 15, 6: 16, 7: 17}
  tests = []
  for id, o in NEW_OUTPUTS.items():
    ref = REF_OUTPUTS[pairs[id]]
    cells = {k: c for k in ('psnr', 'latency_ms') if (c := cell(o['metrics'].get(k), ref['metrics'].get(k)))}
    tests.append({'id': id, 'reference_id': pairs[id], 'reference_mismatch': None, 'test_input_path': o['test_input_path'],
                  'test_input_database': '/data', 'label': None, 'platform': 'linux', 'configurations': o['configurations'],
                  'extra_parameters': {}, 'is_failed': o['is_failed'], 'batch': 'ci', 'metrics': cells, 'bit_accurate': None})
  return {
    'project': PROJECT, 'status': 'complete', 'messages': [],
    'commit': {'id': NEW, 'message': 'Faster denoiser\n\nbody', 'committer_name': 'agent', 'branch': 'feature', 'batch': 'default',
               'runs': {'total': 7, 'valid': 4, 'failed': 3, 'pending': 0, 'running': 0}, 'reruns': {'runs': 1, 'after_failure': 0, 'after_unfinished': 0}},
    'reference': {'requested': reference or 'main', 'type': 'branch', 'milestone': None, 'id': REF, 'branch': 'main', 'message': 'Base', 'batch': 'default',
                  'runs': {'total': 7, 'valid': 6, 'failed': 1, 'pending': 0, 'running': 0}, 'gate': None},
    'metrics': {
      'psnr': {'label': 'PSNR', 'short_label': 'PSNR', 'suffix': ' dB', 'scale': 1.0, 'smaller_is_better': False, 'target': 30, 'declared': True,
               'new': {'median': 30.0, 'count': 4, 'pass_rate': 0.75}, 'ref': {'median': 31.5, 'count': 4, 'pass_rate': 0.5},
               'delta': -1.5, 'delta_relative': -1.5 / 31.5},
      'latency_ms': {'label': 'latency', 'short_label': 'latency', 'suffix': ' ms', 'scale': 1.0, 'smaller_is_better': None, 'target': None, 'declared': False,
                     'new': {'median': 9.75, 'count': 4, 'pass_rate': None}, 'ref': {'median': 9.75, 'count': 4, 'pass_rate': None},
                     'delta': 0.0, 'delta_relative': 0.0},
    },
    'tests': tests, 'bit_accuracy': None, 'project_gate': None,
  }


def commit_answer(sha, outputs):
  failed = sum(1 for o in outputs.values() if o['is_failed'])
  return {'id': sha, 'branch': 'feature' if sha == NEW else 'main', 'message': 'msg', 'committer_name': 'someone', 'authored_datetime': '2026-11-10T10:00:00',
          'batches': {'default': {'id': 1, 'label': 'default', 'origin': 'ci', 'user_name': 'ci', 'valid_outputs': len(outputs) - failed,
                                  'failed_outputs': failed, 'pending_outputs': 0, 'running_outputs': 0, 'aggregated_metrics': {},
                                  'outputs': {str(k): v for k, v in outputs.items()}}}}


def history_answer():
  commits = []
  for i, (sha, psnr) in enumerate(((NEW, 30.0), (REF, 31.5), (OLDER, 31.4))):
    commits.append({'id': sha, 'branch': 'main', 'message': f"commit {i}", 'committer_name': 'someone', 'authored_datetime': f"2026-11-{12 - i:02d}T10:00:00",
                    'batches': {'nightly': {'label': 'nightly', 'valid_outputs': 6, 'failed_outputs': int(i == 0), 'pending_outputs': 0, 'running_outputs': 0,
                                            'aggregated_metrics': {'psnr_median': psnr, 'psnr_average': psnr}}}})
  return commits


class Handler(BaseHTTPRequestHandler):
  requests_seen: list = []

  def log_message(self, *args):
    pass

  def answer(self, status, body, content_type='application/json'):
    data = body if isinstance(body, bytes) else json.dumps(body).encode()
    self.send_response(status)
    self.send_header('Content-Type', content_type)
    self.send_header('Content-Length', str(len(data)))
    self.end_headers()
    self.wfile.write(data)

  def do_POST(self):
    parts = urlsplit(self.path)
    body = json.loads(self.rfile.read(int(self.headers.get('Content-Length') or 0)) or b'{}')
    Handler.requests_seen.append(('POST', parts.path, body, self.headers.get('Authorization')))
    if parts.path == '/api/v1/output/image/diff':
      return self.answer(200, [{'x': 10, 'y': 20, 'w': 30, 'h': 40, 'peak_x': 20, 'peak_y': 30, 'value': 0.4}])
    self.answer(404, {'error': 'not found'})

  def do_GET(self):
    parts = urlsplit(self.path)
    path, query = unquote(parts.path), {k: v[0] for k, v in parse_qs(parts.query).items()}
    Handler.requests_seen.append(('GET', parts.path, query, self.headers.get('Authorization')))
    if path == '/api/v1/config':
      return self.answer(200, {'image_servers': {'default': '/iiif/2/'}})
    if path == '/api/v1/project':
      return self.answer(200, PROJECT_DATA)
    if path == '/api/v1/projects':
      return self.answer(200, {PROJECT: {'latest_output_datetime': '2026-11-12T10:00:00'}, 'other/old': {'latest_output_datetime': '2025-01-01T00:00:00'}})
    if path == '/api/v1/milestones':
      return self.answer(200, {'milestones': [{'id': 1, 'label': 'v1.0', 'shared': True}, {'id': 2, 'label': 'mine', 'shared': False}], 'favorite_groups': []})
    if path.startswith('/api/v1/commits'):
      if query.get('batch') == 'nightly':
        return self.answer(200, history_answer())
      return self.answer(200, [commit_answer(NEW, NEW_OUTPUTS)])
    if path == f"/api/v1/commit/{NEW}/compare":
      # like the server: the reference's runs have the same label, unless asked otherwise. main only has CI runs
      if (query.get('reference_batch') or query.get('batch', 'default')) not in ('default', 'ci'):
        return self.answer(200, {**compare_answer(query.get('reference')), 'status': 'missing', 'tests': [],
                                 'messages': [f"No reference: no milestone or commit \"main\", and no commit on the branch \"main\" has runs in the batch \"{query.get('reference_batch') or query['batch']}\"."],
                                 'reference': {'requested': 'main', 'type': 'branch', 'milestone': None, 'id': None, 'batch': query['batch'], 'runs': {}, 'gate': None}})
      return self.answer(200, compare_answer(query.get('reference')))
    if path in ('/api/v1/commit', '/api/v1/commit/feature') or path == f"/api/v1/commit/{NEW}":
      return self.answer(200, commit_answer(NEW, NEW_OUTPUTS))
    if path == f"/api/v1/commit/{REF}":
      return self.answer(200, commit_answer(REF, REF_OUTPUTS))
    if path.startswith('/api/v1/output/'):
      rest = path[len('/api/v1/output/'):].strip('/')
      run_id, _, what = rest.partition('/')
      run = NEW_OUTPUTS.get(int(run_id)) or REF_OUTPUTS.get(int(run_id))
      if not run:
        return self.answer(404, {'error': f"Cannot find output {run_id}"})
      if what == 'manifest':
        # the same files in every run, with different contents except same.txt
        return self.answer(200, {name: {**meta, 'md5': meta['md5'] if name == 'same.txt' else f"{meta['md5']}{run_id}"} for name, meta in FILES.items()})
      sha = NEW if int(run_id) in NEW_OUTPUTS else REF
      return self.answer(200, {**run, 'project': PROJECT, 'commit_id': sha, 'batch_label': 'default', 'batch_id': 1})
    if path.startswith('/s/'):
      run = next((o for o in [*NEW_OUTPUTS.values(), *REF_OUTPUTS.values()] if path.startswith(o['output_dir_url'] + '/')), None)
      if run is None:
        return self.answer(404, b'', 'text/plain')
      name = path[len(run['output_dir_url']) + 1:]
      if name == 'log.txt':
        return self.answer(200, LOGS.get(run['id'], 'ok\n').encode(), 'text/plain')
      if name in TEXTS:
        return self.answer(200, TEXTS[name][0 if run['id'] < 10 else 1].encode(), 'text/plain')
      return self.answer(404, b'', 'text/plain')
    if path.startswith('/iiif/2/'):
      if path.endswith('/info.json'):
        return self.answer(200, {'width': 640, 'height': 480})
      return self.answer(200, png(8, 6), 'image/png')
    self.answer(404, {'error': f"no route {path}"})


def start(port: int = 0):
  server = ThreadingHTTPServer(('127.0.0.1', port), Handler)
  threading.Thread(target=server.serve_forever, daemon=True).start()
  return server, f"http://127.0.0.1:{server.server_address[1]}"


if __name__ == '__main__':
  server, url = start(int(sys.argv[1]) if len(sys.argv) > 1 else 5152)
  print(url)
  threading.Event().wait()
