"""
Inputs given as absolute paths, in batches or with `qa run --input`, also on Windows.
"""
import os
import sys
import json
import unittest
import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent

# In a new process: importing qaboard here would load its configuration from the wrong directory for other tests
CHECK = r"""
from pathlib import PurePosixPath, PureWindowsPath
from qaboard.iterators import split_absolute, local_input_path

W = PureWindowsPath
# On Windows, paths without a drive are not is_absolute(), but can't be globbed from a database either
assert split_absolute(W('/algo/inputs/*.raw')) == (W('\\'), W('algo/inputs/*.raw'))
assert split_absolute(W('\\algo\\inputs')) == (W('\\'), W('algo/inputs'))
assert split_absolute(W('C:\\data\\a.jpg')) == (W('C:\\'), W('data/a.jpg'))
assert split_absolute(W('\\\\netapp\\share\\a.jpg')) == (W('\\\\netapp\\share\\'), W('a.jpg'))
assert split_absolute(PurePosixPath('/algo/inputs')) == (PurePosixPath('/'), PurePosixPath('algo/inputs'))

# On Windows, Linux paths are mapped with QABOARD_PATH_MAPPINGS
assert local_input_path('/algo/inputs/a.jpg', windows=True) == '\\\\netapp\\algo\\inputs\\a.jpg', local_input_path('/algo/inputs/a.jpg', windows=True)
assert local_input_path('/other/a.jpg', windows=True) == '\\other\\a.jpg'
assert local_input_path('/algo/inputs/a.jpg', windows=False) == '/algo/inputs/a.jpg'
assert local_input_path('relative/a.jpg', windows=True) == 'relative/a.jpg'
# on the CLI, Windows already turned /algo/x into \algo\x
assert local_input_path('\\algo\\inputs\\a.jpg', windows=True) == '\\\\netapp\\algo\\inputs\\a.jpg'
assert local_input_path('\\\\netapp\\algo\\a.jpg', windows=True) == '\\\\netapp\\algo\\a.jpg'
assert local_input_path('C:\\algo\\a.jpg', windows=True) == 'C:\\algo\\a.jpg'

# Linux: databases are left alone
from pathlib import Path
from qaboard.iterators import local_database
assert local_database('/algo/db', windows=False) == Path('/algo/db')
assert local_database('db', windows=False) == Path('db')

# Windows: fixing the case of paths
from qaboard.compat import cased_path_pattern
assert cased_path_pattern('\\algo\\inputs\\foo') == '\\alg[o]\\input[s]\\fo[o]', cased_path_pattern('\\algo\\inputs\\foo')
assert cased_path_pattern('C:\\Data\\a1.jpg') == 'C:\\Dat[a]\\a1.jp[g]'
assert cased_path_pattern('\\\\netapp\\share\\[1].raw') == '\\\\netapp\\share\\[[]1].ra[w]'
assert cased_path_pattern('rel\\dir\\f\\') == 're[l]\\di[r]\\[f]'
assert cased_path_pattern('1\\2') == '1\\2'
print('OK')
"""


class TestInputPaths(unittest.TestCase):
  def test_absolute_paths(self):
    env = {k: v for k, v in os.environ.items() if not k.startswith('QA_')}
    env.update({
      'PYTHONPATH': str(ROOT), 'QA_NO_CHECK_FOR_UPDATES': '1', 'QABOARD_HOST': 'localhost',
      'QABOARD_PATH_MAPPINGS': json.dumps([["\\\\netapp\\algo", "/algo"]]),
    })
    out = subprocess.run([sys.executable, '-c', CHECK], cwd=ROOT / 'qaboard' / 'sample_project', env=env, capture_output=True, text=True)
    self.assertEqual(out.returncode, 0, out.stderr)


if __name__ == '__main__':
  unittest.main()
