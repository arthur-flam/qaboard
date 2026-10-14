import os
import sys
from pathlib import Path

# Test this checkout, not a `qaboard` that is installed (e.g. the deployed CLI), also in subprocesses
root = str(Path(__file__).resolve().parent.parent)
sys.path.insert(0, root)
os.environ['PYTHONPATH'] = os.pathsep.join([root, *filter(None, [os.environ.get('PYTHONPATH')])])


def fresh_qaboard():
  """
  Imports qaboard again, with all its modules: its configuration is loaded at import time from the current directory.
  Tests that run `qa` in-process need it, since other tests may have imported qaboard from another directory.
  """
  import importlib
  for name in [m for m in sys.modules if m == 'qaboard' or m.startswith('qaboard.')]:
    del sys.modules[name]
  return importlib.import_module('qaboard')
