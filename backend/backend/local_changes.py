"""
How much runs may record of their local changes (docs/rfcs/local-changes.md): the `runs.local_changes` setting.

- "patch" (the default): the changed files' summary, and the patches as files of the run (.qaboard/*.patch)
- "summary": the summary only, no code: what a server that must not receive source code allows
- "off": nothing

We keep what runs send in `data.local_changes` up to that level, refuse the patches they upload above it, and tell
them the level when they start, so that the ones writing in a storage shared with us don't write patches either.
"""
import json
from typing import Any, Dict, Optional

from qaboard.local_changes import LEVELS, FOLDER, at_level

from .settings import setting

# What a run's local changes take at most in the database, as JSON: 500 files' paths and counts fit
MAX_STATE_SIZE = 200_000


def level() -> str:
  value = setting('runs.local_changes')
  return value if value in LEVELS else 'patch'


def allowed_data(data: Dict[str, Any], current: Optional[str] = None) -> Dict[str, Any]:
  """What we keep of the local changes a run sends in its `data` (changed in place, and returned)"""
  current = current or level()
  if 'local_changes' not in data:
    if current == 'off':
      data.pop('dirty', None)
    return data
  state = data['local_changes']
  if current == 'off' or not isinstance(state, dict) or len(json.dumps(state, default=str)) > MAX_STATE_SIZE:
    data.pop('local_changes')
    if current == 'off':
      data.pop('dirty', None)
    return data
  data['local_changes'] = at_level(state, current)
  return data


def refuses_file(path: str, current: Optional[str] = None) -> bool:
  """Whether a run can't send us this file of its folder: its patches, above the level"""
  current = current or level()
  return current != 'patch' and path.split('/', 1)[0] == FOLDER and path.endswith('.patch')
