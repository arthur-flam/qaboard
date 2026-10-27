import datetime

from flask import request, jsonify, make_response, g
from sqlalchemy.orm.attributes import flag_modified
from sqlalchemy.orm.exc import NoResultFound

from qaboard.conventions import deserialize_config

from backend import app, db_session
from ..models import TestInput, CiCommit, Output
from ..origins import run_origin, run_user, claim_batch
from ..storage import check_storage_path, UnsafePathError
from ..redo import redo_response
from ..metrics_definitions import save_metrics_definitions
from ..notifications.batches import on_run_ended
from ..access import invalid_project
from .auth import login_required, is_authorized_user, ingest_access, forbidden_project, require_project_access

# Keys of a run's data that only the server sets
# - optimization_best allows `qa optimize` to replace this run (see api/batch.py)
# - submitted_by is who really sent it (api/auth.py:ingest_access)
SERVER_KEYS = {'optimization_best', 'submitted_by'}


@app.route("/api/v1/output/<output_id>", methods=['GET'])
@app.route("/api/v1/output/<output_id>/", methods=['GET'])
def get_output(output_id):
  try:
    output = Output.query.filter(Output.id==output_id).one()
  except NoResultFound:
    return jsonify({"error": f"Cannot find output {output_id}"}), 404
  if not is_authorized_user(None, output.batch.ci_commit.project_id):
    return forbidden_project(output.batch.ci_commit.project_id)
  return jsonify(output.to_dict())


# Only the web app changes or deletes outputs, the CLI uses POST /api/v1/output/
# Deleting removes the output's folder, which comes from unauthenticated API calls
@app.route("/api/v1/output/<output_id>", methods=['PUT', 'DELETE'])
@app.route("/api/v1/output/<output_id>/", methods=['PUT', 'DELETE'])
@login_required
def update_output(output_id):
  try:
    output = Output.query.filter(Output.id==output_id).one()
  except NoResultFound:
    if request.method == 'DELETE':
      return {"status": "OK"}
    return jsonify({"error": f"Cannot find output {output_id}"}), 404
  if forbidden := require_project_access(g.user, output.batch.ci_commit.project_id):
    return forbidden

  if request.method == 'PUT':
    data = request.get_json()
    if 'is_pending' in data or 'is_running' in data:
      is_pending = data.get('is_pending', output.is_pending)
      output.set_status(is_pending, data.get('is_running', output.is_running if is_pending else False))
    if 'is_failed' in data:
      output.is_failed = data['is_failed']
    if 'data' in data:
      if not isinstance(data['data'], dict):
        return jsonify({"error": "`data` must be an object"}), 400
      if not output.data:
        output.data = {}
      output.data.update({k: v for k, v in data['data'].items() if k not in SERVER_KEYS})
      flag_modified(output, "data")
    if data.get('batch'):
      output.data["batch"] = data["batch"]
      flag_modified(output, "data")
    if 'metrics' in data:
      output.metrics = {**output.metrics,  **data['metrics']}
      flag_modified(output, "metrics")
    db_session.add(output)
    db_session.commit()
    return jsonify(output.to_dict())

  if request.method == 'DELETE':
    if output.is_pending:
      return {"error": "Please wait for the Output to finish running before deleting it"}, 409
    soft = request.args.get('soft') == 'true'
    batch_id = output.batch_id
    try:
      output.delete(soft=soft)
    except UnsafePathError as e:
      return jsonify({"error": f"{e}"}), 400
    if not soft:
      db_session.delete(output)
    # soft deletes mark the run as deleted
    db_session.commit()
    from ..storage_usage import refresh_soon
    refresh_soon([batch_id])
    return {"status": "OK"}


@app.route('/api/v1/output/redo/<output_id>', methods=['POST'])
@app.route('/api/v1/output/redo/<output_id>/', methods=['POST'])
@login_required
def output_redo(output_id):
  try:
    output = Output.query.filter(Output.id==output_id).one()
  except NoResultFound:
    return jsonify({"error": f"Cannot find output {output_id}"}), 404
  if forbidden := require_project_access(g.user, output.batch.ci_commit.project_id):
    return forbidden
  return redo_response(output.batch, [output], g.user)


@app.route("/api/v1/output/<output_id>/manifest", methods=['GET'])
@app.route("/api/v1/output/<output_id>/manifest/", methods=['GET'])
def get_output_manifest(output_id):
  try:
    output = Output.query.filter(Output.id==output_id).one()
  except NoResultFound:
    return jsonify({"error": f"Cannot find output {output_id}"}), 404
  if not is_authorized_user(None, output.batch.ci_commit.project_id):
    return forbidden_project(output.batch.ci_commit.project_id)
  manifest_path = output.output_dir / "manifest.outputs.json"
  if output.is_running or request.args.get('refresh') or not manifest_path.exists():
    try:
      manifest = output.update_manifest(compute_hashes=False)
    except UnsafePathError as e:
      return jsonify({"error": f"{e}"}), 400
    return jsonify(manifest)
  else:
    # We return the manifest's content instead of redirecting to dir_to_url(manifest_path):
    # in dev, redirects pointed to http://backend/, which the frontend cannot reach.
    response = make_response(manifest_path.read_text())
    response.headers['Content-Type'] = 'application/json'
    return response


@app.route('/api/v1/output', methods=['POST'])
@app.route('/api/v1/output/', methods=['POST'])
def new_output_webhook():
  """Updates the database when we get new results."""
  data = request.get_json()
  output, ci_commit, error = save_output(data)
  if error:
    return error
  db_session.commit()
  after_output_saved(output, ci_commit, data)
  return jsonify(output.to_dict())


# `qa batch` sends more in several calls
max_runs_per_call = 1000

@app.route('/api/v1/outputs', methods=['POST'])
@app.route('/api/v1/outputs/', methods=['POST'])
def new_outputs_webhook():
  """
  Many runs at once, e.g. the runs `qa batch` is about to start: {...what the runs share, "runs": [{...}, ...]}.
  Each run, with what they share, is what POST /api/v1/output/ gets. They share their project and commit.
  Answers with what /api/v1/output/ answers for each run, in order: the run, or {"error", "status", ...}.
  One request and one transaction instead of one per run: several times faster.
  """
  body = request.get_json(silent=True)
  if not isinstance(body, dict) or not isinstance(body.get('runs'), list) or not all(isinstance(run, dict) for run in body['runs']):
    return jsonify({"error": "Send {...what the runs share, \"runs\": [{...each run}]}"}), 400
  if len(body['runs']) > max_runs_per_call:
    return jsonify({"error": f"Send at most {max_runs_per_call} runs per call"}), 400
  shared = {k: v for k, v in body.items() if k != 'runs'}
  commit_of = lambda data: (data.get('project'), data.get('commit_sha') or data.get('git_commit_sha'))
  if error := check_ingest(shared)[1]:
    return error
  runs = [{**shared, **run} for run in body['runs']]
  cache = {}
  # New inputs are committed when they're created: if another request creates one at the same time, we roll back.
  # Before we save the runs, we wouldn't lose them.
  for data in runs:
    path = data.get('rel_input_path', data.get('input_path'))
    if path and data.get('database') and commit_of(data) == commit_of(shared) and not run_paths(data, cache)[2]:
      input_key = ('input', str(data['database']), str(path))
      if input_key not in cache:
        cache[input_key] = TestInput.get_or_create(db_session, path=path, database=data['database'], autocommit=True)
  saved, answers = [], []
  for data in runs:
    if commit_of(data) != commit_of(shared):
      answers.append({"error": "The runs sent together share their project and commit", "status": 400})
      continue
    output, ci_commit, error = save_output(data, cache)
    if error:
      response, status = error
      answers.append({**response.get_json(), "status": status})
      continue
    # the next runs see it, e.g. the same run sent twice
    db_session.flush()
    # after the commit, reading the runs would read them again: e.g. `qa batch` sends runs that are only pending
    if not output.is_pending or data.get('metrics_definitions'):
      saved.append((output, ci_commit, data))
    answers.append(output.to_dict())
  db_session.commit()
  for output, ci_commit, data in saved:
    after_output_saved(output, ci_commit, data)
  return jsonify(answers)


def check_ingest(data):
  """(who sent the run, None) or (None, an error response)"""
  # git_commit_sha for backward-compatibility
  if not (data.get('commit_sha') or data.get('git_commit_sha')):
    return None, (jsonify({"error": "the commit (commit_sha) was not provided"}), 400)
  if error := invalid_project(data.get('project')):
    return None, error
  return ingest_access(data.get('project'))


def run_paths(data, cache=None):
  """(output_directory, artifacts_commit, None) or (None, None, an error response). `cache`: see save_output"""
  cache = {} if cache is None else cache
  # The server will write, delete and run code in those folders
  output_directory = data.get('output_directory')
  # prefix_output_dir for backward-compatibility
  artifacts_commit = data.get('artifacts_commit', data.get('commit_ci_dir'))
  if not artifacts_commit or not artifacts_commit.startswith('/'): # or "some-protocol://"
    artifacts_commit = None # just ignore...
  try:
    for path in (output_directory, artifacts_commit):
      if path and ('path', path) not in cache:
        check_storage_path(path)
        cache[('path', path)] = True
  except UnsafePathError as e:
    return None, None, (jsonify({"error": f"{e}"}), 400)
  return output_directory, artifacts_commit, None


def save_output(data, cache=None):
  """
  Saves a run `qa` sent in the database session, without committing: (output, commit, None), or (None, None, an error response).
  `cache`: a dict to share when saving many runs of the same commit.
  """
  cache = {} if cache is None else cache
  hexsha = data.get('commit_sha') or data.get('git_commit_sha')
  if not hexsha:
    return None, None, (jsonify({"error": "the commit (commit_sha) was not provided"}), 400)
  output_directory, artifacts_commit, error = run_paths(data, cache)
  if error:
    return None, None, error
  submitted_by, error = check_ingest(data)
  if error:
    return None, None, error

  # We get a handle on the Commit object related to our new output
  commit_key = ('commit', data['project'], hexsha)
  ci_commit = cache.get(commit_key)
  if ci_commit is None:
    try:
      ci_commit = CiCommit.get_or_create(
        session=db_session,
        hexsha=hexsha,
        project_id=data['project'],
        data=data,
      )
    except Exception as e:
      return None, None, (jsonify({"error": f"Could not find your commit ({hexsha}). {e}"}), 404)
    cache[commit_key] = ci_commit

  # update the last_output times, with a crude debouncing to avoid
  # keeping locks on the tables too long
  now = datetime.datetime.utcnow()
  threshold = datetime.timedelta(seconds=5)
  # the column may be read back as timezone-aware (e.g. on fresh databases
  # created from the model's DateTime(timezone=True), unlike legacy
  # alembic-migrated databases), while `now` is naive: normalize before
  # subtracting to avoid TypeError
  def _naive(dt):
    return dt.replace(tzinfo=None) if dt is not None else dt
  if not ci_commit.project.latest_output_datetime or now - _naive(ci_commit.project.latest_output_datetime) > threshold:
      ci_commit.project.latest_output_datetime = now
  if not ci_commit.latest_output_datetime or now - _naive(ci_commit.latest_output_datetime) > threshold:
      ci_commit.latest_output_datetime = now

  # We make sure the Test on which we ran exists in the database 
  test_input_path = data.get('rel_input_path', data.get('input_path'))
  if not test_input_path:
    return None, None, (jsonify({"error": "the input path was not provided"}), 400)
  test_input = cache.get(('input', str(data['database']), str(test_input_path))) or TestInput.get_or_create(
    db_session,
    path=test_input_path,
    database=data['database'],
    autocommit=True,
  )
  if data.get('input_metadata'):
    test_input.data['metadata'] = data['input_metadata']
    flag_modified(test_input, "data")

  # We save the basic information about our result
  batch = ci_commit.get_or_create_batch(data['batch_label'])
  if not batch.data:
    batch.data = {}
  batch.data.update({"type": data['job_type']})

  platform = data['platform']
  # for backward-compat with old clients
  if platform == 'lsf':
    platform = 'linux'

  configurations = deserialize_config(data['configuration']) if 'configuration' in data else data['configurations']
  output = Output.get_or_create(db_session,
                                         batch=batch,
                                         platform=platform,
                                         configurations=configurations,
                                         extra_parameters=data['extra_parameters'],
                                         test_input=test_input,
                                        )
  output.output_type = data.get('input_type', '')

  if not output.data:
    output.data = {}
  if "data" in data: # e.g. storage, job_options, batch name
    output.data.update({k: v for k, v in data['data'].items() if k not in SERVER_KEYS})
    # The `qa batch` command that started the run: the first one when they are nested, e.g. in pipelines.
    # Older clients only send the command of their own `qa batch`. Used to know when batches end (backend/notifications/)
    job_options = data['data'].get('job_options') if isinstance(data['data'].get('job_options'), dict) else {}
    root_command_id = data['data'].get('root_command_id') or job_options.get('command_id')
    if root_command_id:
      output.data['root_command_id'] = str(root_command_id)
    flag_modified(output, "data")
  output.data["user"] = data['user']
  # the changes below are in place: tell SQLAlchemy, also when the client sent no `data`
  flag_modified(output, "data")
  # who really sent it, `user` is who it claims to be
  if submitted_by:
    output.data["submitted_by"] = submitted_by
  else:
    output.data.pop("submitted_by", None)
  # we can only trust CI outputs to run on the exact code from the commit
  output.data["ci"] = data['job_type'] == 'ci'
  # where it comes from, and the batch's if it's new (backend/origins.py)
  sent_data = data.get('data') if isinstance(data.get('data'), dict) else {}
  sent_job_options = sent_data.get('job_options') if isinstance(sent_data.get('job_options'), dict) else {}
  origin = run_origin(data, batch.data, submitted_by, [sent_data.get('root_command_id'), sent_job_options.get('command_id')])
  output.data["origin"] = origin
  claim_batch(batch, origin, run_user(data, submitted_by), (submitted_by or {}).get('user_id'))
  if not isinstance(batch.latest_output_datetime, datetime.datetime) or now - batch.latest_output_datetime > threshold:
    batch.latest_output_datetime = now
  if data.get('batch'):
    output.data["batch"] = data["batch"]
  if output.deleted:
    output.deleted = False

  ci_commit.commit_dir_override = artifacts_commit
  output.output_dir_override = output_directory

  # We update the output's status
  output.set_status(data.get('is_pending', False), data.get('is_running', False))

  # A run that starts again (redo, qa batch...) is not failed anymore, until it says so when it ends.
  # Else re-runs of failed runs show as failed (and count as failed) while they are pending or running.
  if output.is_pending:
    output.is_failed = False
  # We save the output's metrics
  else:
    metrics = data.get('metrics') or {}
    output.metrics = metrics
    # False when the metrics don't say: batches count NULL as neither valid nor failed
    output.is_failed = bool(data.get('is_failed', False) or metrics.get('is_failed'))

  db_session.add(ci_commit)
  db_session.add(output)
  return output, ci_commit, None


def after_output_saved(output, ci_commit, data):
  """Once the run is committed"""
  if not output.is_pending:
    # maybe the batch's last run: users may want to know
    on_run_ended(db_session, output.data.get('root_command_id'))
  # The metrics the run's code declared, e.g. qaboard.Metric(12.3, suffix="ms")
  if data.get('metrics_definitions'):
    try:
      save_metrics_definitions(db_session, ci_commit, data)
    except Exception as e:
      db_session.rollback()
      print(f"WARNING: could not save the metrics definitions of a run of {ci_commit.hexsha}: {e}")




