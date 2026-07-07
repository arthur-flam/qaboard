"""
Access data related to Projects.
"""
import pytz
import json
import datetime

import ujson
from flask import request, jsonify, make_response

from sqlalchemy import func, asc, desc, or_, null
from sqlalchemy.orm import selectinload
from sqlalchemy.sql import label

from backend import app, db_session
from ..models import Project, CiCommit, Batch, Output
from ..git_hosts import normalize_git_project


to_datetime = lambda s: timezone.localize(datetime.datetime.strptime(s, '%Y-%m-%dT%H:%M:%S.%fZ'))
timezone = pytz.timezone("utc")


def batch_output_summaries(commit_ids, metrics_to_aggregate=None):
  """
  Aggregates output statuses (and optionally metrics) for all the batches
  belonging to the given commits, without loading full Output rows.
  Returns {batch_id: {"valid_outputs": int, ..., "aggregated_metrics": {...}}}
  """
  if not commit_ids:
    return {}
  rows = (db_session
          .query(
            Output.batch_id,
            Output.is_failed,
            Output.is_pending,
            Output.is_running,
            Output.deleted,
            # the metrics JSON can be big: only load it when aggregating
            Output.metrics if metrics_to_aggregate else null().label('metrics'),
          )
          .join(Batch, Output.batch_id == Batch.id)
          .filter(Batch.ci_commit_id.in_(commit_ids))
         )
  summaries = {}
  metric_values = {} # batch_id -> metric -> [values]
  for batch_id, is_failed, is_pending, is_running, deleted, metrics in rows:
    if batch_id not in summaries:
      summaries[batch_id] = {
        'valid_outputs': 0,
        'pending_outputs': 0,
        'running_outputs': 0,
        'failed_outputs': 0,
        'deleted_outputs': 0,
        'aggregated_metrics': {},
      }
    summary = summaries[batch_id]
    is_valid = not is_failed and not is_pending
    if is_valid:
      summary['valid_outputs'] += 1
    if is_pending:
      summary['pending_outputs'] += 1
    if is_running:
      summary['running_outputs'] += 1
    if is_failed:
      summary['failed_outputs'] += 1
    if deleted:
      summary['deleted_outputs'] += 1
    if metrics_to_aggregate and is_valid and isinstance(metrics, dict):
      values = metric_values.setdefault(batch_id, {})
      for metric in metrics_to_aggregate:
        value = metrics.get(metric)
        if value is not None:
          values.setdefault(metric, []).append(value)
  import numpy as np
  for batch_id, values_per_metric in metric_values.items():
    aggregated = {}
    for metric, values in values_per_metric.items():
      if not values:
        continue
      try:
        aggregated[f'{metric}_median'] = float(np.median(values))
        aggregated[f'{metric}_average'] = float(np.average(values))
      except Exception:
        continue
    # remove NaN values, like Batch.aggregated_metrics does
    summaries[batch_id]['aggregated_metrics'] = {k: v for k, v in aggregated.items() if v == v}
  return summaries


@app.route("/api/v1/commits")
@app.route("/api/v1/commits/")
@app.route("/api/v1/commits/<path:branch>")
def get_commits(branch=None):
  """
  List a project's commits, most-recent first.
  Filters: ?branch=, ?committer=, ?from=/&to= (dates), ?search= (matches
  the commit hash, branch, message, committer and batch labels).
  Pagination: ?limit= and ?offset=. When `limit` is passed, the response is
  an envelope {commits, limit, offset, has_more}; otherwise, for backward
  compatibility, a plain array using the legacy date-window behavior.
  """
  project_id = request.args['project']
  paginated = 'limit' in request.args
  limit = min(int(request.args.get('limit', 1000)), 1000)
  offset = max(int(request.args.get('offset', 0)), 0)

  filters = [
    CiCommit.project_id == project_id,
    CiCommit.batches.any(), # commits without runs are never shown
  ]
  if branch:
    branch = branch.replace('origin/', '')
    filters.append(or_(CiCommit.branch == branch, CiCommit.branch == f'origin/{branch}'))
  committer_name = request.args.get('committer', None)
  if committer_name:
    filters.append(CiCommit.committer_name == committer_name)

  search = request.args.get('search', '').strip()
  if search:
    like = f"%{search}%"
    filters.append(or_(
      CiCommit.hexsha.ilike(f"{search}%"),
      CiCommit.branch.ilike(like),
      CiCommit.committer_name.ilike(like),
      CiCommit.message.ilike(like),
      CiCommit.batches.any(Batch.label.ilike(like)),
    ))

  now_localized = timezone.localize(datetime.datetime.now())
  to_date_s = request.args.get('to', None)
  from_date_s = request.args.get('from', None)
  # Paginated requests only filter by date when asked to;
  # legacy requests always default to a sliding 4-day window
  if to_date_s or from_date_s or not paginated:
    to_date = to_datetime(to_date_s) if to_date_s else now_localized
    # We only care about days, and this makes sure we smooth out TZ issues
    to_date = to_date + datetime.timedelta(hours=3)
    from_date = to_datetime(from_date_s) if from_date_s else (now_localized - datetime.timedelta(days=4))
    if not paginated:
      # if there is nothing recent, slide the date window
      # back to the latest commit so users see *something*
      latest_authored_datetime = (db_session
                                  .query(func.max(CiCommit.authored_datetime))
                                  .filter(*filters)
                                  .scalar())
      if not latest_authored_datetime:
        return jsonify([])
      from_date = min(latest_authored_datetime - (to_date - from_date), from_date)
    from_date = from_date - datetime.timedelta(hours=3) # timezones as above
    filters.extend([
      CiCommit.authored_datetime >= from_date,
      CiCommit.authored_datetime <= to_date,
    ])

  with_outputs = False if request.args.get('with_outputs', 'false')=='false' else True
  ci_commits = (db_session
    .query(CiCommit)
    .options(selectinload(CiCommit.batches).selectinload(Batch.outputs)
             if with_outputs else
             selectinload(CiCommit.batches))
    .filter(*filters)
    .order_by(CiCommit.authored_datetime.desc())
    .limit(limit + 1)
    .offset(offset)
    .all()
  )
  has_more = len(ci_commits) > limit
  ci_commits = ci_commits[:limit]

  metrics_to_aggregate = json.loads(request.args.get('metrics', '{}'))

  with_batches = None
  batch = request.args.get('batch', None)
  if batch:
    with_batches = [batch]
  else:
    only_ci_batches = False if request.args.get('only_ci_batches', 'false')=='false' else True
    if only_ci_batches:
      with_batches = ['default']

  # Aggregate output counts/metrics with a single lightweight query,
  # instead of loading every Output row of every batch
  batch_summaries = {} if with_outputs else batch_output_summaries(
    [c.id for c in ci_commits],
    metrics_to_aggregate,
  )
  serializable_commits = [
    c.to_dict(
      with_aggregation=metrics_to_aggregate,
      with_batches=with_batches,
      with_outputs=with_outputs,
      batch_summaries=batch_summaries if not with_outputs else None,
    )
    for c in ci_commits
  ]
  if paginated:
    payload = {
      'commits': serializable_commits,
      'limit': limit,
      'offset': offset,
      'has_more': has_more,
    }
  else:
    payload = serializable_commits
  response = make_response(ujson.dumps(payload))
  response.headers['Content-Type'] = 'application/json'
  return response

@app.route("/api/v1/project/branches")
def get_branches():
  """
  Returns a list of the project's branches, most recently active first.
  Supports ?filter= (substring match) and ?limit=.
  """
  project_id = request.args.get('project')
  branch_filter = request.args.get('filter', '').strip()
  limit = min(int(request.args.get('limit', 5000)), 5000)
  branches = (db_session
              .query(CiCommit.branch, label('latest', func.max(CiCommit.authored_datetime)))
              .filter(CiCommit.project_id==project_id)
             )
  if branch_filter:
    branches = branches.filter(CiCommit.branch.ilike(f"%{branch_filter}%"))
  branches = (branches
              .group_by(CiCommit.branch)
              .order_by(desc('latest'))
              .limit(limit)
             )
  # dedupe "origin/branch" vs "branch", keeping the most-recently-active first
  seen = set()
  results = []
  for branch, _ in branches:
    if not branch:
      continue
    branch = branch.replace('origin/', '')
    if branch not in seen:
      seen.add(branch)
      results.append(branch)
  return jsonify(results)



def serialize_project_row(data, latest_output_datetime, latest_commit_datetime, total_commits):
  data = dict(data)
  if "qatools_metrics" in data:
    del data['qatools_metrics']
  if "qatools_config" in data:
    data['qatools_config'] = {"project": data['qatools_config'].get("project", {})}
  if "git" in data:
    # normalized, and much smaller than raw webhook payloads
    data['git'] = normalize_git_project(data['git'])
  return {
    'data': data,
    'latest_output_datetime': latest_output_datetime.isoformat() if latest_output_datetime else None,
    'latest_commit_datetime': latest_commit_datetime.isoformat() if latest_commit_datetime else None,
    'total_commits': total_commits,
  }


@app.route("/api/v1/projects")
def get_projects():
  """
  List projects. Supports ?search= (substring match on the project id)
  and pagination with ?limit= and ?offset=. When `limit` is passed the
  response is {projects: [{id, ...}], total, limit, offset}, ordered by
  latest activity; otherwise, for backward compatibility, a {id: {...}} map.
  """
  paginated = 'limit' in request.args
  search = request.args.get('search', '').strip()

  projects = (db_session
              .query(
                Project.id,
                Project.data,
                Project.latest_output_datetime,
                label('latest_commit_datetime', func.max(CiCommit.authored_datetime)),
                label('total_commits', func.count(CiCommit.id)),
              )
              .join(CiCommit)
              .group_by(Project.id)
             )
  if search:
    projects = projects.filter(Project.id.ilike(f"%{search}%"))

  if paginated:
    limit = min(int(request.args.get('limit', 50)), 500)
    offset = max(int(request.args.get('offset', 0)), 0)
    # count with the same inner join as the listing: only projects with commits
    total_query = (db_session
                   .query(func.count(func.distinct(Project.id)))
                   .select_from(Project)
                   .join(CiCommit))
    if search:
      total_query = total_query.filter(Project.id.ilike(f"%{search}%"))
    total = total_query.scalar()
    projects = (projects
                # most-recently-active first; commits are a fallback for projects without outputs
                .order_by(desc(func.coalesce(Project.latest_output_datetime, func.max(CiCommit.authored_datetime))))
                .limit(limit)
                .offset(offset))
    payload = {
      'projects': [
        {'id': project_id, **serialize_project_row(*row)}
        for project_id, *row in projects
      ],
      'total': total,
      'limit': limit,
      'offset': offset,
    }
  else:
    projects = projects.order_by(asc(func.lower(Project.id)))
    payload = {
      project_id: serialize_project_row(*row)
      for project_id, *row in projects.all()
    }
  response = make_response(ujson.dumps(payload))
  response.headers['Content-Type'] = 'application/json'
  return response

@app.route("/api/v1/project")
def get_project():
  project_id = request.args['project']
  project = (Project
               .query.filter(
                 Project.id==project_id,
               )
               .one()
              )
  return jsonify(project.data)
