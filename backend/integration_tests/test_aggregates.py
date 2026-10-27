"""
The batches' aggregated metrics (models/Batch.py batches_stats). The runs' metrics are jsonb, or json on databases
where `qaboard-admin convert-metrics` didn't run yet: both give the same aggregates.
"""
import json

import pytest

from .test_ingest import output_payload, PROJECT
from .test_admin_cli import run as admin


def run(client, **extra):
  response = client.post('/api/v1/output/', json=output_payload(**extra))
  assert response.status_code == 200, response.get_data(as_text=True)


def aggregated(client, metrics):
  response = client.get('/api/v1/commits', query_string={"project": PROJECT, "metrics": json.dumps(metrics)})
  assert response.status_code == 200, response.get_data(as_text=True)
  return {label: batch['aggregated_metrics'] for label, batch in response.get_json()[0]['batches'].items()}


def loss(median, average, total):
  return {'default': {'loss_median': median, 'loss_average': average, 'loss_sum': total}}


def metrics_type():
  from sqlalchemy import text
  from backend.database import db_session
  return db_session.execute(text("SELECT data_type FROM information_schema.columns WHERE table_name = 'outputs' AND column_name = 'metrics'")).scalar()


@pytest.fixture(params=['jsonb', 'json'])
def metrics_as(request):
  """The tests run with jsonb metrics (databases created by the app), and json ones (databases from before)"""
  if request.param == 'json':
    assert admin('convert-metrics', '--revert', '--yes').exit_code == 0
  assert metrics_type() == request.param
  yield request.param
  if request.param == 'json':
    result = admin('convert-metrics', '--yes')
    assert result.exit_code == 0 and "Converted the runs' metrics to jsonb" in result.output, result.output


def test_aggregates_follow_the_runs(client, metrics_as):
  run(client, rel_input_path='inputs/a.txt', metrics={"loss": 1.0})
  run(client, rel_input_path='inputs/b.txt', metrics={"loss": 3})
  assert aggregated(client, {"loss": 0}) == loss(2.0, 2.0, 4.0)
  # a run sends other results
  run(client, rel_input_path='inputs/b.txt', metrics={"loss": 5.0})
  assert aggregated(client, {"loss": 0}) == loss(3.0, 3.0, 6.0)
  # failed or pending runs don't count
  run(client, rel_input_path='inputs/b.txt', metrics={"loss": 5.0}, is_failed=True)
  assert aggregated(client, {"loss": 0}) == loss(1.0, 1.0, 1.0)
  run(client, rel_input_path='inputs/c.txt', is_pending=True)
  assert aggregated(client, {"loss": 0}) == loss(1.0, 1.0, 1.0)
  run(client, rel_input_path='inputs/c.txt', metrics={"loss": 2.0})
  assert aggregated(client, {"loss": 0}) == loss(1.5, 1.5, 3.0)
  # deleted runs neither
  from backend.database import db_session
  from backend.models import Output, TestInput
  db_session.remove()
  Output.query.join(TestInput).filter(TestInput.path == 'inputs/c.txt').one().deleted = True
  db_session.commit()
  assert aggregated(client, {"loss": 0}) == loss(1.0, 1.0, 1.0)


def test_only_numbers(client, metrics_as):
  """Texts, booleans, objects, nulls and numbers too big for floats are not aggregated"""
  for name, value in [('a', 2), ('b', "3"), ('c', True), ('d', None), ('e', {"x": 1}), ('f', [1]), ('g', 1.5)]:
    run(client, rel_input_path=f'inputs/{name}.txt', metrics={"loss": value, "status": "ok\u0000"})
  # JSON numbers that don't fit in floats, e.g. from other clients
  from sqlalchemy import text
  from backend.database import db_session
  db_session.execute(text(f"UPDATE outputs SET metrics = '{{\"loss\": 1e400}}'::{metrics_as}"))
  db_session.commit()
  run(client, rel_input_path='inputs/h.txt', metrics={"loss": 4.5})
  assert aggregated(client, {"loss": 0, "missing": 0}) == loss(4.5, 4.5, 4.5)


def test_texts_with_nul(client, logged_in):
  """jsonb refuses the character \\u0000 in texts: runs that send it are saved without it"""
  run(client, metrics={"loss": 1.0, "status": "ok\u0000"})
  from backend.models import Output
  assert Output.query.one().metrics == {"loss": 1.0, "status": "ok�"}
  # the activity page reads how long runs computed from their metrics
  assert logged_in('alice').get('/api/v1/activity/runs').status_code == 200


def test_convert_metrics(client, logged_in):
  """`qaboard-admin convert-metrics` converts them both ways, the metrics are the same, and it's audited"""
  run(client, metrics={"loss": 1.5, "status": "ok", "compute_time": 12})
  from backend.models import Output, AuditEvent
  result = admin('convert-metrics', '--yes')
  assert result.exit_code == 0 and "already jsonb" in result.output
  result = admin('convert-metrics', '--revert', input='y\n')
  assert result.exit_code == 0, result.output
  assert 'GB' in result.output and metrics_type() == 'json'
  assert Output.query.one().metrics == {"loss": 1.5, "status": "ok", "compute_time": 12}
  assert logged_in('alice').get('/api/v1/activity/runs').status_code == 200
  assert admin('convert-metrics', input='n\n').exit_code == 1 and metrics_type() == 'json'
  assert admin('convert-metrics', '--yes').exit_code == 0 and metrics_type() == 'jsonb'
  assert Output.query.one().metrics == {"loss": 1.5, "status": "ok", "compute_time": 12}
  assert [e.meta["to"] for e in AuditEvent.query.filter(AuditEvent.action == 'runs.convert_metrics').order_by(AuditEvent.id)] == ['json', 'jsonb']
