"""
Unit tests for the background tasks (backend/tasks.py) and the endpoints that queue them (backend/api/jobs.py)
"""
import threading
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest


class FakeOutput:
  def __init__(self, id, error=None):
    self.id = id
    self.error = error

  def write_redo_script(self, user, command_id):
    if self.error:
      raise self.error
    return Path(f"/mnt/qaboard/outputs/{self.id}/redo.sh")


@pytest.fixture
def tasks(monkeypatch):
  import backend.tasks as tasks
  session = MagicMock()
  monkeypatch.setattr(tasks, "Session", lambda: session)
  def with_outputs(*outputs):
    session.query.return_value.filter.return_value = list(outputs)
    return session
  tasks.with_outputs = with_outputs
  yield tasks
  del tasks.with_outputs


def test_redo_outputs_reports_each_failure(tasks, monkeypatch):
  session = tasks.with_outputs(FakeOutput(1), FakeOutput(2, error=ValueError("unsafe path")), FakeOutput(3))
  submitted = []
  def submit(user, script_path):
    submitted.append((user, script_path))
    return "/3/" not in str(script_path)
  monkeypatch.setattr(tasks, "submit_redo_script", submit)

  result = tasks.redo_outputs([1, 2, 3, 4], user="arthurf")

  assert result["started"] == 1
  errors = {f["id"]: f["error"] for f in result["failed"]}
  assert errors[2] == "unsafe path"
  assert "/mnt/qaboard/outputs/3/log.txt" in errors[3]
  assert errors[4] == "Not found"
  assert sorted(submitted) == [("arthurf", Path("/mnt/qaboard/outputs/1/redo.sh")), ("arthurf", Path("/mnt/qaboard/outputs/3/redo.sh"))]
  session.close.assert_called_once()


def test_redo_outputs_submits_in_parallel(tasks, monkeypatch):
  tasks.with_outputs(FakeOutput(1), FakeOutput(2))
  # Each submission waits for the other: it only passes if they run at the same time
  barrier = threading.Barrier(2, timeout=10)
  def submit(user, script_path):
    barrier.wait()
    return True
  monkeypatch.setattr(tasks, "submit_redo_script", submit)
  monkeypatch.setattr(tasks, "redo_concurrency", 2)
  assert tasks.redo_outputs([1, 2], user="arthurf") == {"started": 2, "failed": []}


def test_redo_outputs_survives_submission_errors(tasks, monkeypatch):
  tasks.with_outputs(FakeOutput(1))
  def submit(user, script_path):
    raise ValueError("Unsafe user name")
  monkeypatch.setattr(tasks, "submit_redo_script", submit)
  assert tasks.redo_outputs([1], user="a'b") == {"started": 0, "failed": [{"id": 1, "error": "Unsafe user name"}]}


@pytest.fixture
def redo_task(monkeypatch):
  import backend.api.jobs as jobs
  task = MagicMock()
  task.delay.return_value = SimpleNamespace(id="job-1")
  monkeypatch.setattr(jobs, "redo_outputs", task)
  yield task


def test_queue_redo_returns_the_job(dummy_app, redo_task):
  from backend.api.jobs import queue_redo
  with dummy_app.test_request_context('/api/v1/batch/redo/', method='POST'):
    response, status = queue_redo([1, 2], user="arthurf")
  assert status == 202
  assert response.get_json() == {"status": "queued", "outputs": 2, "job_id": "job-1"}
  redo_task.delay.assert_called_once_with([1, 2], user="arthurf")


def test_queue_redo_without_outputs(dummy_app, redo_task):
  from backend.api.jobs import queue_redo
  with dummy_app.test_request_context('/api/v1/batch/redo/', method='POST'):
    response = queue_redo([], user="arthurf")
  assert response.get_json() == {"status": "OK", "outputs": 0}
  redo_task.delay.assert_not_called()


def test_queue_redo_when_the_queue_is_down(dummy_app, redo_task):
  from backend.api.jobs import queue_redo
  redo_task.delay.side_effect = ConnectionError("redis is down")
  with dummy_app.test_request_context('/api/v1/batch/redo/', method='POST'):
    response, status = queue_redo([1], user="arthurf")
  assert status == 503
  assert "try again later" in response.get_json()["error"]
  assert "redis is down" not in response.get_json()["error"]


def test_job_status(monkeypatch):
  import backend.celery_app as celery_app
  results = {
    "a": SimpleNamespace(state="PENDING", result=None),
    "b": SimpleNamespace(state="SUCCESS", result={"started": 1, "failed": []}),
    "c": SimpleNamespace(state="FAILURE", result=ValueError("boom")),
  }
  monkeypatch.setattr(celery_app, "AsyncResult", lambda job_id, app: results[job_id])
  assert celery_app.job_status("a") == {"id": "a", "state": "PENDING"}
  assert celery_app.job_status("b") == {"id": "b", "state": "SUCCESS", "result": {"started": 1, "failed": []}}
  assert celery_app.job_status("c") == {"id": "c", "state": "FAILURE", "error": "ValueError: boom"}


def test_tasks_are_registered_with_json_arguments():
  from backend.celery_app import celery_app
  import backend.tasks  # noqa: F401
  assert "qaboard.redo_outputs" in celery_app.tasks
  assert celery_app.conf.task_serializer == "json"
  assert celery_app.conf.accept_content == ["json"]
  # Web processes don't subscribe to the results of the tasks they queue
  assert type(celery_app.backend).__name__ == "PollingRedisBackend"
  celery_app.backend.on_task_call(producer=None, task_id="abc")
  assert not celery_app.backend.result_consumer.subscribed_to
