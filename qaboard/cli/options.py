"""
Options shared by several `qa` commands, and their defaults from qaboard.yaml.

The python names of the parameters matter: they define the environment variables that can
replace each option, e.g. `qa batch --runner` reads QA_BATCH_RUNNER (QA_{COMMAND}_{PARAMETER}).
tests/test_cli_surface.py makes sure they don't change by accident.
"""
import os
from pathlib import Path
from typing import Annotated, Any, List, Optional

import typer

from ..config import config, default_batches_files


def int_or_str(value: Any) -> Any:
  """Integers stay integers (e.g. memory in MB), anything else is kept as-is (e.g. "16G")."""
  try:
    return int(value)
  except (TypeError, ValueError):
    return value


# Inputs and outputs of a single run
InputPath = Annotated[Path, typer.Option(
  '--input', '-i', parser=Path, metavar='PATH',
  help="Input to run on, relative to the database (or an absolute path).",
)]
OptionalInputPath = Annotated[Optional[Path], typer.Option(
  '--input', '-i', parser=Path, metavar='PATH',
  help="Input to run on, relative to the database (or an absolute path).",
)]
OutputPath = Annotated[Optional[Path], typer.Option(
  '--output', '-o', parser=Path, metavar='PATH',
  help="Custom output directory. By default it is derived from the commit, label, configurations and input.",
)]
ForwardedArgs = Annotated[Optional[List[str]], typer.Argument(
  metavar='[FORWARDED_ARGS]...', show_default=False,
  help="Extra arguments, given to your code as `context.forwarded_args`.",
)]

# Batches of inputs
Batches = Annotated[List[str], typer.Option(
  '--batch', '-b', metavar='BATCH',
  help="Batch of inputs+configurations+database, defined in a batches file. Repeat for several batches.",
)]
RequiredBatches = Annotated[List[str], typer.Option(
  '--batch', '-b', metavar='BATCH', show_default=False,
  help="Batch of inputs+configurations+database, defined in a batches file. Repeat for several batches.",
)]
BatchesFiles = Annotated[List[Path], typer.Option(
  '--batches-file', parser=Path, metavar='PATH',
  help="YAML file defining batches. Defaults to `inputs.batches` in qaboard.yaml.",
)]
Strict = Annotated[bool, typer.Option(
  '--strict',
  help="Also fail if a file exists in one run and not the other. By default only files in both runs are compared.",
)]

# The defaults are lists, typer needs them as such
default_batches_files_list: List[Path] = list(default_batches_files)


## Runners ######################################################################################
runners_config = config.get('runners', {})
lsf_config = config['lsf'] if 'lsf' in config else runners_config.get('lsf', {})
local_config = runners_config.get('local', {})
dask_config = runners_config.get('dask', {})

if 'default' in runners_config:
  default_runner = runners_config['default']
else:
  task_runners = [r for r in runners_config if r not in ['default', 'local']]
  default_runner = task_runners[0] if task_runners else 'local'
if 'lsf' in config:
  default_runner = 'lsf'
if default_runner == 'lsf' and os.name == 'nt':
  default_runner = 'local'

default_lsf_queue = lsf_config.get('queue')
default_lsf_max_threads = lsf_config.get('max_threads', 0)
default_lsf_max_memory = int_or_str(lsf_config.get('max_memory', lsf_config.get('memory', 0)))
default_lsf_resources = lsf_config.get('resources', None)
default_lsf_priority = lsf_config.get('priority')
default_lsf_options = lsf_config.get('options')
default_lsf_concurrency = lsf_config.get('concurrency', 0)
default_dask_concurrency = dask_config.get('concurrency')
default_local_concurrency = os.environ.get('QA_BATCH_CONCURRENCY', local_config.get('concurrency'))
default_local_timeout = int(float(os.environ.get('QA_BATCH_TIMEOUT', local_config.get('timeout', 0))))

outputs_config = config.get('outputs', {})
default_action_on_existing = outputs_config.get('action_on_existing', "run")
default_action_on_pending = outputs_config.get('action_on_pending', "wait")
