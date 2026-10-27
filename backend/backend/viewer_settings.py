"""
What a project's developers and owners save from the web app for everyone who looks at its results, in
Project.data['settings']: /api/v1/project answers with it. Today, the sensor of the project's raw images, for the
image viewer's analysis (webapp/src/viewers/images/projectSensor.js):
  {"sensor": {"kind": "mosaic", "pattern": "GRBG", "block": 2, "offset": {"x": 0, "y": 0}, "black_level": 64,
              "updated_by": {"user_name": "bob", "full_name": "Bob B"}, "updated_at": "2026-10-27T10:00:00Z"}}
Every field is optional. For each value the viewer uses, in this order: what users set in their browser, the image's
metadata (sensor.py), the project's, then what it guesses from the pixels.
"""
import datetime
import math
from typing import Any, Optional, Tuple

from sqlalchemy.orm.attributes import flag_modified


PATTERNS = ('RGGB', 'GRBG', 'GBRG', 'BGGR')
# the image viewer's largest cells (webapp/src/image-viewer/cfa.js)
MAX_BLOCK = 9
MAX_BLACK_LEVEL = 2 ** 32


def _integer(value, minimum, maximum) -> Optional[int]:
  if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value) or value != int(value):
    return None
  value = int(value)
  return value if minimum <= value <= maximum else None


def clean_sensor(sensor: Any) -> Tuple[Optional[dict], Optional[str]]:
  """
  (the sensor to save, None) or (None, the error). `null`, or nothing set, forgets it: (None, None).
  Unknown fields are ignored. "auto" (the default) isn't saved.
  """
  if sensor is None:
    return None, None
  if not isinstance(sensor, dict):
    return None, "`sensor` must be an object, or null to forget it"
  cleaned: dict = {}
  kind = sensor.get('kind')
  if kind not in (None, 'auto', 'mosaic', 'mono'):
    return None, '`sensor.kind` must be "auto", "mosaic" or "mono"'
  if kind in ('mosaic', 'mono'):
    cleaned['kind'] = kind
  pattern = sensor.get('pattern')
  if pattern is not None:
    if not isinstance(pattern, str) or pattern.upper() not in PATTERNS:
      return None, f"`sensor.pattern` must be one of {', '.join(PATTERNS)}"
    cleaned['pattern'] = pattern.upper()
  if sensor.get('block') is not None:
    block = _integer(sensor['block'], 1, MAX_BLOCK)
    if block is None:
      return None, f"`sensor.block` must be the cells' size, from 1 (Bayer) to {MAX_BLOCK}"
    cleaned['block'] = block
  offset = sensor.get('offset')
  if offset is not None:
    # where the cells start: within a period of the pattern, 2 cells
    largest = 2 * cleaned.get('block', MAX_BLOCK) - 1
    xy = {axis: _integer(offset.get(axis, 0), 0, largest) for axis in ('x', 'y')} if isinstance(offset, dict) else {}
    if not xy or None in xy.values():
      return None, f"`sensor.offset` must be {{\"x\", \"y\"}}, integers from 0 to {largest}"
    cleaned['offset'] = xy
  black_level = sensor.get('black_level')
  if black_level is not None:
    if isinstance(black_level, bool) or not isinstance(black_level, (int, float)) or not math.isfinite(black_level) or not 0 <= black_level < MAX_BLACK_LEVEL:
      return None, "`sensor.black_level` must be a number, 0 or more"
    cleaned['black_level'] = int(black_level) if float(black_level).is_integer() else black_level
  return (cleaned or None), None


def utc_now() -> str:
  return datetime.datetime.now(datetime.timezone.utc).strftime('%Y-%m-%dT%H:%M:%SZ')


def save_setting(session, project, key: str, value: Optional[dict], user_info: dict) -> dict:
  """
  Saves one of the project's settings (None removes it), with who saved it and when, and commits. Returns the
  project's settings. The row is locked: we don't lose what's saved meanwhile in its data, e.g. by `qa` runs.
  """
  session.refresh(project, with_for_update=True)
  data = dict(project.data or {})
  settings = dict(data.get('settings') or {})
  if value is None:
    settings.pop(key, None)
  else:
    settings[key] = {
      **value,
      'updated_by': {'user_name': user_info.get('user_name'), 'full_name': user_info.get('full_name')},
      'updated_at': utc_now(),
    }
  if settings:
    data['settings'] = settings
  else:
    data.pop('settings', None)
  project.data = data
  flag_modified(project, 'data')
  session.commit()
  return settings
