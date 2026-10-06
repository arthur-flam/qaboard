"""
Background jobs, run by the worker (see backend/celery_app.py and backend/tasks.py).
Endpoints that start one answer 202 with {"job_id"}, then the webapp polls /api/v1/jobs/<job_id>.
"""
from flask import jsonify

from backend import app
from ..celery_app import job_status
from ..tasks import redo_outputs
from .auth import login_required


@app.route('/api/v1/jobs/<job_id>', methods=['GET'])
@app.route('/api/v1/jobs/<job_id>/', methods=['GET'])
@login_required
def get_job(job_id):
  try:
    return jsonify(job_status(job_id))
  except Exception as e:
    print(f"ERROR: could not get the status of job {job_id}: {type(e).__name__}: {e}")
    return jsonify({"error": "Could not get the status of the job, try again later."}), 503


def queue_redo(output_ids, user):
  """Response of the endpoints that run outputs again."""
  if not output_ids:
    return jsonify({"status": "OK", "outputs": 0})
  try:
    job = redo_outputs.delay(output_ids, user=user)
  except Exception as e:
    # e.g. redis is down. Users don't need the internal hostnames in the error
    print(f"ERROR: could not queue the runs of outputs {output_ids}: {type(e).__name__}: {e}")
    return jsonify({"error": "Could not queue the runs, try again later. If it lasts, tell your QA-Board admins."}), 503
  return jsonify({"status": "queued", "outputs": len(output_ids), "job_id": job.id}), 202
