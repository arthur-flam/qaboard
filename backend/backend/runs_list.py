"""
The Runs page (docs/rfcs/python-sdk-runs-follow-ups.md, part A): a project's runs across commits, by experiment.

An experiment is a batch's label without the `@user| ` prefix of the batches people run outside the CI: `qa --share`
and `qaboard.start_run()` put Alice's runs of "backbone/lr-sweep" in "@alice| backbone/lr-sweep". Experiments are a
hierarchy by the slashes of their names: "backbone" is everything under "backbone/".

The queries never read a project's whole history: they start from the project's batches that got runs in a time
window (batches.latest_output_datetime, indexed), then read those batches' runs (outputs.batch_id, indexed).
They are plain SQL, so that tests run them on a real PostgreSQL (tests/test_runs_list.py).

- experiments(): the tree, one row per experiment with its counts of runs.
- run_ids(): a page of runs, the running first, then the latest (when they started, were queued, or were created).
"""
import datetime
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Sequence, Tuple

from .mine import Identity


# Batches people run outside the CI: "@alice| name" (see qaboard/active_run.py, `qa --share`)
USER_PREFIX = r'^@[^|]+\| '
EXPERIMENT_SQL = f"regexp_replace(b.label, '{USER_PREFIX}', '')"
# qa optimize's iterations: the web app doesn't list them with the batch's runs
HIDDEN_TYPES = ('optim_iteration',)
STATUSES = ('running', 'pending', 'failed', 'finished')
# A page of the tree. Projects with more experiments in the window see the most recent.
MAX_EXPERIMENTS = 2000


def experiment_of(label: Optional[str]) -> str:
  import re
  return re.sub(USER_PREFIX, '', label or 'default')


def escape_like(text: str) -> str:
  return text.replace('\\', '\\\\').replace('%', '\\%').replace('_', '\\_')


@dataclass
class RunsFilter:
  """What the Runs page selects. `since`: naive UTC, like the database's dates."""
  project: str
  since: datetime.datetime
  experiment: Optional[str] = None       # a node of the tree: itself and what is under it
  identity: Optional[Identity] = None    # with scope: whose
  scope: str = 'all'                     # all, mine, others
  user: Optional[str] = None             # who ran them, by their user name
  origins: Optional[Sequence[str]] = None
  statuses: Sequence[str] = field(default_factory=tuple)
  # only the batches of runs started from code (qaboard.start_run()), see Batch.data['sdk']
  from_code: bool = False

  def batches_where(self) -> Tuple[List[str], Dict[str, Any]]:
    """The conditions on the batches (b) and their commits (c), with their parameters"""
    where = ["c.project_id = :project", "b.latest_output_datetime >= :since"]
    params: Dict[str, Any] = {'project': self.project, 'since': self.since}
    if self.experiment:
      experiment = self.experiment.strip('/')
      where.append(f"({EXPERIMENT_SQL} = :experiment OR {EXPERIMENT_SQL} LIKE :experiment_prefix ESCAPE '\\')")
      params['experiment'] = experiment
      params['experiment_prefix'] = f"{escape_like(experiment)}/%"
    if self.scope in ('mine', 'others') and self.identity is not None:
      mine = []
      if self.identity.user_id is not None:
        mine.append("b.user_id = :user_id")
        params['user_id'] = self.identity.user_id
      if self.identity.user_name:
        mine.append("(b.user_id IS NULL AND b.user_name = :user_name)")
        params['user_name'] = self.identity.user_name
      condition = f"({' OR '.join(mine)})" if mine else "false"
      where.append(condition if self.scope == 'mine' else f"NOT coalesce({condition}, false)")
    if self.user:
      where.append("b.user_name = :run_user")
      params['run_user'] = self.user
    if self.origins:
      where.append("coalesce(b.origin, 'ci') IN :origins")
      params['origins'] = tuple(self.origins)
    if self.from_code:
      where.append("b.data ? 'sdk'")
    return where, params

  def runs_where(self) -> Tuple[List[str], Dict[str, Any]]:
    """...and on their runs (o)"""
    where, params = self.batches_where()
    where.append("o.output_type IS DISTINCT FROM 'optim_iteration'")
    # a batch with a recent run can have old runs: we keep the window
    where.append("coalesce(o.started_at, o.queued_at, o.created_date) >= :since")
    statuses = []
    for status in self.statuses:
      if status == 'running':
        statuses.append("(o.is_pending AND o.is_running)")
      elif status == 'pending':
        statuses.append("(o.is_pending AND o.is_running IS NOT TRUE)")
      elif status == 'failed':
        statuses.append("(o.is_failed AND o.is_pending IS NOT TRUE)")
      elif status == 'finished':
        statuses.append("(o.is_pending IS NOT TRUE AND o.is_failed IS NOT TRUE)")
    if statuses:
      where.append(f"({' OR '.join(statuses)})")
    return where, params


def _execute(session, sql: str, params: Dict[str, Any]):
  from sqlalchemy import text, bindparam
  statement = text(sql)
  if 'origins' in params:
    statement = statement.bindparams(bindparam('origins', expanding=True))
    params = {**params, 'origins': list(params['origins'])}
  return session.execute(statement, params)


def experiments_sql(selection: RunsFilter) -> Tuple[str, Dict[str, Any]]:
  where, params = selection.batches_where()
  sql = f"""
    WITH recent AS (
      SELECT b.id, {EXPERIMENT_SQL} AS experiment, b.latest_output_datetime, b.user_name, (b.data ? 'sdk') AS from_code
      FROM batches b JOIN ci_commits c ON c.id = b.ci_commit_id
      WHERE {' AND '.join(where)}
    )
    SELECT
      r.experiment,
      count(DISTINCT r.id) AS batches,
      max(r.latest_output_datetime) AS latest,
      count(o.id) AS runs,
      count(o.id) FILTER (WHERE o.is_pending AND o.is_running) AS running,
      count(o.id) FILTER (WHERE o.is_pending AND o.is_running IS NOT TRUE) AS pending,
      count(o.id) FILTER (WHERE o.is_failed AND o.is_pending IS NOT TRUE) AS failed,
      bool_or(r.from_code) AS from_code,
      array_agg(DISTINCT r.user_name) FILTER (WHERE r.user_name IS NOT NULL) AS users
    FROM recent r
    LEFT JOIN outputs o ON o.batch_id = r.id
      AND o.output_type IS DISTINCT FROM 'optim_iteration'
      AND coalesce(o.started_at, o.queued_at, o.created_date) >= :since
    GROUP BY r.experiment
    ORDER BY latest DESC NULLS LAST, r.experiment
    LIMIT {MAX_EXPERIMENTS + 1}
  """
  return sql, params


def experiments(session, selection: RunsFilter) -> Tuple[List[Dict[str, Any]], bool]:
  """[{experiment, batches, runs, running, pending, failed, latest, from_code, users}], and if there were more"""
  sql, params = experiments_sql(selection)
  return experiment_rows(_execute(session, sql, params))


def experiment_rows(result) -> Tuple[List[Dict[str, Any]], bool]:
  rows = [
    {
      'experiment': row[0],
      'batches': row[1],
      'latest': f"{row[2].isoformat()}Z" if row[2] else None,
      'runs': row[3],
      'running': row[4],
      'pending': row[5],
      'failed': row[6],
      'from_code': bool(row[7]),
      'users': sorted(row[8] or []),
    }
    for row in result
  ]
  return rows[:MAX_EXPERIMENTS], len(rows) > MAX_EXPERIMENTS


def run_ids_sql(selection: RunsFilter, limit: int, offset: int) -> Tuple[str, Dict[str, Any]]:
  where, params = selection.runs_where()
  sql = f"""
    SELECT o.id
    FROM outputs o
    JOIN batches b ON b.id = o.batch_id
    JOIN ci_commits c ON c.id = b.ci_commit_id
    WHERE {' AND '.join(where)}
    ORDER BY (o.is_pending AND o.is_running) IS TRUE DESC, coalesce(o.started_at, o.queued_at, o.created_date) DESC, o.id DESC
    LIMIT :limit OFFSET :offset
  """
  return sql, {**params, 'limit': limit, 'offset': offset}


def run_ids(session, selection: RunsFilter, limit: int, offset: int) -> List[int]:
  """The ids of a page of runs, running first, then the latest"""
  sql, params = run_ids_sql(selection, limit, offset)
  return [row[0] for row in _execute(session, sql, params)]


def mark_runs_from_code(session, batch_id: int, project: str) -> None:
  """
  A batch got a run started from code: it says so (Batch.data['sdk'], ?from_code=1), and its project gets the Runs page
  (Project.data['has_runs_from_code']). In SQL, without rewriting the rows' data: other requests change them too, and
  once marked, nothing is written again.
  """
  from sqlalchemy import text
  session.execute(text("""
    UPDATE batches SET data = coalesce(data, '{}'::jsonb) || '{"sdk": true}'::jsonb
    WHERE id = :batch AND NOT (coalesce(data, '{}'::jsonb) ? 'sdk')
  """), {'batch': batch_id})
  session.execute(text("""
    UPDATE projects SET data = coalesce(data, '{}'::jsonb) || '{"has_runs_from_code": true}'::jsonb
    WHERE id = :project AND NOT (coalesce(data, '{}'::jsonb) ? 'has_runs_from_code')
  """), {'project': project})
  session.commit()
