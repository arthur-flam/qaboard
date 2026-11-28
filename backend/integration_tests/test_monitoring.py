"""/metrics for Prometheus (backend/monitoring.py) in the real app, against PostgreSQL: hooks' order, runs, the queries"""
from prometheus_client.parser import text_string_to_metric_families

from .conftest import bearer, login
from .test_heartbeats import make_run


def scrape(client, **kwargs) -> dict:
  response = client.get('/metrics', **kwargs)
  assert response.status_code == 200, response.text
  return {(sample.name, tuple(sorted(sample.labels.items()))): sample.value
          for family in text_string_to_metric_families(response.text) for sample in family.samples}


def test_metrics_are_answered_before_the_app_authenticates(client, monkeypatch):
  # the test client connects from 127.0.0.1
  assert ('qaboard_build_info', (('version', 'unknown'),)) in scrape(client)
  # a server that requires signing in, and a bearer token that isn't a user's: still ours to answer
  monkeypatch.setenv('QABOARD_LOGIN_REQUIRED', 'true')
  monkeypatch.setenv('QABOARD_METRICS_TOKEN', 'metrics-secret')
  assert client.get('/metrics').status_code == 401
  assert client.get('/metrics', headers=bearer('qab_pat_not-the-metrics-token')).status_code == 401
  scrape(client, headers=bearer('metrics-secret'))
  # through nginx (it sends these): refused without the token, whatever the address
  monkeypatch.delenv('QABOARD_METRICS_TOKEN')
  assert client.get('/metrics', headers={'X-Forwarded-Proto': 'https'}).status_code == 403
  # and the metrics token is nothing for the API
  assert client.get('/api/v1/projects', headers=bearer('metrics-secret')).status_code == 401


def test_requests_sign_ins_and_tokens_are_counted(client, make_user):
  before = scrape(client)
  make_user('alice')
  assert client.post('/api/v1/user/auth/', data={"username": 'alice', "password": 'wrong'}).status_code == 403
  login(client, 'alice')
  client.get('/api/v1/commit/0123456789abcdef?project=group/repo')
  client.get('/api/v1/projects', headers=bearer('qab_pat_unknown'))
  after = scrape(client)
  def added(name, **labels):
    key = (name, tuple(sorted(labels.items())))
    return after.get(key, 0) - before.get(key, 0)
  assert added('qaboard_sign_ins_total', result='success') == 1
  assert added('qaboard_sign_ins_total', result='failure') == 1
  assert added('qaboard_token_authentications_total', result='failure') == 1
  # the route's template: no commit id, no query string
  assert added('qaboard_http_requests_total', method='GET', route='/api/v1/commit/<path:commit_id>', status='4xx') == 1
  assert not any('0123456789abcdef' in str(key) or 'alice' in str(key) for key in after)
  assert after[('qaboard_db_pool_limit', ())] > 0


def test_runs_are_counted(client):
  from backend.database import db_session
  from backend.models import Output
  before = scrape(client)
  run = make_run(is_pending=True, is_running=True)
  output = db_session.get(Output, run)
  output.set_status(False, False)
  # like the endpoint that receives results: after the status
  output.is_failed = True
  db_session.commit()
  after = scrape(client)
  created = ('qaboard_runs_created_total', (('project', 'group/repo'),))
  failed = ('qaboard_runs_finished_total', (('project', 'group/repo'), ('status', 'failed')))
  assert after[created] - before.get(created, 0) == 1
  assert after[failed] - before.get(failed, 0) == 1


def test_queries(client):
  from backend import monitoring
  from backend.database import db_session
  from backend.jobs import queue
  make_run(is_pending=True, is_running=True)
  queue.create('garbage_collect', {"dry_run": True})
  db_session.commit()
  failures = []
  monitoring_warn = monitoring._warn_once
  try:
    monitoring._warn_once = lambda key, *args, **kwargs: failures.append((key, args))
    samples = {(name, tuple(sorted(labels.items()))): value for name, _, labels, value in monitoring.fetch_stats()}
  finally:
    monitoring._warn_once = monitoring_warn
  # every query runs on the real schema
  assert not failures
  assert samples[('qaboard_database_up', ())] == 1
  assert samples[('qaboard_runs_active', (('state', 'running'),))] == 1
  assert samples[('qaboard_runs_active', (('state', 'queued'),))] == 0
  assert samples[('qaboard_projects', ())] == 1
  assert samples[('qaboard_jobs_active', (('status', 'queued'),))] == 1
  assert samples[('qaboard_jobs_oldest_queued_timestamp_seconds', ())] > 1_600_000_000
  assert samples[('qaboard_jobs_workers', ())] == 0
  assert any(name == 'qaboard_storage_free_bytes' for name, _ in samples)
