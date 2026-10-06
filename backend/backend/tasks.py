"""
Background tasks run by the "worker" service, see celery_app.py.
Queue them with `task.delay(...)` and return the job id: the webapp polls /api/v1/jobs/<id>.

Rules, so that they keep working whatever the web framework:
- arguments and results are JSON-serializable,
- no Flask (request, g, app context): tasks open their own database session.
"""
import os
import uuid
from concurrent.futures import ThreadPoolExecutor

from backend.celery_app import celery_app
from backend.database import Session
from backend.models import Output, submit_redo_script


# Submitting a run can take minutes (ssh, then waiting for LSF): we submit several at once
redo_concurrency = int(os.environ.get('QABOARD_REDO_CONCURRENCY', 8))


@celery_app.task(name='qaboard.redo_outputs')
def redo_outputs(output_ids: list, user: str) -> dict:
  """
  Runs again the given outputs, as `user`.
  Returns {"started": number of runs submitted, "failed": [{"id": output id, "error": message}]}
  """
  command_id = str(uuid.uuid4())
  scripts, failed = {}, []
  session = Session()
  try:
    outputs = {o.id: o for o in session.query(Output).filter(Output.id.in_(output_ids))}
    for output_id in output_ids:
      output = outputs.get(output_id)
      if not output:
        failed.append({"id": output_id, "error": "Not found"})
        continue
      try:
        scripts[output_id] = output.write_redo_script(user=user, command_id=command_id)
      except Exception as e:
        print(f"ERROR: could not redo output {output_id}: {e}")
        failed.append({"id": output_id, "error": str(e)})
  finally:
    session.close()

  def submit(output_id):
    try:
      if submit_redo_script(user, scripts[output_id]):
        return None
      return f"Could not submit the run, see {scripts[output_id].parent / 'log.txt'}"
    except Exception as e:
      return str(e)

  with ThreadPoolExecutor(max_workers=redo_concurrency) as pool:
    errors = dict(zip(scripts, pool.map(submit, scripts)))
  failed += [{"id": output_id, "error": error} for output_id, error in errors.items() if error]
  return {"started": sum(1 for error in errors.values() if not error), "failed": failed}
