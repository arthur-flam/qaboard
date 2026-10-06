"""
Helpers for `qa init`, which is implemented in qaboard/wizard: showing the site defaults in a new qaboard.yaml.
"""
from typing import Any, Dict, List, Tuple

import yaml

from .site_config import LOCATION_KEYS


Key = Tuple[str, ...]

def shadowed_keys(sample: Dict[str, Any], site: Dict[str, Any], prefix: Key = ()) -> List[Key]:
  """
  Keys in the sample configuration that would override the site defaults.
  Lists are kept: they are usually project-specific, and can extend the site's with "super".
  """
  keys = []
  for key, site_value in site.items():
    if key not in sample or isinstance(sample[key], list):
      continue
    sample_value = sample[key]
    is_location = (*prefix, key) in LOCATION_KEYS
    if isinstance(site_value, dict) and isinstance(sample_value, dict) and not is_location:
      sub_keys = shadowed_keys(sample_value, site_value, (*prefix, key))
      # if the whole section would be overriden, we remove it
      if sample_value and set(sub_keys) == {(*prefix, key, k) for k in sample_value}:
        keys.append((*prefix, key))
      else:
        keys.extend(sub_keys)
    else:
      keys.append((*prefix, key))
  return keys


def last_line(node: yaml.Node) -> int:
  """Last line containing a value in a YAML node - ignoring trailing comments."""
  if isinstance(node, yaml.MappingNode):
    return max(last_line(n) for kv in node.value for n in kv) if node.value else node.end_mark.line
  if isinstance(node, yaml.SequenceNode):
    return max(last_line(n) for n in node.value) if node.value else node.end_mark.line
  # block scalars end at the start of the next line
  return node.end_mark.line - 1 if node.end_mark.column == 0 and node.end_mark.line > node.start_mark.line else node.end_mark.line


def use_site_defaults(sample_config: str, site: Dict[str, Any]) -> str:
  """
  Comment-out the sample configuration's settings that would override the site defaults,
  and show instead the values inherited from the site.
  """
  sample = yaml.load(sample_config, Loader=yaml.SafeLoader) or {}
  root = yaml.compose(sample_config)
  lines = sample_config.splitlines()
  replacements = []
  for key in shadowed_keys(sample, site):
    node, site_value = root, site
    for k in key:
      key_node, node = next((kn, vn) for kn, vn in node.value if kn.value == k)
      site_value = site_value[k]
    indent = " " * key_node.start_mark.column
    site_yaml = yaml.safe_dump({key[-1]: site_value}, default_flow_style=False, sort_keys=False)
    comment = [f"{indent}# Inherited from the site defaults, override if needed:"]
    comment += [f"{indent}# {l}" for l in site_yaml.splitlines()]
    replacements.append((key_node.start_mark.line, last_line(node), comment))
  for start, end, comment in sorted(replacements, reverse=True):
    lines[start:end+1] = comment
  header = [
    "# This configuration is merged on top of the site defaults (QABOARD_SITE_CONFIG, installed by your site package).",
    "# Settings defined here take precedence.",
  ]
  return "\n".join([*header, *lines]) + "\n"
