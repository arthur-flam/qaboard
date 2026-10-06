"""
Commands comparing or optimizing results: check-bit-accuracy, check-bit-accuracy-manifest, optimize.
Their logic is in qaboard/bit_accuracy.py and qaboard/optimize.py.
"""
from pathlib import Path
from typing import Annotated, List, Optional

import typer

from .app import app
from .options import Batches, RequiredBatches, BatchesFiles, Strict, ForwardedArgs, default_batches_files_list
from ..config import config


@app.command(rich_help_panel="Compare results")
def check_bit_accuracy(
  ctx: typer.Context,
  reference: Annotated[str, typer.Option(
    '--reference',
    help="Branch, tag or commit used as reference. Defaults to `project.reference_branch` in qaboard.yaml.",
  )] = config.get('project', {}).get('reference_branch', 'master'),
  batches: Batches = [],
  batches_files: BatchesFiles = default_batches_files_list,
  strict: Strict = False,
  reference_label: Annotated[Optional[str], typer.Option('--reference-label', help="Compare against another label on the same batch.")] = None,
  reference_platform: Annotated[Optional[str], typer.Option('--reference-platform', help="Compare against a different platform.")] = None,
):
  """
  Check that the outputs are the same as a reference commit's. Exits with 1 if not.

  Files are compared using the hashes in each run's manifest.outputs.json.
  In CI on the reference branch, the commit is compared with its parents.

  Examples:

      qa check-bit-accuracy --batch my-batch
      qa check-bit-accuracy --reference develop --batch my-batch --strict
  """
  from ..bit_accuracy import check_bit_accuracy as check_bit_accuracy_
  check_bit_accuracy_(ctx, reference, batches, batches_files, strict, reference_label, reference_platform)


@app.command(rich_help_panel="Compare results")
def check_bit_accuracy_manifest(
  ctx: typer.Context,
  batches: RequiredBatches,
  batches_files: BatchesFiles = default_batches_files_list,
  strict: Strict = False,
):
  """
  Check that the outputs are the same as the manifests saved next to the inputs. Exits with 1 if not.

  Save the manifests with `qa batch --save-manifests-in-database`.

  Example:

      qa check-bit-accuracy-manifest --batch my-batch
  """
  from ..bit_accuracy import check_bit_accuracy_manifest as check_bit_accuracy_manifest_
  check_bit_accuracy_manifest_(ctx, batches, batches_files, strict)


@app.command(
  rich_help_panel="Tuning",
  context_settings=dict(ignore_unknown_options=True),
)
def optimize(
  ctx: typer.Context,
  batches: RequiredBatches,
  config_file: Annotated[Path, typer.Option('--config-file', parser=Path, metavar='PATH', help="YAML file with the search space, objective and budget.")],
  batches_files: BatchesFiles = default_batches_files_list,
  checkpoint: Annotated[Optional[Path], typer.Option('--checkpoint', parser=Path, metavar='PATH', help="Save/load the optimizer state there, to restart interrupted optimizations.")] = None,
  parallel_param_sampling: Annotated[Optional[int], typer.Option('--parallel-param-sampling', help="Number of parameters sampled and evaluated in parallel.")] = None,
  forwarded_args: Annotated[Optional[List[str]], typer.Argument(
    metavar='[QA_BATCH_ARGS]...', show_default=False,
    help="Extra arguments for each `qa batch`.",
  )] = None,
):
  """
  Optimize parameters with bayesian optimization: each evaluation is a `qa batch`.

  Example:

      qa --label my-optim optimize --batch my-batch --config-file optim.yaml
  """
  from ..optimize import optimize as optimize_
  optimize_(ctx, batches, batches_files, config_file, checkpoint, parallel_param_sampling, tuple(forwarded_args or ()))
