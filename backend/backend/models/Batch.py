"""
Represents runs belonging to the same commit.
It might by a CI job, or tuning experiments.
"""
import os
import json
import hashlib
import datetime
from pathlib import Path

import redis
from sqlalchemy import ForeignKey, Integer, String, DateTime, Float, Numeric, Text, text, literal_column
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy import UniqueConstraint, Column, Index
from sqlalchemy.orm import relationship
from sqlalchemy import func, case, and_, cast
from sqlalchemy.exc import DataError

from qaboard.conventions import batch_folder_name
from qaboard.api import dir_to_url

from backend.models import Base, Output, serialize_outputs
from backend.metrics_aggregation import metric_names
from backend.shell_utils import shell_safe, safe_user_name
from backend.storage import UnsafePathError
from backend.hybrid_cache import redis_client



def with_log_dir_url(submission):
  """Where the webapp can read the logs of a batch started from QA-Board"""
  try:
    return {**submission, 'log_dir_url': dir_to_url(Path(submission['log_dir']))}
  except Exception:
    return submission


class Batch(Base):
  __tablename__ = 'batches'
  id = Column(Integer, primary_key=True)
  created_date = Column(DateTime, default=datetime.datetime.utcnow, nullable=False)
  data = Column(
    JSONB(),
    nullable=False,
    default=dict,
    server_default='{}',
    # FIXME?
    # https://stackoverflow.com/questions/38961396/sqlalchemy-set-default-value-for-postgres-json-column
    # says we should use this?
    # default=text("'{}'::jsonb"),
    # server_default=text("'{}'::jsonb"),
  )

  ci_commit_id = Column(Integer(), ForeignKey('ci_commits.id'), index=True)
  ci_commit = relationship("CiCommit", back_populates="batches", foreign_keys=[ci_commit_id])

  # identifies eg whether it is the default CI job, or a tuning experiment...
  label = Column(String(), default="default")

  # Where it comes from: webapp, local or ci, and who started it (backend/origins.py). NULL for batches from before,
  # until the migration e9b1c3d5f7a2 fills them in.
  origin = Column(String())
  user_name = Column(String())
  # the verified user, when it was sent with a token or started from the web app
  user_id = Column(Integer(), ForeignKey('users.id', ondelete='SET NULL', name='fk_batches_user_id'))
  # when it last got a run: lists of recent batches
  latest_output_datetime = Column(DateTime())

  __table_args__ = (
    UniqueConstraint('ci_commit_id', 'label', name='_ci_commit__label'),
    # The batches started from QA-Board: "Activity" shows those that will start soon (backend/run_activity.py)
    Index('ix_batches_submissions', 'id', postgresql_where=text("data ? 'submissions'")),
    # Users' recent batches (the home page, the project page), created by the migration e9b1c3d5f7a2
    Index('ix_batches_user_id_latest_output_datetime', 'user_id', 'latest_output_datetime'),
    Index('ix_batches_user_name_latest_output_datetime', 'user_name', 'latest_output_datetime'),
    Index('ix_batches_latest_output_datetime', 'latest_output_datetime'),
  )

  batch_dir_override = Column(String())

  outputs = relationship("Output",
                         back_populates="batch",
                         cascade="all, delete-orphan"
                        )


  @property
  def batch_dir(self):
    if self.batch_dir_override:
      return Path(self.batch_dir_override)
    else:
      return self.ci_commit.outputs_dir / batch_folder_name(self.label)


  def to_dict(self, session, with_outputs=False, with_aggregation=None, stats=None, outputs=None):
    """
    `stats` are the batch's counts of outputs and aggregated metrics, from batches_stats().
    When serializing many batches, compute them all at once and pass them here. Same for `outputs`, from serialize_outputs()
    """
    batch_dir = self.batch_dir
    if with_outputs:
      if outputs is None:
        outputs = serialize_outputs(session, [self])[self.id]
      outputs = {'outputs': outputs}
    else:
      outputs = {}
    if stats is None:
      stats = batches_stats(session, [self.id], with_aggregation).get(self.id, no_stats)
    data = self.data if self.data else {} # None check for old batches (todo: migrate them properly)
    if data.get('submissions'):
      data = {**data, 'submissions': {id: with_log_dir_url(s) for id, s in data['submissions'].items()}}
    return {
        'id': self.id,
        'commit_id': self.ci_commit.hexsha,
        'label': self.label,
        'created_date': self.created_date.isoformat(),
        'origin': self.origin,
        'user_name': self.user_name,
        'latest_output_datetime': self.latest_output_datetime.isoformat() if self.latest_output_datetime else None,
        'data': data,
        'batch_dir_url': dir_to_url(batch_dir),
        **stats,
        **outputs,
    }

  def __repr__(self):
    return (f"<Batch commit='{self.ci_commit.hexsha}' "
            f"label='{self.label}' "
            f"outputs={len(self.outputs)} />")

  def rename(self, label, db_session):
    # Note that the output directories will still be based on the old label, we don't move/copy anything
    self.label = label
    db_session.add(self)
    db_session.commit()

  def stop(self, session):
    if not any([o.is_pending for o in self.outputs]):
      return {}

    # TODO: it's a bit overkill to stop everything, and may even yield errors...
    # TODO: can we after the stop() just mark all outputs as is_pending:False ?
    errors = []
    for command_id, command in self.data.get('commands', {}).items():
      print(f"stopping {command.get('runner')} {command_id}")
      from qaboard.runners.job import JobGroup
      # Batch.data.commands is written by unauthenticated API calls, and the runners
      # use those options to build shell commands (e.g. `bkill` via the LSF bridge).
      # So we only forward the few options needed to stop jobs, after validating them.
      runner = command.get('runner')
      if runner not in ("lsf", "dask", "local", "celery"):
        print(f"WARNING: cannot stop jobs for runner {runner!r}")
        continue
      try:
        job_options = {
          "type": runner,
          "command_id": shell_safe(command_id, "command_id"),
          "bridge": os.environ.get("QA_RUNNERS_LSF_BRIDGE", ""),
        }
        if command.get('user'):
          job_options['user'] = safe_user_name(command['user'])
      except ValueError as e:
        print(f"WARNING: cannot stop jobs for command {command_id!r}: {e}")
        continue
      # We only connect to Dask schedulers we trust
      trusted_schedulers = [s for s in os.environ.get("QABOARD_DASK_SCHEDULERS", "").split(",") if s]
      if runner == "dask" and command.get('scheduler_address') in trusted_schedulers:
        job_options['scheduler_address'] = command['scheduler_address']
      jobs = JobGroup(job_options=job_options)
      try:
        jobs.stop()
      except Exception as e:
        print(e)
        errors.append(str(e))
        continue
    if errors:
      return {"error": errors}
    else:
      for o in self.outputs:
        if o.is_pending:
          o.is_failed = True
          o.set_status(False, False)
          session.add(o)
      session.commit()
      return {}

  def delete(self, session, soft=False, only_failed=False, filter=None):
    """
    Delete the batch and all related outputs.
    By default it will be a "hard" delete where the metadata+files are deleted from the database/disk.
    With soft deletes, only the files are deleted.
    Note: You should call .stop() before.
    """
    still_has_outputs = False
    for output in self.outputs:
      if only_failed and not output.is_failed:
        still_has_outputs = True
        continue
      try:
        output.delete(soft=soft, filter=filter)
      except UnsafePathError as e:
        # we keep the output, so that admins can see it and its files are not lost
        print(f"WARNING: not deleting {output}: {e}")
        still_has_outputs = True
        continue
      if not soft:
        session.delete(output)
    if not still_has_outputs and not soft:
      session.delete(self)
    session.commit()


no_stats = {
  'aggregated_metrics': {},
  'valid_outputs': 0,
  'pending_outputs': 0,
  'running_outputs': 0,
  'failed_outputs': 0,
  'deleted_outputs': 0,
}
# keeps the query reasonable whatever clients ask for
max_aggregated_metrics = 20

# Aggregating is the slow part: postgres parses each run's metrics (JSON text) for each metric. The aggregates only depend
# on the runs' metrics and statuses, so we keep them in redis, by a fingerprint of exactly that.
# Any change to the runs changes the fingerprint: what we keep is never stale, it only expires.
aggregates_cache_seconds = 24 * 3600
# Batches with pending runs change while they run: we keep each state a short while (some runs never report back)
pending_aggregates_cache_seconds = 10 * 60
# change it when the aggregates change (what's computed, or how)
aggregates_cache_version = 1


def batches_stats(session, batch_ids, metrics_to_aggregate=None):
  """
  In one query for many batches: their number of outputs per status, and the median, average
  and sum of some metrics over their valid outputs (`metrics_to_aggregate`: {name: target}).
  Returns {batch_id: {'valid_outputs': 10, ..., 'aggregated_metrics': {'loss_median': 0.1, 'loss_average': 0.2, 'loss_sum': 2}}}
  Batches without outputs are missing.
  """
  if not batch_ids:
    return {}
  # e.g. {"loss": 0.1}, a list of names, or anything clients send
  metrics = metric_names(metrics_to_aggregate)[:max_aggregated_metrics]
  if not metrics:
    return query_batches_stats(session, batch_ids, [])
  # The counts, and which batches' aggregates we already know
  stats = query_batches_stats(session, batch_ids, [], with_fingerprints=True)
  cached = cached_aggregates(stats, metrics)
  missing = [batch_id for batch_id in stats if batch_id not in cached]
  for batch_id, aggregated in cached.items():
    stats[batch_id]['aggregated_metrics'] = aggregated
  if missing:
    try:
      # e.g. numbers too big for floats: we'd rather show batches without metrics than an error
      with session.begin_nested():
        computed = query_batches_stats(session, missing, metrics, with_fingerprints=True)
    except DataError as e:
      print(f"WARNING: could not aggregate {metrics}: {e}")
    else:
      # the runs may have changed since we counted them
      stats.update(computed)
      cache_aggregates(computed, metrics)
  for batch_stats in stats.values():
    batch_stats.pop('fingerprint', None)
  return stats


def aggregates_key(batch_id, metrics, fingerprint):
  metrics_hash = hashlib.sha1(json.dumps(metrics).encode()).hexdigest()[:16]
  return f"qaboard:batch-aggregates:v{aggregates_cache_version}:{batch_id}:{metrics_hash}:{fingerprint}"


def cached_aggregates(stats, metrics):
  """{batch_id: aggregated_metrics} for the batches of `stats` (with fingerprints) whose aggregates are in redis"""
  keys = {batch_id: aggregates_key(batch_id, metrics, s['fingerprint']) for batch_id, s in stats.items()}
  try:
    values = redis_client.mget(list(keys.values()))
  except redis.RedisError:
    return {}
  return {batch_id: json.loads(value) for batch_id, value in zip(keys, values) if value is not None}


def cache_aggregates(stats, metrics):
  try:
    pipeline = redis_client.pipeline(transaction=False)
    for batch_id, s in stats.items():
      seconds = pending_aggregates_cache_seconds if s['pending_outputs'] else aggregates_cache_seconds
      pipeline.setex(aggregates_key(batch_id, metrics, s['fingerprint']), seconds, json.dumps(s['aggregated_metrics']))
    pipeline.execute()
  except redis.RedisError as e:
    print(f"WARNING: could not save the aggregated metrics: {e}")


def query_batches_stats(session, batch_ids, metrics, with_fingerprints=False):
  """
  batches_stats(), computed by postgres. `with_fingerprints`: each batch's stats also have a `fingerprint` of the
  metrics and statuses of its runs: the number of runs, and the sum of hashes of each run's.
  """
  # Output.metrics is JSON, stored as text: reading a key means parsing it. We read each metric once per output
  # (OFFSET 0 keeps postgres from inlining subqueries, it would read them again for each use).
  outputs = (session
    .query(
      Output.batch_id,
      and_(~Output.is_failed, ~Output.is_pending).label('is_valid'),
      # The metrics of runs from old clients, with NULL statuses, count. Deleted runs' don't.
      and_(Output.is_failed.isnot(True), Output.is_pending.isnot(True), Output.deleted.isnot(True)).label('is_aggregated'),
      Output.is_pending, Output.is_running, Output.is_failed, Output.deleted,
      *([func.hashtextextended(func.concat(cast(Output.metrics, Text), '|', Output.is_failed, '|', Output.is_pending, '|', Output.deleted), 0).label('run_hash')] if with_fingerprints else []),
      *[Output.metrics[metric].label(f'metric_{index}') for index, metric in enumerate(metrics)],
    )
    .filter(Output.batch_id.in_(batch_ids))
    .offset(0)
    .subquery()
  )
  # We only aggregate numbers (not strings, booleans...), from valid outputs (not failed, pending or deleted),
  # that fit in floats. Aggregates ignore NULLs. Like backend/metrics_aggregation.py, in the same query as the counts.
  def as_float(value):
    number = cast(cast(value, Text), Numeric)
    # nested CASEs: postgres evaluates them in order, but not the terms of AND
    return case((func.json_typeof(value) == 'number', case((func.abs(number) < literal_column('1e308::numeric'), cast(number, Float)))))
  values = (session
    .query(
      outputs,
      *[case((outputs.c.is_aggregated, as_float(outputs.c[f'metric_{index}']))).label(f'value_{index}') for index in range(len(metrics))],
    )
    .offset(0)
    .subquery()
  )
  columns = [
    values.c.batch_id,
    func.sum(case((values.c.is_valid, 1), else_=0)),
    func.sum(case((values.c.is_pending, 1), else_=0)),
    func.sum(case((values.c.is_running, 1), else_=0)),
    func.sum(case((values.c.is_failed, 1), else_=0)),
    func.sum(case((values.c.deleted, 1), else_=0)),
  ]
  if with_fingerprints:
    columns.append(func.concat(func.count(), ':', func.sum(values.c.run_hash)))
  for index in range(len(metrics)):
    value = values.c[f'value_{index}']
    columns.append(func.percentile_cont(0.5).within_group(value))
    columns.append(func.avg(value))
    columns.append(func.sum(value))
  rows = session.query(*columns).group_by(values.c.batch_id).all()

  stats = {}
  for batch_id, valid, pending, running, failed, deleted, *aggregates in rows:
    fingerprint = aggregates.pop(0) if with_fingerprints else None
    aggregated = {}
    for index, metric in enumerate(metrics):
      median, average, total = aggregates[3 * index:3 * index + 3]
      if median is not None:
        aggregated[f'{metric}_median'] = median
      if average is not None:
        aggregated[f'{metric}_average'] = average
      if total is not None:
        aggregated[f'{metric}_sum'] = total
    stats[batch_id] = {
      'aggregated_metrics': aggregated,
      'valid_outputs': valid or 0,
      'pending_outputs': pending or 0,
      'running_outputs': running or 0,
      'failed_outputs': failed or 0,
      'deleted_outputs': deleted or 0,
    }
    if with_fingerprints:
      stats[batch_id]['fingerprint'] = fingerprint
  return stats
