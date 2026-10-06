"""
The commands, options and environment variables of `qa` are an API: CI scripts, the web app and users rely on them.
This test fails if they change, so that it's never by accident. To accept a change:
  python tests/test_cli_surface.py --update
"""
import os
import sys
import json
import unittest
import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SNAPSHOT = Path(__file__).parent / 'cli_surface.json'

# In a new process: importing qaboard here would load its configuration from the wrong directory for other tests
DUMP = """
import json
import typer.main
from qaboard.cli import app

def dump(command, envvar_prefix):
  surface = {
    "context_settings": {k: v for k, v in sorted(command.context_settings.items()) if k in ("ignore_unknown_options", "allow_interspersed_args")},
    "params": {},
  }
  for param in command.params:
    if param.name in ("help", "version", "install_completion", "show_completion"):
      continue
    is_option = param.param_type_name == "option"
    surface["params"][param.name] = {
      "kind": param.param_type_name,
      "opts": sorted(param.opts),
      "multiple": getattr(param, "multiple", False),
      "is_flag": getattr(param, "is_flag", False),
      "nargs": param.nargs,
      "required": param.required,
      # Options can be set with environment variables named after the command and the parameter's python name
      "envvar": f"{envvar_prefix}_{param.name}".upper() if is_option else None,
    }
  if hasattr(command, "commands"):
    surface["commands"] = {
      name: dump(subcommand, f"{envvar_prefix}_{name.replace('-', '_')}")
      for name, subcommand in sorted(command.commands.items())
    }
  return surface

print(json.dumps(dump(typer.main.get_command(app), "QA"), indent=2, sort_keys=True))
"""


def current_surface() -> str:
  env = {k: v for k, v in os.environ.items() if not k.startswith('QA_')}
  env.update({'PYTHONPATH': str(ROOT), 'QA_NO_CHECK_FOR_UPDATES': '1', 'QABOARD_HOST': 'localhost'})
  out = subprocess.run([sys.executable, '-c', DUMP], cwd=ROOT / 'qaboard' / 'sample_project', env=env, capture_output=True, text=True)
  if out.returncode:
    raise RuntimeError(out.stderr)
  return out.stdout


class TestCliSurface(unittest.TestCase):
  def test_surface_did_not_change(self):
    expected = json.loads(SNAPSHOT.read_text())
    current = json.loads(current_surface())
    self.maxDiff = None
    self.assertEqual(current, expected, f"The CLI changed. If it's on purpose, run: python {Path(__file__).relative_to(ROOT)} --update")


if __name__ == '__main__':
  if '--update' in sys.argv:
    SNAPSHOT.write_text(current_surface())
    print(f"Updated {SNAPSHOT}")
  else:
    unittest.main()
