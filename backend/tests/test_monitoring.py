"""
Tests for backend/monitoring.py: /metrics for Prometheus. Who can read it, what the requests are labelled with,
how the processes of a container add up (forked processes here, a real uWSGI with several workers at the end),
and the cache of the metrics that need the database.
"""
import json
import os
import re
import shutil
import signal
import socket
import subprocess
import sys
import textwrap
import threading
import time
import urllib.error
import urllib.request
from pathlib import Path
from types import SimpleNamespace

import pytest
from flask import Flask
from prometheus_client.parser import text_string_to_metric_families

from backend import monitoring

BACKEND_DIR = Path(__file__).resolve().parent.parent


@pytest.fixture
def metrics(monkeypatch, tmp_path):
  """Metrics on, in a folder of the test; this process exports nothing until it says so (serve_as)"""
  for name in list(os.environ):
    if name.startswith('QABOARD_METRICS'):
      monkeypatch.delenv(name)
  monkeypatch.setenv('QABOARD_METRICS', '1')
  monkeypatch.setenv('QABOARD_METRICS_DIR', str(tmp_path / 'metrics'))
  monkeypatch.setattr(monitoring, '_role', None)
  monkeypatch.setattr(monitoring, '_uwsgi', None)
  monkeypatch.setattr(monitoring, '_stats', monitoring.Stats(lambda: [], directory=tmp_path / 'metrics'))
  monkeypatch.setattr(monitoring, '_projects', set())
  monitoring.reset_files()
  yield tmp_path / 'metrics'
  monitoring.reset_files()


def samples(text=None) -> dict:
  """{(sample name, sorted labels): value} of the rendered metrics"""
  text = monitoring.render().decode() if text is None else text
  found = {}
  for family in text_string_to_metric_families(text):
    for sample in family.samples:
      found[(sample.name, tuple(sorted(sample.labels.items())))] = sample.value
  return found


def value(name, text=None, **labels):
  return samples(text).get((name, tuple(sorted(labels.items()))))


def environ(address='10.1.2.3', **extra):
  return {'REMOTE_ADDR': address, **extra}


# --- Who can read the metrics ---

def test_off_by_default(monkeypatch):
  monkeypatch.delenv('QABOARD_METRICS', raising=False)
  assert monitoring.refusal(environ('127.0.0.1')) == (404, "Not found")
  app = Flask(__name__)
  monitoring.register(app)
  # no route, no hooks: /metrics is a 404 like any unknown page
  assert app.test_client().get('/metrics').status_code == 404
  assert not app.before_request_funcs and not app.after_request_funcs
  assert monitoring.security_item() is None
  assert monitoring.process_identity() is None


@pytest.mark.parametrize('address, allowed', [
  ('127.0.0.1', True), ('10.42.0.7', True), ('172.20.0.3', True), ('192.168.1.5', True), ('100.64.3.2', True),
  ('::1', True), ('fd00::12', True), ('::ffff:10.0.0.1', True),
  ('8.8.8.8', False), ('172.32.0.1', False), ('2001:db8::1', False), ('', False), ('not-an-address', False),
])
def test_without_a_token_only_private_networks(metrics, address, allowed):
  assert (monitoring.refusal(environ(address)) is None) == allowed


@pytest.mark.parametrize('proxy_key', ['DOCUMENT_ROOT', 'HTTP_X_FORWARDED_PROTO', 'HTTP_X_FORWARDED_FOR', 'HTTP_X_REAL_IP', 'HTTP_FORWARDED'])
def test_never_through_the_proxy_without_a_token(metrics, proxy_key):
  # nginx passes the client's address: a private one says nothing about who asks
  status, reason = monitoring.refusal(environ('10.0.0.1', **{proxy_key: ''}))
  assert status == 403 and 'QABOARD_METRICS_TOKEN' in reason


def test_token(metrics, monkeypatch):
  monkeypatch.setenv('QABOARD_METRICS_TOKEN', 's3cret')
  assert monitoring.refusal(environ('10.0.0.1'))[0] == 401
  assert monitoring.refusal(environ('10.0.0.1', HTTP_AUTHORIZATION='Bearer wrong'))[0] == 401
  assert monitoring.refusal(environ('10.0.0.1', HTTP_AUTHORIZATION='Basic s3cret'))[0] == 401
  assert monitoring.refusal(environ('10.0.0.1', HTTP_AUTHORIZATION='Bearer s3cret')) is None
  # with the token: from anywhere, also through a proxy an admin configured for it
  assert monitoring.refusal(environ('8.8.8.8', HTTP_AUTHORIZATION='bearer s3cret', HTTP_X_FORWARDED_PROTO='https')) is None
  # ...unless networks are given too
  monkeypatch.setenv('QABOARD_METRICS_ALLOWED_NETWORKS', '10.0.0.0/8')
  assert monitoring.refusal(environ('8.8.8.8', HTTP_AUTHORIZATION='Bearer s3cret'))[0] == 403
  assert monitoring.refusal(environ('10.9.9.9', HTTP_AUTHORIZATION='Bearer s3cret')) is None


def test_allowed_networks(metrics, monkeypatch):
  monkeypatch.setenv('QABOARD_METRICS_ALLOWED_NETWORKS', '192.0.2.0/24, 198.51.100.7, nonsense')
  assert monitoring.refusal(environ('192.0.2.9')) is None
  assert monitoring.refusal(environ('198.51.100.7')) is None
  assert monitoring.refusal(environ('198.51.100.8'))[0] == 403
  # the default isn't added to the list
  assert monitoring.refusal(environ('10.0.0.1'))[0] == 403
  monkeypatch.setenv('QABOARD_METRICS_ALLOWED_NETWORKS', 'any')
  assert monitoring.refusal(environ('8.8.8.8')) is None


def test_security_checklist_item(metrics, monkeypatch):
  item = monitoring.security_item()
  assert not item['ok'] and item['severity'] == 'info' and 'private network' in item['detail']
  monkeypatch.setenv('QABOARD_METRICS_ALLOWED_NETWORKS', 'any')
  assert monitoring.security_item()['severity'] == 'warning'
  monkeypatch.setenv('QABOARD_METRICS_TOKEN', 's3cret')
  assert monitoring.security_item()['ok']


# --- The endpoint, and what requests are labelled with ---

@pytest.fixture
def app(metrics):
  app = Flask(__name__)
  monitoring.register(app)
  authenticated = []
  app.authenticated = authenticated

  @app.before_request
  def authenticate():
    # like backend/actor.py: a bearer token that isn't a user's is refused, whatever the page
    authenticated.append(True)
    from flask import request
    if request.headers.get('Authorization'):
      return "This token is not valid.", 401

  @app.route('/api/v1/output/<int:output_id>', methods=['GET', 'POST'])
  @app.route('/api/v1/output/<int:output_id>/', methods=['GET', 'POST'])
  def output(output_id):
    if output_id == 500:
      raise RuntimeError("boom")
    return 'ok'
  return app


def test_endpoint(app):
  client = app.test_client()
  response = client.get('/metrics', environ_base={'REMOTE_ADDR': '10.0.0.5'})
  assert response.status_code == 200
  assert response.headers['Content-Type'].startswith('text/plain; version=')
  assert 'qaboard_build_info' in response.text
  # answered before the hooks that authenticate: no session, no database
  assert not app.authenticated
  assert client.get('/metrics', environ_base={'REMOTE_ADDR': '8.8.8.8'}).status_code == 403
  assert client.get('/metrics', environ_base={'REMOTE_ADDR': '10.0.0.5'}, headers={'X-Forwarded-For': '10.0.0.9'}).status_code == 403
  assert client.post('/metrics', environ_base={'REMOTE_ADDR': '10.0.0.5'}).status_code == 405


def test_endpoint_with_a_token(app, monkeypatch):
  monkeypatch.setenv('QABOARD_METRICS_TOKEN', 's3cret')
  client = app.test_client()
  refused = client.get('/metrics')
  assert refused.status_code == 401 and refused.headers['WWW-Authenticate'].startswith('Bearer')
  assert 's3cret' not in refused.text
  # the users' tokens are checked by another hook: ours never gets there
  assert client.get('/metrics', headers={'Authorization': 'Bearer s3cret'}).status_code == 200
  assert not app.authenticated


def test_requests_are_labelled_with_route_templates(app):
  client = app.test_client()
  for output_id in (1, 2, 3):
    assert client.get(f'/api/v1/output/{output_id}').status_code == 200
  client.get('/api/v1/output/4/')
  client.post('/api/v1/output/5')
  client.get('/api/v1/output/500')
  client.get('/some/unknown/page/123')
  client.open('/api/v1/output/1', method='PROPFIND')
  found = samples()
  # (series other tests made are there too, at 0)
  requests = {labels: v for (name, labels), v in found.items() if name == 'qaboard_http_requests_total' and v}
  route = '/api/v1/output/<int:output_id>'
  assert requests == {
    (('method', 'GET'), ('route', route), ('status', '2xx')): 4,
    (('method', 'POST'), ('route', route), ('status', '2xx')): 1,
    (('method', 'GET'), ('route', route), ('status', '5xx')): 1,
    (('method', 'GET'), ('route', '(unmatched)'), ('status', '4xx')): 1,
    # a method the route doesn't take matches no route; unknown methods are one label
    (('method', 'OTHER'), ('route', '(unmatched)'), ('status', '4xx')): 1,
  }
  # no path, no id, anywhere
  assert not any('123' in str(labels) or '/output/1' in str(labels) for _, labels in found)
  assert value('qaboard_http_request_duration_seconds_count', method='GET', route=route) == 5
  # every bucket is there, also the empty ones: quantiles need them
  buckets = [labels for (name, labels) in found if name == 'qaboard_http_request_duration_seconds_bucket' and ('method', 'POST') in labels and ('route', route) in labels]
  assert len(buckets) == len(monitoring.HTTP_DURATION._upper_bounds)
  assert value('qaboard_http_request_duration_seconds_bucket', le='+Inf', method='POST', route=route) == 1


def test_requests_in_progress(app):
  seen = []
  @app.route('/now')
  def now():
    seen.append(monitoring.HTTP_IN_PROGRESS._value.get())
    return 'ok'
  client = app.test_client()
  client.get('/now')
  client.get('/api/v1/output/500')
  client.get('/now')
  assert seen == [1, 1]
  # the scrape itself
  assert value('qaboard_http_requests_in_progress', client.get('/metrics', environ_base={'REMOTE_ADDR': '127.0.0.1'}).text) == 1


def test_project_labels_are_capped(metrics, monkeypatch):
  monkeypatch.setenv('QABOARD_METRICS_MAX_PROJECTS', '2')
  assert [monitoring.project_label(p) for p in ['a', 'b', 'c', 'a', 'd', None]] == ['a', 'b', '(other)', 'a', '(other)', '']


# --- What the backend counts ---

def test_processes_that_serve_nothing_write_no_file(metrics):
  monitoring.audit_event('auth.login')
  monitoring.uploaded(10)
  assert not metrics.exists() or not list(metrics.glob('*.db'))


def test_domain_counters(metrics):
  monitoring.serve_as('app')
  monitoring.audit_event('auth.login')
  monitoring.audit_event('auth.login_failed')
  monitoring.audit_event('auth.login_failed')
  monitoring.audit_event('batch.start', 'group/repo')
  monitoring.token_authentication(True)
  monitoring.token_authentication(False)
  monitoring.uploaded(1000)
  monitoring.uploaded(24)
  monitoring.job_finished('garbage_collect', 'done', 12.0)
  monitoring.job_finished('garbage_collect', 'failed', 2.0)
  monitoring.job_finished('hard_delete', 'queued', 5.0)
  found = samples()
  def get(name, **labels):
    return found.get((name, tuple(sorted(labels.items()))))
  assert get('qaboard_sign_ins_total', result='success') == 1
  assert get('qaboard_sign_ins_total', result='failure') == 2
  assert get('qaboard_batches_started_total', project='group/repo') == 1
  assert get('qaboard_audit_events_total', action='auth.login_failed') == 2
  assert get('qaboard_token_authentications_total', result='success') == 1
  assert get('qaboard_token_authentications_total', result='failure') == 1
  assert get('qaboard_upload_bytes_total') == 1024 and get('qaboard_upload_files_total') == 2
  assert get('qaboard_jobs_finished_total', kind='garbage_collect', status='done') == 1
  assert get('qaboard_jobs_finished_total', kind='garbage_collect', status='failed') == 1
  # its worker stopped: another one resumes it, it didn't run for 5 s
  assert get('qaboard_jobs_finished_total', kind='hard_delete', status='requeued') == 1
  assert get('qaboard_job_duration_seconds_count', kind='garbage_collect') == 2
  assert get('qaboard_job_duration_seconds_count', kind='hard_delete') is None


def test_runs_are_counted_when_flushed(metrics):
  monitoring.serve_as('app')
  def run(project='group/repo', is_failed=False):
    return SimpleNamespace(is_failed=is_failed, batch=SimpleNamespace(ci_commit=SimpleNamespace(project_id=project)))
  new, ended, failed, redone, other = run(), run(), run(), run('group/other'), SimpleNamespace(name="not a run")
  monitoring.run_ended(ended)
  monitoring.run_ended(failed)
  # like the endpoint that receives results: whether it failed is known after the status changed
  failed.is_failed = True
  monitoring.run_ended(redone)
  session = SimpleNamespace(new=[new, ended, other], dirty=[failed, redone])
  monitoring.count_runs(session, is_run=lambda o: hasattr(o, 'is_failed'))
  # flushed again: nothing new ended
  monitoring.count_runs(SimpleNamespace(new=[], dirty=[failed, redone]), is_run=lambda o: hasattr(o, 'is_failed'))
  found = samples()
  assert found[('qaboard_runs_created_total', (('project', 'group/repo'),))] == 2
  assert found[('qaboard_runs_finished_total', (('project', 'group/repo'), ('status', 'success')))] == 1
  assert found[('qaboard_runs_finished_total', (('project', 'group/repo'), ('status', 'failed')))] == 1
  assert found[('qaboard_runs_finished_total', (('project', 'group/other'), ('status', 'success')))] == 1


def test_counting_never_raises(metrics):
  monitoring.serve_as('app')
  monitoring.count_runs(SimpleNamespace(new=None, dirty=None), is_run=lambda o: True)
  # a folder we can't write to: values stay in memory
  os.environ['QABOARD_METRICS_DIR'] = '/proc/nope/metrics'
  monitoring.reset_files()
  monitoring.uploaded(1)
  monitoring.audit_event('auth.login')


# --- Several processes ---

def in_child(function):
  """Runs function in a forked process, like uWSGI's workers are forked from its master"""
  pid = os.fork()
  if pid == 0:
    status = 1
    try:
      function()
      monitoring.reset_files()
      status = 0
    finally:
      os._exit(status)
  assert os.waitpid(pid, 0)[1] == 0


def as_worker(worker_id, function, numproc=3):
  def run():
    monitoring._uwsgi = SimpleNamespace(worker_id=lambda: worker_id, numproc=numproc, workers=lambda: [])
    monitoring.process_started()
    function()
  in_child(run)


def test_processes_add_up(metrics):
  # the master defines the metrics and forks: it exports nothing itself
  monitoring._uwsgi = SimpleNamespace(worker_id=lambda: 0, numproc=3, workers=lambda: [])
  monitoring.process_started()
  monitoring.observe_request('GET', SimpleNamespace(rule='/in-the-master'), 200, 0.1)
  for worker_id in (1, 2, 3):
    def work(n=worker_id):
      for _ in range(n):
        monitoring.observe_request('GET', SimpleNamespace(rule='/api/v1/projects'), 200, 0.02 * n)
      monitoring.HTTP_IN_PROGRESS.inc(n)
      monitoring.uploaded(100)
    as_worker(worker_id, work)
  found = samples()
  labels = (('method', 'GET'), ('route', '/api/v1/projects'), ('status', '2xx'))
  assert found[('qaboard_http_requests_total', labels)] == 6
  assert found[('qaboard_http_request_duration_seconds_count', (('method', 'GET'), ('route', '/api/v1/projects')))] == 6
  assert found[('qaboard_http_request_duration_seconds_bucket', (('le', '0.025'), ('method', 'GET'), ('route', '/api/v1/projects')))] == 1
  assert found[('qaboard_http_request_duration_seconds_bucket', (('le', '0.05'), ('method', 'GET'), ('route', '/api/v1/projects')))] == 3
  assert found[('qaboard_upload_bytes_total', ())] == 300
  assert found[('qaboard_http_requests_in_progress', ())] == 6
  # what the master counted before it forked isn't in the workers' files
  assert found[('qaboard_http_requests_total', (('method', 'GET'), ('route', '/in-the-master'), ('status', '2xx')))] == 0
  # one set of files per worker
  assert sorted(p.name for p in metrics.glob('counter_*.db')) == ['counter_1.db', 'counter_2.db', 'counter_3.db']


def test_respawned_workers_keep_counters_and_reset_gauges(metrics):
  def first():
    monitoring.observe_request('GET', SimpleNamespace(rule='/x'), 200, 0.1)
    # killed while it answered a request
    monitoring.HTTP_IN_PROGRESS.inc()
  def second():
    monitoring.observe_request('GET', SimpleNamespace(rule='/x'), 200, 0.1)
  as_worker(1, first)
  assert value('qaboard_http_requests_in_progress') == 1
  for _ in range(20):
    as_worker(1, second)
  assert value('qaboard_http_requests_total', method='GET', route='/x', status='2xx') == 21
  assert value('qaboard_http_requests_in_progress') in (None, 0)
  # however many times workers are recycled, the scrape reads the same few files
  assert len(list(metrics.glob('*.db'))) <= 3


def test_start_removes_what_previous_workers_left(metrics):
  as_worker(1, lambda: monitoring.uploaded(5))
  as_worker(7, lambda: monitoring.uploaded(5), numproc=8)
  monitoring.serve_as('jobs')
  monitoring.job_finished('garbage_collect', 'done', 1)
  assert value('qaboard_upload_bytes_total') == 10
  # a worker of a server with fewer processes: the files of the workers that don't exist anymore go
  as_worker(2, lambda: None, numproc=3)
  assert value('qaboard_upload_bytes_total') == 5
  # the master starts: the workers' files go, not the jobs worker's, which runs next to it
  monitoring._uwsgi = SimpleNamespace(worker_id=lambda: 0, numproc=3, workers=lambda: [])
  monitoring._role = None
  monitoring.process_started()
  assert not value('qaboard_upload_bytes_total')
  assert sorted(p.name for p in metrics.glob('counter_*.db')) == [f'counter_jobs{os.getpid()}.db']
  assert value('qaboard_jobs_finished_total', kind='garbage_collect', status='done') == 1


def test_files_of_dead_processes_without_uwsgi_are_removed(metrics):
  in_child(lambda: (monitoring.serve_as('jobs'), monitoring.job_finished('hard_delete', 'done', 1)))
  assert value('qaboard_jobs_finished_total', kind='hard_delete', status='done') == 1
  # the worker restarts, with another pid
  monitoring.serve_as('jobs')
  assert not value('qaboard_jobs_finished_total', kind='hard_delete', status='done')
  assert monitoring.process_identity() == f'jobs{os.getpid()}'


def test_jobs_worker_serves_its_metrics(metrics, monkeypatch):
  with socket.socket() as s:
    s.bind(('127.0.0.1', 0))
    port = s.getsockname()[1]
  monkeypatch.setenv('QABOARD_METRICS_JOBS_PORT', str(port))
  monkeypatch.setenv('QABOARD_METRICS_JOBS_ADDRESS', '127.0.0.1')
  monkeypatch.setenv('QABOARD_METRICS_TOKEN', 's3cret')
  server = monitoring.start_jobs_worker()
  try:
    monitoring.job_finished('scan_storage', 'done', 3)
    with pytest.raises(urllib.error.HTTPError) as refused:
      urllib.request.urlopen(f'http://127.0.0.1:{port}/metrics', timeout=10)
    assert refused.value.code == 401
    request = urllib.request.Request(f'http://127.0.0.1:{port}/metrics', headers={'Authorization': 'Bearer s3cret'})
    text = urllib.request.urlopen(request, timeout=10).read().decode()
    assert value('qaboard_jobs_finished_total', text, kind='scan_storage', status='done') == 1
    with pytest.raises(urllib.error.HTTPError) as missing:
      urllib.request.urlopen(f'http://127.0.0.1:{port}/', timeout=10)
    assert missing.value.code == 404
  finally:
    server.shutdown()
    server.server_close()


# --- The metrics that need the database ---

def gauge(name, v, **labels):
  return (name, "help", labels, v)


def collected(stats) -> dict:
  found = {}
  for family in stats.collect():
    for sample in family.samples:
      found[(sample.name, tuple(sorted(sample.labels.items())))] = sample.value
  return found


def test_stats_are_cached(tmp_path):
  calls = []
  def fetch():
    calls.append(time.time())
    return [gauge('qaboard_runs_active', len(calls), state='running')]
  stats = monitoring.Stats(fetch, seconds=60, directory=tmp_path)
  for _ in range(20):
    assert collected(stats)[('qaboard_runs_active', (('state', 'running'),))] == 1
  assert len(calls) == 1
  # the other processes of the container read what it computed
  other = monitoring.Stats(fetch, seconds=60, directory=tmp_path)
  assert collected(other)[('qaboard_runs_active', (('state', 'running'),))] == 1
  assert len(calls) == 1
  assert ('qaboard_stats_last_refresh_timestamp_seconds', ()) in collected(other)


def test_stats_are_refreshed_in_the_background(tmp_path):
  release, calls = threading.Event(), []
  def fetch():
    calls.append(1)
    if len(calls) > 1:
      # the database, or a storage, doesn't answer
      release.wait(30)
    return [gauge('qaboard_projects', len(calls))]
  stats = monitoring.Stats(fetch, seconds=0.05, directory=tmp_path, wait=5)
  assert collected(stats)[('qaboard_projects', ())] == 1
  time.sleep(0.1)
  started = time.time()
  for _ in range(5):
    # expired, and the refresh hangs: scrapes get what we had, at once, and start no other refresh
    assert collected(stats)[('qaboard_projects', ())] == 1
  assert time.time() - started < 2
  for _ in range(100):
    if len(calls) == 2:
      break
    time.sleep(0.05)
  assert collected(stats)[('qaboard_projects', ())] == 1
  assert len(calls) == 2
  release.set()
  stats.thread.join(10)
  stats.seconds = 60
  assert collected(stats)[('qaboard_projects', ())] == 2


def test_stats_without_anything_yet_dont_wait_long(tmp_path):
  release = threading.Event()
  stats = monitoring.Stats(lambda: release.wait(30) and [], seconds=60, directory=tmp_path, wait=0.1)
  started = time.time()
  assert collected(stats) == {}
  assert time.time() - started < 5
  release.set()


def test_stats_survive_failures(tmp_path):
  def fetch():
    raise RuntimeError("no database")
  stats = monitoring.Stats(fetch, seconds=60, directory=tmp_path, wait=2)
  assert collected(stats) == {}


def test_database_samples(metrics, monkeypatch):
  monkeypatch.setenv('QABOARD_METRICS_MAX_PROJECTS', '2')
  queries = []
  def execute(sql):
    queries.append(sql)
    if sql == "SELECT 1":
      return [(1,)]
    if 'FROM outputs' in sql:
      return [(3, 10)]
    if 'FROM projects' in sql:
      return [(42,)]
    if "status IN ('queued', 'running')" in sql:
      return [('queued', 2, 1700000000.0)]
    if 'max(finished_at)' in sql:
      return [('garbage_collect', 'done', 1700000100.0), ('garbage_collect', 'failed', 1700000200.0)]
    if 'FROM job_schedules' in sql:
      return [('garbage_collect', 1, 1700009999.0)]
    if 'FROM job_workers' in sql:
      raise RuntimeError("relation does not exist")
    if 'FROM storage_usage' in sql:
      return [('big', 1000, 10), ('medium', 100, 5), ('small', 10, 2), ('tiny', 1, 1)]
    raise AssertionError(sql)
  found = {(name, tuple(sorted(labels.items()))): v for name, _, labels, v in monitoring.database_samples(execute)}
  assert found[('qaboard_database_up', ())] == 1
  assert found[('qaboard_runs_active', (('state', 'running'),))] == 3
  assert found[('qaboard_runs_active', (('state', 'queued'),))] == 7
  assert found[('qaboard_projects', ())] == 42
  assert found[('qaboard_jobs_active', (('status', 'queued'),))] == 2
  assert found[('qaboard_jobs_active', (('status', 'running'),))] == 0
  assert found[('qaboard_jobs_oldest_queued_timestamp_seconds', ())] == 1700000000.0
  assert found[('qaboard_job_last_finished_timestamp_seconds', (('kind', 'garbage_collect'), ('status', 'failed')))] == 1700000200.0
  assert found[('qaboard_job_schedule_next_run_timestamp_seconds', (('kind', 'garbage_collect'),))] == 1700009999.0
  # a query that fails only loses its own metrics
  assert not any(name == 'qaboard_jobs_workers' for name, _ in found)
  # the largest projects by name, the others together
  assert found[('qaboard_storage_used_bytes', (('project', 'big'),))] == 1000
  assert found[('qaboard_storage_used_bytes', (('project', '(other)'),))] == 11
  assert found[('qaboard_storage_runs', (('project', '(other)'),))] == 3
  # no query reads a whole large table per run: they count, with a filter or a group
  assert all('count(' in sql or 'max(' in sql or 'sum(' in sql or sql == "SELECT 1" for sql in queries)


def test_database_down():
  def execute(sql):
    raise RuntimeError("connection refused")
  assert [(name, v) for name, _, _, v in monitoring.database_samples(execute)] == [('qaboard_database_up', 0.0)]


def test_disk_samples(tmp_path):
  found = monitoring.disk_samples([tmp_path, tmp_path / 'missing', tmp_path])
  # filesystems once, missing folders skipped
  assert [name for name, _, _, _ in found] == ['qaboard_storage_free_bytes', 'qaboard_storage_size_bytes']
  assert all(v > 0 for _, _, _, v in found)


# --- A real uWSGI, with several workers ---

UWSGI = shutil.which('uwsgi', path=f"{Path(sys.executable).parent}{os.pathsep}{os.environ.get('PATH', '')}")

UWSGI_APP = '''
import sys, time, types
# only backend/monitoring.py, not the app: like the unit tests' conftest.py
backend = types.ModuleType('backend')
backend.__path__ = [{backend_dir!r}]
sys.modules['backend'] = backend
from backend import monitoring
from flask import Flask
import uwsgi

app = Flask(__name__)
monitoring.register(app)
monitoring.process_started()
uwsgi.post_fork_hook = monitoring.process_started
# no database here
monitoring._stats = monitoring.Stats(lambda: [], directory=monitoring.metrics_dir())

@app.route('/items/<int:item_id>')
def item(item_id):
  return 'ok'

@app.route('/slow')
def slow():
  time.sleep(60)
  return 'ok'
'''


def get(url, timeout=10):
  return urllib.request.build_opener(urllib.request.ProxyHandler({})).open(url, timeout=timeout).read().decode()


@pytest.mark.skipif(UWSGI is None, reason="needs the uwsgi binary")
def test_uwsgi_workers(tmp_path):
  (tmp_path / 'wsgi.py').write_text(textwrap.dedent(UWSGI_APP).format(backend_dir=str(BACKEND_DIR / 'backend')))
  with socket.socket() as s:
    s.bind(('127.0.0.1', 0))
    port = s.getsockname()[1]
  env = {**{k: v for k, v in os.environ.items() if not k.startswith('QABOARD_METRICS')},
         'QABOARD_METRICS': '1', 'QABOARD_METRICS_DIR': str(tmp_path / 'metrics')}
  log = open(tmp_path / 'uwsgi.log', 'w')
  # like backend/uwsgi.ini: a master that loads the app then forks its workers, with threads, recycled after some requests
  server = subprocess.Popen([
    UWSGI, '--master', '--need-app', '--http-socket', f'127.0.0.1:{port}', '--processes', '3', '--threads', '2',
    '--enable-threads', '--py-call-uwsgi-fork-hooks', '--single-interpreter', '--die-on-term', '--max-requests', '40', '--min-worker-lifetime', '0',
    '--virtualenv', sys.prefix, '--chdir', str(tmp_path), '--wsgi-file', 'wsgi.py', '--callable', 'app',
  ], env=env, stdout=log, stderr=subprocess.STDOUT)
  url = f'http://127.0.0.1:{port}'
  def scrape(name, **labels):
    return value(name, get(f'{url}/metrics'), **labels)
  try:
    for _ in range(100):
      try:
        get(f'{url}/items/0')
        break
      except OSError:
        assert server.poll() is None, (tmp_path / 'uwsgi.log').read_text()
        time.sleep(0.1)
    for i in range(1, 200):
      get(f'{url}/items/{i}')
    # whichever worker answers, it's the total of them all, in one series
    for _ in range(6):
      assert scrape('qaboard_http_requests_total', method='GET', route='/items/<int:item_id>', status='2xx') == 200
    assert scrape('qaboard_uwsgi_processes') == 3
    assert scrape('qaboard_uwsgi_workers', status='busy') == 1
    # workers were recycled (max-requests), their counters weren't lost, and there's still one set of files per worker
    assert 'Respawned uWSGI worker' in (tmp_path / 'uwsgi.log').read_text()
    assert len(list((tmp_path / 'metrics').glob('counter_*.db'))) == 3

    # workers killed while they answer: the requests in progress don't stay counted forever
    def slow_request():
      try:
        get(f'{url}/slow', timeout=90)
      except OSError:
        pass  # its worker is killed below
    slow = [threading.Thread(target=slow_request, daemon=True) for _ in range(2)]
    for thread in slow:
      thread.start()
    for _ in range(100):
      if scrape('qaboard_http_requests_in_progress') == 3:
        break
      time.sleep(0.1)
    assert scrape('qaboard_http_requests_in_progress') == 3
    workers = subprocess.run(['pgrep', '-P', str(server.pid)], capture_output=True, text=True).stdout.split()
    assert workers
    for pid in workers:
      os.kill(int(pid), signal.SIGKILL)
    for _ in range(150):
      try:
        if scrape('qaboard_http_requests_in_progress') == 1:
          break
      except OSError:
        pass
      time.sleep(0.2)
    assert scrape('qaboard_http_requests_in_progress') == 1
    assert scrape('qaboard_http_requests_total', method='GET', route='/items/<int:item_id>', status='2xx') == 200
  finally:
    server.terminate()
    try:
      server.wait(20)
    except subprocess.TimeoutExpired:
      server.kill()
    log.close()


# --- The alerts, the dashboard and the docs only name metrics that exist ---

REPO = BACKEND_DIR.parent
RULES = REPO / 'charts' / 'qaboard' / 'files' / 'prometheus-rules.yaml'
DASHBOARD = REPO / 'charts' / 'qaboard' / 'files' / 'grafana-dashboard.json'
DOCS = REPO / 'website' / 'docs' / 'backend-admin' / 'monitoring.mdx'
# CI also runs these tests in the backend's image, which only has backend/
in_checkout = pytest.mark.skipif(not RULES.exists() or not DOCS.exists(), reason="needs the repository's checkout")


def base_name(name: str) -> str:
  return re.sub(r'_(total|bucket|sum|count)$', '', name)


def exported() -> set:
  return {base_name(name) for name in re.findall(r"'(qaboard_[a-z_]+)'", (BACKEND_DIR / 'backend' / 'monitoring.py').read_text())}


def named_in(text: str) -> set:
  return {base_name(name) for name in re.findall(r'\bqaboard_[a-z_]+[a-z]', text)}


@in_checkout
def test_alerts_and_dashboard_use_exported_metrics():
  assert len(exported()) > 25
  rules = RULES.read_text()
  assert named_in(rules) and named_in(rules) <= exported(), named_in(rules) - exported()
  dashboard = json.loads(DASHBOARD.read_text())
  expressions = ' '.join(target['expr'] for panel in dashboard['panels'] for target in panel.get('targets', []))
  assert named_in(expressions) and named_in(expressions) <= exported(), named_in(expressions) - exported()
  ids = [panel['id'] for panel in dashboard['panels']]
  assert len(ids) == len(set(ids))


@in_checkout
def test_dashboard_is_generated():
  generator = DASHBOARD.with_suffix('.py')
  rendered = subprocess.run([sys.executable, '-c', f"import runpy; print(runpy.run_path({str(generator)!r})['render'](), end='')"],
                            capture_output=True, text=True, check=True).stdout
  assert rendered == DASHBOARD.read_text(), f"run {generator.relative_to(REPO)}"


@in_checkout
def test_docs_list_every_metric_and_alert():
  docs = DOCS.read_text()
  documented = {base_name(name) for name in re.findall(r'`(qaboard_[a-z_]+)`', docs)}
  assert documented == exported(), documented ^ exported()
  alerts = set(re.findall(r'alert: (\w+)', RULES.read_text()))
  assert alerts and alerts == set(re.findall(r'`(QABoard\w+)`', docs)), alerts ^ set(re.findall(r'`(QABoard\w+)`', docs))
