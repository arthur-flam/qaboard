"""
Bit-accuracy comparisons of output files.
"""
import os
import sys
import unittest
import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent

# In a new process: importing qaboard here would load its configuration from the wrong directory for other tests
CHECK = """
import tempfile
from pathlib import Path
from qaboard.bit_accuracy import default_cmp, cmpfiles

with tempfile.TemporaryDirectory() as tmp:
  new, ref = Path(tmp) / 'new', Path(tmp) / 'ref'
  for d in (new, ref):
    d.mkdir()
    (d / 'same.txt').write_text('same')
  (new / 'changed.txt').write_text('new')
  (ref / 'changed.txt').write_text('ref')
  (new / 'only-new.txt').write_text('')
  assert default_cmp(new / 'same.txt', ref / 'same.txt') is True
  assert default_cmp(new / 'changed.txt', ref / 'changed.txt') is False
  # Without manifests, output directories are compared file by file
  comparison = cmpfiles(dir_1=new, dir_2=ref)
  assert comparison['mismatch'] == {Path('changed.txt')}, comparison
  assert comparison['match'] == {Path('same.txt')}, comparison
  assert comparison['only_in_1'] == {Path('only-new.txt')}, comparison
print('OK')
"""


class TestBitAccuracy(unittest.TestCase):
  def test_compare_files(self):
    env = {k: v for k, v in os.environ.items() if not k.startswith('QA_')}
    env.update({'PYTHONPATH': str(ROOT), 'QA_NO_CHECK_FOR_UPDATES': '1', 'QABOARD_HOST': 'localhost'})
    out = subprocess.run([sys.executable, '-c', CHECK], cwd=ROOT / 'qaboard' / 'sample_project', env=env, capture_output=True, text=True)
    self.assertEqual(out.returncode, 0, out.stderr)


if __name__ == '__main__':
  unittest.main()
