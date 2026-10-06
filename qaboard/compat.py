"""
Deprecation warnings, backward compatibility, Windows compatibility
"""
import re
import os
import sys
import json
from pathlib import Path

import typer

from qaboard.site_config import site_config, site_entry_points


def ensure_cli_backward_compatibility():
    """Handle deprecate flag names here"""
    renamings = (
        ('--input-path', '--input'),
        ('--output-path', '--output'),
        ('save_artifacts', 'save-artifacts'),
        ('check_bit_accuracy', 'check-bit-accuracy'),
        ('--reference-branch', '--reference'),
        ('--batch-label', '--label'),
        ('--inputs-database', '--database'),
        ('--inputs-globs', 'REMOVED: Use "inputs.types" in qaboard.yaml'),
        ('--save-manifests', '--save-manifests-in-database'),
        ('--return-prefix-outputs-path', '--list-output-dirs'),
        ('--ci', '--share'),
        ('--dry-run', '--dryrun'),
        ('--lsf-memory', '--lsf-max-memory'),
        ('--group', '--batch'),
        ('--groups-file', '--batches-file'),
        ('--no-qa-database', '--offline'),
    )
    def renamed_deprecated(arg):
        for before, after in renamings:
            if arg == before:
                typer.secho(f'[DEPRECATION WARNING]: "{before}" was replaced by "{after}" and will be removed in a future release.', fg='yellow')
                return after
        return arg
    sys.argv = [renamed_deprecated(arg) for arg in sys.argv]
    if '--lsf-sequential' in sys.argv:
        typer.secho('[DEPRECATION WARNING]: "--lsf-sequential" was replaced with "--runner local"', fg='yellow', bold=True)



def cased_path(path):
    # Adapted from
    # https://stackoverflow.com/questions/3692261/in-python-how-can-i-get-the-correctly-cased-path-for-a-file/14742779#14742779
    if os.name != 'nt':
      return path
    import glob
    dirs = str(path).split('\\')
    # For absolute paths with drive names ("\\host\volume\..."), we must have the correct case at least at the beginning...
    # Still, then, we could always call .upper() if the length of the first part is 1 (drive letter..)
    if not dirs[0] and not dirs[1]:
      dirs = [f'\\\\{dirs[2]}\\{dirs[3]}', *dirs[4:]]
      test_name = [dirs[0]]
    elif not dirs[0]: # absolute paths like "\c\Users\..."
      dirs = [f'\\{dirs[1]}', *dirs[3:]]
      test_name = [dirs[0]]      
    elif dirs[0].endswith(':'): # e.g. C:\\
      test_name = [dirs[0]]
    else: # relative paths
      test_name = ["%s[%s]" % (dirs[0][:-1], dirs[0][-1])]
    for d in dirs[1:]:
        test_name += ["%s[%s]" % (d[:-1], d[-1])]
    res = glob.glob('\\'.join(test_name))
    if not res: #File not found
        return None
    return Path(res[0])



def escaped_for_cli(string):
  # we assume single_quotes are already escaped
  if os.name == 'nt':
    string_escaped = string.replace('\\', '\\\\')
    string_escaped = string_escaped.replace('"', '\\"')
    string_escaped = string_escaped.replace('|', '^|')
    return f'"{string_escaped}"'
  else:
    return 

def _load_path_mappings():
  """Load path mappings from site config (env var or site package)."""
  raw = site_config("QABOARD_PATH_MAPPINGS", "[]")
  try:
    parsed = json.loads(raw)
    return tuple(tuple(pair) for pair in parsed)
  except (json.JSONDecodeError, TypeError):
    return ()

mappings = _load_path_mappings()

# TODO: ideally parameterizable, but too painful and near-zero chance of name collision
re_algo_inputs = re.compile(r"\\\\netapp\\vol23_algo\\([^\\]+)[\\_]inputs")


def _after_prefix(path: str, prefix: str, sep: str, ignore_case: bool = False):
  """The rest of `path` if it is `prefix` or inside it (/algo matches /algo/x, not /algo2), else None."""
  prefix = prefix.rstrip(sep)
  head = path[:len(prefix)]
  if (head.lower() == prefix.lower()) if ignore_case else (head == prefix):
    rest = path[len(prefix):]
    if not rest or rest.startswith(sep):
      return rest
  return None


def windows_to_linux(path : str) -> str:
  path = path.replace('/', '\\')
  for path_windows, path_linux in mappings:
    rest = _after_prefix(path, path_windows, '\\', ignore_case=True)
    if rest is not None:
      path = path_linux.rstrip('/') + rest
      break
  return path.replace('\\', '/')

def linux_to_windows(path : str) -> str:
  for path_windows, path_linux in mappings:
    rest = _after_prefix(path, path_linux, '/')
    if rest is not None:
      path = path_windows.rstrip('\\') + rest
      break
  path = path.replace('/', '\\')
  match_algo_inputs = re_algo_inputs.match(path)
  if match_algo_inputs:
    # /algo/CIS/inputs is a symlink to /algo/CIS_inputs, we prefer the later
    # /algo is split into multiple volumes, it is not as transparent on windows as on linux
    path = rf"\\netapp\vol24_algo\{match_algo_inputs.group(1)}_inputs{path[match_algo_inputs.end():]}"
  return path


def windows_to_linux_path(path : Path) -> Path:
  return Path(windows_to_linux(str(path)))

def linux_to_windows_path(path : Path) -> Path:
  return Path(linux_to_windows(str(path)))


def fix_linux_permissions(path: Path):
  """Dispatch to a site-specific fix_permissions hook, if installed."""
  try:
    eps = site_entry_points("qaboard.hooks")
    for ep in eps:
      if ep.name == "fix_permissions":
        hook = ep.load()
        hook(path)
        return
  except Exception as e:
    typer.secho(f'WARNING: fix_permissions hook failed: {e}', err=True)
    return
  typer.secho("... No fix_permissions hook installed, skipping", err=True, fg='yellow')
