"""
The batches' aggregated metrics are kept in redis by a fingerprint of their runs (backend/models/Batch.py):
lists answer from it, and never with aggregates of runs that changed since.
"""
import json
import importlib

import redis
import pytest

from .test_ingest import output_payload, PROJECT


batch_module = importlib.import_module('backend.models.Batch')


@pytest.fixture(autouse=True)
def forget_aggregates():
  # the same runs in other tests have the same fingerprints
  try:
    for key in batch_module.redis_client.scan_iter('qaboard:batch-aggregates:*'):
      batch_module.redis_client.delete(key)
  except redis.RedisError:
    pass


def run(client, **extra):
  response = client.post('/api/v1/output/', json=output_payload(**extra))
  assert response.status_code == 200, response.get_data(as_text=True)


def aggregated(client, metrics):
  response = client.get('/api/v1/commits', query_string={"project": PROJECT, "metrics": json.dumps(metrics)})
  assert response.status_code == 200, response.get_data(as_text=True)
  return {label: batch['aggregated_metrics'] for label, batch in response.get_json()[0]['batches'].items()}


def loss(median, average, total):
  return {'default': {'loss_median': median, 'loss_average': average, 'loss_sum': total}}


def test_aggregates_follow_the_runs(client, monkeypatch):
  aggregations = []
  query = batch_module.query_batches_stats
  def spy(session, batch_ids, metrics, **kwargs):
    if metrics:
      aggregations.append(metrics)
    return query(session, batch_ids, metrics, **kwargs)
  monkeypatch.setattr(batch_module, 'query_batches_stats', spy)

  run(client, rel_input_path='inputs/a.txt', metrics={"loss": 1.0})
  run(client, rel_input_path='inputs/b.txt', metrics={"loss": 3.0})
  assert aggregated(client, {"loss": 0}) == loss(2.0, 2.0, 4.0)
  assert aggregations == [['loss']]
  # known: postgres only counts the runs
  assert aggregated(client, {"loss": 0}) == loss(2.0, 2.0, 4.0)
  assert aggregations == [['loss']]
  # other metrics are aggregated on their own
  assert aggregated(client, {"loss": 0, "psnr": 0}) == loss(2.0, 2.0, 4.0)
  assert aggregations == [['loss'], ['loss', 'psnr']]

  # a run sends other results: it's still finished, it was never pending
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
  output = Output.query.join(TestInput).filter(TestInput.path == 'inputs/c.txt').one()
  output.deleted = True
  db_session.commit()
  assert aggregated(client, {"loss": 0}) == loss(1.0, 1.0, 1.0)
  # ...and they were all computed, the cache never answered for runs that changed
  assert len(aggregations) == 2 + 5


def test_without_redis(client, monkeypatch):
  def down(*args, **kwargs):
    raise redis.ConnectionError("redis is down")
  monkeypatch.setattr(batch_module.redis_client, 'mget', down)
  monkeypatch.setattr(batch_module.redis_client, 'pipeline', down)
  run(client, metrics={"loss": 2.0})
  assert aggregated(client, {"loss": 0}) == loss(2.0, 2.0, 2.0)
