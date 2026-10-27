import unittest
from pathlib import PureWindowsPath
from unittest import mock


class TestBatchInfo(unittest.TestCase):
  def test_windows_project_uses_forward_slashes(self):
    from qaboard.api import batch_info
    response = mock.Mock()
    response.json.return_value = {"batches": {"default": {"outputs": {}}}}
    with mock.patch("requests.get", return_value=response) as get:
      batch_info("0123456789", "default", project=PureWindowsPath("group/subgroup/repo") / "subproject", metrics=["none"])
    self.assertEqual(get.call_args.kwargs["params"]["project"], "group/subgroup/repo/subproject")


class TestNotifyQaDatabase(unittest.TestCase):
  def test_failed_request_does_not_print_the_token(self):
    import io
    import requests
    from contextlib import redirect_stderr
    from qaboard.api import notify_qa_database
    response = mock.Mock()
    response.raise_for_status.side_effect = requests.HTTPError("401 Client Error")
    response.status_code, response.text = 401, "You need to be logged-in to do this."
    response.request.headers = {"Content-Type": "application/json", "Authorization": "Bearer secret-token"}
    stderr = io.StringIO()
    with mock.patch("requests.post", return_value=response), redirect_stderr(stderr):
      notify_qa_database(object_type="batch", share=True, batch_label="default")
    self.assertIn("Failed to update QA-Board", stderr.getvalue())
    self.assertIn("Authorization", stderr.getvalue())
    self.assertNotIn("secret-token", stderr.getvalue())

  def test_requests_have_a_timeout(self):
    """A hung server must not block CI jobs forever"""
    from qaboard.api import notify_qa_database, get_output, batch_info, api_timeout
    self.assertGreaterEqual(api_timeout[1], 300) # uwsgi's harakiri: saving big manifests can take that long
    response = mock.Mock()
    response.json.return_value = {"batches": {"default": {"outputs": {}}}}
    with mock.patch("requests.post", return_value=response) as post, mock.patch("requests.get", return_value=response) as get:
      notify_qa_database(object_type="batch", share=True, batch_label="default")
      get_output(1)
      batch_info("0123456789", "default", project="group/repo", metrics=["none"])
    self.assertEqual(post.call_args.kwargs["timeout"], api_timeout)
    self.assertEqual([c.kwargs["timeout"] for c in get.call_args_list], [api_timeout, api_timeout])

  def test_data_that_does_not_serialize(self):
    import io
    from contextlib import redirect_stderr
    from qaboard.api import notify_qa_database
    stderr = io.StringIO()
    with mock.patch("requests.post") as post, redirect_stderr(stderr):
      notify_qa_database(object_type="batch", share=True, batch_label="default", data={"not-json": object()})
    post.assert_not_called()
    self.assertIn("Failed to update QA-Board", stderr.getvalue())
    self.assertIn("not-json", stderr.getvalue())


class TestNotifyQaDatabaseRuns(unittest.TestCase):
  """`qa batch` sends the runs it's about to start in a few calls (POST /api/v1/outputs/), not one per run"""
  shared = dict(share=True, batch_label="default", database="/data", platform="linux", user="ci")
  runs = [{"rel_input_path": f"a/{i}.raw", "database": "/data", "is_pending": True} for i in range(5)]

  def response(self, status, body):
    response = mock.Mock(status_code=status, headers={})
    response.json.return_value = body
    return response

  def sent(self, post):
    import json
    return [json.loads(c.kwargs["data"]) for c in post.call_args_list]

  def test_one_call(self):
    from qaboard.api import notify_qa_database_runs, notify_qa_database
    answers = [{"id": i} for i in range(4)] + [{"error": "the input path was not provided", "status": 400}]
    with mock.patch("requests.post", return_value=self.response(200, answers)) as post:
      self.assertEqual(notify_qa_database_runs(self.runs, **self.shared), [{"id": i} for i in range(4)] + [None])
    [bulk] = self.sent(post)
    self.assertTrue(post.call_args.args[0].endswith("/api/v1/outputs/"))
    # runs only send what they don't share: with it, each is what it would send on its own
    self.assertEqual(bulk["runs"][0], {"rel_input_path": "a/0.raw", "is_pending": True})
    shared = {k: v for k, v in bulk.items() if k != "runs"}
    with mock.patch("requests.post", return_value=self.response(200, {"id": 0})) as post:
      for run in self.runs:
        notify_qa_database(**{**self.shared, **run})
    self.assertEqual([{**shared, **run} for run in bulk["runs"]], self.sent(post))

  def test_many_runs(self):
    from qaboard.api import notify_qa_database_runs
    def answer(url, data, **kwargs):
      import json
      return self.response(200, [{"id": run["rel_input_path"]} for run in json.loads(data)["runs"]])
    with mock.patch("qaboard.api.runs_per_call", 2), mock.patch("requests.post", side_effect=answer) as post:
      self.assertEqual(notify_qa_database_runs(self.runs, **self.shared), [{"id": run["rel_input_path"]} for run in self.runs])
    self.assertEqual([len(call["runs"]) for call in self.sent(post)], [2, 2, 1])

  def test_older_servers(self):
    """They don't know /api/v1/outputs/: one call per run"""
    from qaboard.api import notify_qa_database_runs
    def answer(url, data, **kwargs):
      return self.response(404, {"error": "Not Found"}) if url.endswith("/outputs/") else self.response(200, {"id": 1})
    with mock.patch("requests.post", side_effect=answer) as post:
      self.assertEqual(notify_qa_database_runs(self.runs, **self.shared), [{"id": 1}] * 5)
    self.assertEqual(len(post.call_args_list), 1 + 5)
    self.assertEqual(self.sent(post)[1]["rel_input_path"], "a/0.raw")

  def test_offline(self):
    from qaboard.api import notify_qa_database_runs
    with mock.patch("requests.post") as post:
      self.assertEqual(notify_qa_database_runs(self.runs, **{**self.shared, "offline": True}), [None] * 5)
    post.assert_not_called()


class TestServerUrl(unittest.TestCase):
  def stderr_of_import(self, **env):
    import os
    import sys
    import subprocess
    import tempfile
    clean_env = {k: v for k, v in os.environ.items() if not k.startswith(('QABOARD_', 'QA_'))}
    # Without the defaults of an installed site package (e.g. qaboard-site-sirc gives the server's URL)
    without_site_packages = (
      "import importlib.metadata as m; entry_points = m.entry_points\n"
      "class NoSite:\n  def select(self, **kwargs): return []\n  def get(self, group, default=None): return []\n"
      "m.entry_points = lambda **kwargs: entry_points(**kwargs) if kwargs else NoSite()\n"
      "import qaboard.api\n"
    )
    with tempfile.TemporaryDirectory() as tmp:
      process = subprocess.run(
        [sys.executable, '-c', without_site_packages],
        cwd=tmp, capture_output=True, text=True, check=True,
        # secrets files can also say where the server is
        env={**clean_env, 'QA_NO_CHECK_FOR_UPDATES': '1', 'QA_USER_SECRETS': f'{tmp}/secrets.yaml', **env},
      )
    return process.stderr

  def test_warns_without_a_server(self):
    self.assertIn('not sure where to find your QA-Board server', self.stderr_of_import())

  def test_no_warning_for_a_server_on_localhost(self):
    self.assertNotIn('not sure where', self.stderr_of_import(QABOARD_URL='http://localhost:5151'))


if __name__ == '__main__':
  unittest.main()
