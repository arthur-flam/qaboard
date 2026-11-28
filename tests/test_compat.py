import json
import os
from pathlib import Path
from unittest import mock

import unittest


# Mappings like a site's (e.g. in its site package: deployments/example/cli/)
SITE_MAPPINGS = json.dumps([
    ["\\\\fileserver\\projects_ws", "/projects/ws"],
    ["\\\\fileserver\\vol1_projects", "/projects"],
    ["\\\\fileserver\\vol2_projects", "/projects"],
    ["\\\\nas\\raid\\projects", "/projects"],
    ["\\\\nas\\raid", "/raid"],
])
# The inputs of /projects/<group>/inputs are on another volume, shared on Windows as <group>_inputs
SITE_REWRITES = json.dumps([
    [r"^\\\\fileserver\\vol1_projects\\([^\\]+)[\\_]inputs", r"\\\\fileserver\\vol2_projects\\\1_inputs"],
])


def _reload_compat():
    """Reload compat module so it re-reads the env var for mappings."""
    import importlib
    import qaboard.compat
    importlib.reload(qaboard.compat)
    return qaboard.compat


class TestCompatWithSiteMappings(unittest.TestCase):
    """Tests with a site's path mappings and rewrites injected via env vars."""

    @classmethod
    def setUpClass(cls):
        os.environ['QABOARD_PATH_MAPPINGS'] = SITE_MAPPINGS
        os.environ['QABOARD_PATH_REWRITES'] = SITE_REWRITES
        cls.compat = _reload_compat()

    @classmethod
    def tearDownClass(cls):
        del os.environ['QABOARD_PATH_MAPPINGS']
        del os.environ['QABOARD_PATH_REWRITES']
        _reload_compat()

    def test_to_linux(self):
        w2l = self.compat.windows_to_linux
        self.assertEqual(w2l(r'\\fileserver\projects_ws'), '/projects/ws')
        self.assertEqual(w2l(r'\\fileserver\projects_ws\test'), '/projects/ws/test')
        self.assertEqual(w2l(r'\\fileserver\projects_ws\test' + '\\'), '/projects/ws/test/')
        self.assertEqual(w2l(r"\\nas\raid\projects\test"), '/projects/test')
        self.assertEqual(w2l(r'//fileserver/projects_ws'), '/projects/ws')
        # case insensitivity
        self.assertEqual(w2l(r'\\FILESERVER\projects_ws\test'), '/projects/ws/test')
        self.assertEqual(w2l(r'\\FIleserver\projects_ws\TEST'), '/projects/ws/TEST')
        self.assertEqual(w2l(r'\\FIleserver\projects_ws\FIleserver'), '/projects/ws/FIleserver')

    def test_to_windows(self):
        l2w = self.compat.linux_to_windows
        self.assertEqual(l2w('/projects/ws'), r'\\fileserver\projects_ws')
        self.assertEqual(l2w('/projects/ws/hello/there'), r'\\fileserver\projects_ws\hello\there')
        self.assertEqual(l2w('/projects/ws/hello/there/'), '\\\\fileserver\\projects_ws\\hello\\there\\')
        # the first mapping of /projects, then the rewrites
        self.assertEqual(l2w('/projects/CIS/outputs/test'), r'\\fileserver\vol1_projects\CIS\outputs\test')
        self.assertEqual(l2w('/projects/CIS/inputs/test'), r'\\fileserver\vol2_projects\CIS_inputs\test')
        self.assertEqual(l2w('/projects/CIS_inputs/test'), r'\\fileserver\vol2_projects\CIS_inputs\test')

    def test_to_path(self):
        w2l_path = self.compat.windows_to_linux_path
        l2w_path = self.compat.linux_to_windows_path
        self.assertEqual(w2l_path(Path(r'\\fileserver\projects_ws')), Path('/projects/ws'))
        self.assertEqual(l2w_path(Path('/projects/ws')), Path(r'\\fileserver\projects_ws'))


class TestCompatNoMappings(unittest.TestCase):
    """Tests with no path mappings configured (open-source default)."""

    @classmethod
    def setUpClass(cls):
        # Ensure no mappings env var is set
        os.environ.pop('QABOARD_PATH_MAPPINGS', None)
        cls.compat = _reload_compat()
        # Mappings can also come from a site package (e.g. qaboard-site-example)
        # installed on the machine, which site_config() falls back to when the
        # env var is absent. Force an empty mapping table so these tests
        # deterministically exercise the no-mappings passthrough behavior.
        for name in ('mappings', 'rewrites'):
            patcher = mock.patch.object(cls.compat, name, ())
            patcher.start()
            cls.addClassCleanup(patcher.stop)

    @classmethod
    def tearDownClass(cls):
        _reload_compat()

    def test_windows_to_linux_passthrough(self):
        """With no mappings, windows_to_linux only converts separators."""
        w2l = self.compat.windows_to_linux
        self.assertEqual(w2l(r'\\fileserver\projects_ws'), '//fileserver/projects_ws')
        self.assertEqual(w2l(r'\\server\share\file.txt'), '//server/share/file.txt')

    def test_linux_to_windows_passthrough(self):
        """With no mappings, linux_to_windows only converts separators."""
        l2w = self.compat.linux_to_windows
        self.assertEqual(l2w('/some/path/file.txt'), r'\some\path\file.txt')

    def test_fix_linux_permissions_noop(self):
        """With no hook installed, fix_linux_permissions is a no-op."""
        # Should not raise, just print a message
        self.compat.fix_linux_permissions(Path('/tmp/test'))


if __name__ == '__main__':
    unittest.main()


class TestVersion(unittest.TestCase):
  def test_version_matches_pyproject(self):
    # From a checkout, qaboard.__version__ is its pyproject's, even if another qaboard is installed (CI's deployed CLI)
    import re
    import qaboard
    pyproject = (Path(__file__).resolve().parent.parent / 'pyproject.toml').read_text()
    version = re.search(r'^version = "([^"]+)"', pyproject, re.MULTILINE).group(1)
    self.assertEqual(qaboard.__version__, version)
