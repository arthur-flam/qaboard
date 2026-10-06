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
    return jsonify({"error": f"Could not get the status of the job: {e}"}), 503


def queue_redo(output_ids, user):
  """Response of the endpoints that run outputs again."""
  if not output_ids:
    return jsonify({"status": "OK", "outputs": 0})
  try:
    job = redo_outputs.delay(output_ids, user=user)
  except Exception as e:
    # e.g. redis is down
    return jsonify({"error": f"Could not queue the runs, try again later. {type(e).__name__}: {e}"}), 503
  return jsonify({"status": "queued", "outputs": len(output_ids), "job_id": job.id}), 202
