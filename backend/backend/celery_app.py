"""
Celery app for the server's background tasks (backend/tasks.py): requests that would take too long
for an HTTP request (nginx and uwsgi give up after 5 minutes) queue a task and return its id right away.
The "worker" service runs them (see worker.sh), and the webapp polls /api/v1/jobs/<id> for their result.

Not to be confused with qaboard.runners.celery_app, that runs users' code on their workers.

To keep the tasks independent from the web framework (they don't use Flask's request, g or app context):
- task arguments and results are JSON (ids and names, not ORM objects),
- tasks open their own database session,
- `job_status` describes a job as a dict, that any web framework can return.
"""
import os

from celery import Celery
from celery.backends.redis import RedisBackend
from celery.result import AsyncResult
from celery.signals import worker_process_init


redis_url = f"redis://{os.environ.get('REDIS_HOST', 'localhost')}:{os.environ.get('REDIS_PORT', '6379')}"
# Database 1: the cache uses database 0 (backend/hybrid_cache.py)
broker_url = os.environ.get('QABOARD_TASKS_BROKER_URL', f'{redis_url}/1')
# Celery has no amqp result backend: with a RabbitMQ broker, results are still saved in redis
default_result_backend = broker_url if broker_url.startswith(('redis://', 'rediss://')) else f'{redis_url}/1'
result_backend_url = os.environ.get('QABOARD_TASKS_RESULT_BACKEND', default_result_backend)


class PollingRedisBackend(RedisBackend):
  """
  We poll the state of jobs, and never wait for their result (result.get()). So unlike celery's default,
  we don't subscribe to the result of each task we queue: long-lived web processes would accumulate subscriptions,
  and when redis is down queuing a task would wait for them to reconnect.
  """
  def on_task_call(self, producer, task_id):
    pass


if result_backend_url.startswith(('redis://', 'rediss://')):
  result_backend_url = f'{__name__}:PollingRedisBackend+{result_backend_url}'

celery_app = Celery(
  'qaboard-server',
  broker=broker_url,
  backend=result_backend_url,
  include=['backend.tasks'],
)
celery_app.conf.update(
  task_default_queue=os.environ.get('QABOARD_TASKS_QUEUE', 'qaboard-server'),
  task_serializer='json',
  result_serializer='json',
  accept_content=['json'],
  # Jobs are STARTED while they run, not PENDING (that also means "unknown job")
  task_track_started=True,
  result_expires=7 * 24 * 3600,
  # Tasks are long: don't reserve tasks that another worker process could start
  worker_prefetch_multiplier=1,
  broker_connection_retry_on_startup=True,
  # Fail fast in HTTP requests if redis is down or unreachable
  broker_connection_timeout=5,
  broker_transport_options={'socket_connect_timeout': 5, 'socket_timeout': 10},
  redis_socket_connect_timeout=5,
  # For development without a worker: tasks run in the HTTP request, like before we had a worker
  task_always_eager=os.environ.get('QABOARD_TASKS_EAGER', '').lower() in ('1', 'true', 'yes'),
  task_store_eager_result=True,  # so that job_status works the same
)


@worker_process_init.connect
def reset_database_connections(**kwargs):
  # Worker processes are forked: they must not share the parent's database connections
  # https://docs.sqlalchemy.org/en/20/core/pooling.html#pooling-multiprocessing
  from backend.database import engine
  engine.dispose(close=False)


def job_status(job_id: str) -> dict:
  """
  Describes a job: {"id", "state", "result" (if it succeeded), "error" (if it failed)}.
  States: PENDING (queued, or unknown id), STARTED, SUCCESS, FAILURE (RETRY and REVOKED are not used yet).
  """
  result = AsyncResult(job_id, app=celery_app)
  status = {"id": job_id, "state": result.state}
  if result.state == 'SUCCESS':
    status["result"] = result.result
  elif result.state == 'FAILURE':
    status["error"] = f"{type(result.result).__name__}: {result.result}"
  return status
