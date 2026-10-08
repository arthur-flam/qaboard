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


if __name__ == '__main__':
  unittest.main()
