"""
Here is the "write" part of the API, to signal more data is ready.
It includes the actual webhooks sent e.g. by Gitlab, as well as
API calls to update batches and outputs.
"""
import sys
import json

from flask import request, jsonify
from sqlalchemy.orm.attributes import flag_modified

from backend import app, db_session
from ..models import CiCommit, Output
from ..models.Project import update_project
from ..git_hosts import normalize_push_event


@app.route('/api/v1/commit/<path:project_id>/<commit_id>/batches', methods=['DELETE'])
@app.route('/api/v1/commit/<path:project_id>/<commit_id>/batches/', methods=['DELETE'])
@app.route('/api/v1/commit/<commit_id>/batches', methods=['DELETE'])
@app.route('/api/v1/commit/<commit_id>/batches/', methods=['DELETE'])
def delete_commit(commit_id, project_id=None):
  try:
    ci_commits = CiCommit.query.filter(CiCommit.hexsha == commit_id)
    if project_id:
      ci_commits = ci_commits.filter(CiCommit.project_id == project_id)
    ci_commits = ci_commits.all()
  except Exception as e:
    return f"404 ERROR {e}: {commit_id} in {project_id}", 404
  for ci_commit in ci_commits:
    print("DELETING", ci_commit)
    if ci_commit.hexsha in ci_commit.project.milestone_commits:
      return f"403 ERROR: Cannot delete milestones", 403
    for batch in ci_commit.batches:
      print(f" > {batch}")
      stop_status = batch.stop(db_session)
      if "error" in stop_status:
        return jsonify(stop_status), 500
      batch.delete(session=db_session)
    return {"status": "OK"}
  return f"404 ERROR: Cannot find commit", 404




@app.route('/webhook/gitlab', methods=['GET', 'POST'])
def gitlab_webhook():
  """Push webhook for GitLab: keeps our project metadata and local git clones up-to-date."""
  # https://docs.gitlab.com/ce/user/project/integrations/webhooks.html
  data = json.loads(request.data)
  # GitLab sends many event types on the same hook; we only care about pushes
  if request.headers.get('X-Gitlab-Event', 'Push Hook') not in ('Push Hook', 'Tag Push Hook'):
    return jsonify({"status": "ignored"})
  update_project(normalize_push_event(data, host='gitlab'), db_session)
  return jsonify({"status": "OK"})


@app.route('/webhook/github', methods=['GET', 'POST'])
def github_webhook():
  """Push webhook for GitHub: keeps our project metadata and local git clones up-to-date."""
  # https://docs.github.com/webhooks/webhook-events-and-payloads#push
  event = request.headers.get('X-GitHub-Event', 'push')
  if event == 'ping':
    return jsonify({"status": "pong"})
  if event != 'push':
    return jsonify({"status": "ignored"})
  data = json.loads(request.data)
  update_project(normalize_push_event(data, host='github'), db_session)
  return jsonify({"status": "OK"})


@app.route('/webhook/git', methods=['GET', 'POST'])
def git_webhook():
  """
  Generic push webhook for any other git host.
  Expects a normalized payload:
    {"ref": "refs/heads/main", "checkout_sha": "...",
     "project": {"path_with_namespace": "group/repo", "web_url": ..., "clone_url": ...}}
  GitLab- and GitHub-shaped payloads are also recognized.
  """
  data = json.loads(request.data)
  update_project(normalize_push_event(data), db_session)
  return jsonify({"status": "OK"})

