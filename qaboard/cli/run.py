"""
Commands working on a single run: run, postprocess, sync, wait.
"""
import os
import sys
import json
import time
import traceback
from shlex import quote
from pathlib import Path
from contextlib import nullcontext
from typing import Annotated, Optional

import typer

from .app import app
from .options import InputPath, OutputPath, ForwardedArgs
from ..run import RunContext
from ..utils import entrypoint_module, save_outputs_manifest, total_storage
from ..utils import redirect_std_streams, clean_output_dir
from ..api import print_url, notify_qa_database
from ..config import config, project, commit_id, is_ci

PANEL = "Run your code"


def forward_args(ctx: typer.Context):
  """Arguments after "--" are split before parsing, see split_dashdash."""
  ctx.params["forwarded_args"] = [
    *(ctx.params.get("forwarded_args") or []),
    *ctx.obj.get("forwarded_args", []),
  ]


def click_compat_context(ctx: typer.Context):
  """
  User code written when `qa` used click may call click.secho or click.get_current_context().
  If it imported click, we give it a context like before: same `obj`, and colors in logs.
  """
  if 'click' not in sys.modules:
    return nullcontext()
  import click
  click_ctx = click.Context(click.Command(ctx.info_name), obj=ctx.obj, color=ctx.color)
  click_ctx.params = ctx.params
  return click_ctx


def print_outputs(run_context: RunContext):
  typer.echo(typer.style("Outputs: ", fg='cyan') + typer.style(str(run_context.output_dir), fg='cyan', bold=True), err=True)


@app.command(
  rich_help_panel=PANEL,
  context_settings=dict(
    ignore_unknown_options=True,
    allow_interspersed_args=False,
  ),
)
def run(
  ctx: typer.Context,
  input_path: InputPath,
  output_path: OutputPath = None,
  keep_previous: Annotated[bool, typer.Option('--keep-previous', help="Don't clean previous outputs before the run.")] = False,
  no_postprocess: Annotated[bool, typer.Option('--no-postprocess', help="Don't do the postprocessing.")] = False,
  save_manifests_in_database: Annotated[bool, typer.Option('--save-manifests-in-database', help="Save the input and outputs manifests in the database.")] = False,
  forwarded_args: ForwardedArgs = None,
):
  """
  Run your code on one input, then compute its metrics.

  Calls `run(context)` from your entrypoint, then `postprocess` if defined.
  Outputs are saved in the output directory (printed on stderr) with
  `metrics.json`, `log.txt` and manifests of the input and output files.
  The metrics are printed at the end. Exits with 1 if the run failed.

  Examples:

      qa run --input images/a.jpg
      qa --share --config fast run -i images/a.jpg
      qa run -i images/a.jpg -- --flag-for-your-code
  """
  forward_args(ctx)
  run_context = RunContext.from_click_run_context(ctx, config)

  if run_context.output_dir.exists():
  # Usually we want to remove any files already present in the output directory.
  # It avoids issues with remaining state... This said,
  # In some cases users want to debug long, multi-stepped runs, for which they have their own caching
    if not (keep_previous or 'QABOARD_RUN_KEEP' in os.environ):
      # TODO: check in the database the status of the run?
      # Keeps the log files runners redirected our output to (e.g. log.lsf.txt with LSF job arrays)
      clean_output_dir(run_context.output_dir)
  run_context.output_dir.mkdir(parents=True, exist_ok=True)

  with (run_context.output_dir / 'run.json').open('w') as f:
    json.dump({
      # run_context.database is always made absolute, we keep it relative if given so
      "database": str(ctx.obj["database"]),
      "input_path": str(run_context.rel_input_path),
      "input_type": run_context.type,
      "configurations": run_context.configurations,
      "extra_parameters": run_context.extra_parameters,
      "platform": run_context.platform,
    }, f, sort_keys=True, indent=2, separators=(',', ': '))

  # Without this, we can only log runs from `qa batch`, on linux, via LSF
  # this redirect is not 100% perfect, we don't get stdout from C calls
  # if not 'LSB_JOBID' in os.environ: # When using LSF, we usally already have incremental logs
  with redirect_std_streams(run_context.output_dir / 'log.txt', color=ctx.obj['color']):
    # Help reproduce qa runs with something copy-pastable in the logs
    if is_ci:
      typer.secho(' '.join(['qa', *map(quote, sys.argv[1:])]), fg='cyan', bold=True)
    print_outputs(run_context)
    print_url(ctx)

    if not ctx.obj['offline']:
        qa_run_data = notify_qa_database(**ctx.obj, is_pending=True, is_running=True)
        if qa_run_data:
          run_context.id = qa_run_data["id"]

    start = time.time()
    cwd = os.getcwd()

    entrypoint = entrypoint_module(config)
    try:
      with click_compat_context(ctx):
        runtime_metrics = entrypoint.run(run_context)
    except Exception as e:
      typer.secho(f'[ERROR] Your `run` function raised an exception: {e}', fg='red', bold=True)
      typer.secho(traceback.format_exc(), fg='red')
      runtime_metrics = {'is_failed': True}

    if not runtime_metrics:
      runtime_metrics = {"is_failed": False}

    if not isinstance(runtime_metrics, dict):
      typer.secho(f'[ERROR] Your `run` function did not return a dict, but {runtime_metrics}', fg='red', bold=True)
      runtime_metrics = {'is_failed': True}

    runtime_metrics['compute_time'] = time.time() - start

    # avoid issues if code in run() changes cwd
    if os.getcwd() != cwd:
      os.chdir(cwd)

    with click_compat_context(ctx):
      metrics = postprocess_(runtime_metrics, run_context, skip=no_postprocess or runtime_metrics['is_failed'], save_manifests_in_database=save_manifests_in_database)
    if not metrics:
      metrics = runtime_metrics

    if metrics['is_failed']:
      typer.secho('[ERROR] The run has failed.', fg='red', err=True)
      typer.secho(str(metrics), fg='red', bold=True)
      raise typer.Exit(1)
    else:
      typer.secho(str(metrics), fg='green')


def postprocess_(runtime_metrics, run_context, skip=False, save_manifests_in_database=False):
  """Computes various success metrics and outputs."""
  from ..utils import file_info
  from ..compat import windows_to_linux_path

  try:
    if not skip:
      try:
        entrypoint_postprocess = entrypoint_module(config).postprocess
      except Exception:
        metrics = runtime_metrics
      else:
        metrics = entrypoint_postprocess(runtime_metrics, run_context)
    else:
      metrics = runtime_metrics
  except Exception:
    # TODO: we should provide a default postprocess function, that reads metrics.json and returns {**previous, **runtime_metrics}
    typer.secho('[ERROR] Your `postprocess` function raised an exception:', fg='red', bold=True)
    typer.secho(traceback.format_exc(), fg='red')
    metrics = {**runtime_metrics, 'is_failed': True}

  if 'is_failed' not in metrics:
    typer.secho("[Warning] The result of the `postprocess` function misses a key `is_failed` (bool)", fg='yellow')
    metrics['is_failed'] = False

  if (run_context.output_dir / 'metrics.json').exists():
    with (run_context.output_dir / 'metrics.json').open('r') as f:
      previous_metrics = json.load(f)
      metrics = {
        **previous_metrics,
        **metrics,
      }
  with (run_context.output_dir / 'metrics.json').open('w') as f:
      json.dump(metrics, f, sort_keys=True, indent=2, separators=(',', ': '))

  # To help identify if input files change, we compute and save some metadata.
  manifest_inputs = run_context.obj.get('manifest-inputs', [run_context.input_path])
  input_files = {}
  def manifest_path_str(path):
    return windows_to_linux_path(path).as_posix()
  def update_manifest(path):
    path_str = manifest_path_str(path)
    input_files[path_str] = file_info(path, config=config)
  for manifest_input in manifest_inputs:
    manifest_input = Path(manifest_input)
    if manifest_input.is_dir():
      for idx, path in enumerate(manifest_input.rglob('*')):
        if idx >= 200:
          break
        if not path.is_file():
          continue
        update_manifest(path)
    elif manifest_input.is_file():
      update_manifest(manifest_input)
  try:
    with (run_context.output_dir / 'manifest.inputs.json').open('w') as f:
      json.dump(input_files, f, sort_keys=True, indent=2)
  except Exception as e:
    typer.secho('WARNING: When writing the input manifest:', fg="yellow", bold=True, err=True)
    typer.secho(str(e), fg="yellow", err=True)

  output_data = {}
  try:
    outputs_manifest = save_outputs_manifest(run_context.output_dir, config=config)
    output_data['storage'] = total_storage(outputs_manifest)
  except Exception as e:
    outputs_manifest = {}
    typer.secho('WARNING: When writing the output manifest:', fg="yellow", bold=True, err=True)
    typer.secho(str(e), fg="yellow", err=True)
  if 'params' in metrics:
    output_data['params'] = metrics['params']
    del metrics['params']


  if save_manifests_in_database:
    if run_context.input_path.is_file():
      typer.secho('WARNING: saving the manifests in the database is only implemented for inputs that are *folders*.', fg='yellow', err=True)
    else:
      from ..utils import copy
      copy(run_context.output_dir / 'manifest.inputs.json', run_context.input_path / 'manifest.inputs.json')
      copy(run_context.output_dir / 'manifest.outputs.json', run_context.input_path / 'manifest.outputs.json')

  if not run_context.obj.get('offline') and not run_context.obj.get('dryrun'):
    notify_qa_database(**run_context.obj, metrics=metrics, data=output_data, is_pending=False, is_running=False)


  ###### SIRC-specific ########################################################
  # Track output images in IDB, only if the (internal) idb_client package is installed
  try:
    import idb_client  # noqa: F401
    has_idb = True
  except ImportError:
    has_idb = False
  try:
    if has_idb and input_files:
      from ..idb import update_idb
      update_idb(run_context, input_files, outputs_manifest, manifest_path_str)
  except Exception as e:
    from sentry_sdk import capture_exception
    capture_exception(e)
    print(f"WARNING: idb raised {e}")
    from ..site_config import site_config
    backlog_dir = site_config('QABOARD_IDB_BACKLOG_DIR')
    if backlog_dir:
      import random
      hex_string = ''.join(random.choices('0123456789abcdef', k=8))
      task_path = Path(backlog_dir) / f"{hex_string}.pickle"
      try:
        json.dump(
          {
            "id": run_context.id,
            "input_path": str(run_context.input_path),
            "output_dir": str(run_context.output_dir),
            "batch_label": run_context.obj['batch_label'],
            "project": str(project.name),
            "commit_id": commit_id,
            "input_files": input_files,
            "outputs_manifest": outputs_manifest,
          },
          task_path.open("w")
        )
      except Exception as e_backlog:
        print(f"WARNING: could not save the idb task in the backlog: {e_backlog}")
      ### Then to tackle the backlog...
      # for task in backlog_dir.glob("*.pickle"):
      #   args = pickle.load(task.open())
      #   from qaboard.idb import idb_update
      #   idb_update(*args)
      #   task.unlink() # delete the file

  if os.name == "nt" and not run_context.obj.get('dryrun') and (run_context.obj.get('share') or is_ci):
    from ..compat import fix_linux_permissions
    fix_linux_permissions(run_context.output_dir)

  return metrics



@app.command(
  rich_help_panel=PANEL,
  context_settings=dict(ignore_unknown_options=True),
)
def postprocess(
  ctx: typer.Context,
  input_path: InputPath,
  output_path: OutputPath = None,
  forwarded_args: ForwardedArgs = None,
):
  """
  Run only the post-processing of a run, assuming its results already exist.

  Example:

      qa postprocess --input images/a.jpg
  """
  forward_args(ctx)
  run_context = RunContext.from_click_run_context(ctx, config)
  with redirect_std_streams(run_context.output_dir / 'log.txt', color=ctx.obj['color']):
    print_outputs(run_context)
    print_url(ctx)
    with click_compat_context(ctx):
      metrics = postprocess_({}, run_context)
    if metrics['is_failed']:
      typer.secho('[ERROR] The run has failed.', fg='red', err=True, bold=True)
      typer.secho(str(metrics), fg='red')
    else:
      typer.secho(str(metrics), fg='green')



@app.command(
  rich_help_panel=PANEL,
  context_settings=dict(ignore_unknown_options=True),
)
def sync(
  ctx: typer.Context,
  input_path: InputPath,
  output_path: OutputPath = None,
):
  """
  Send to QA-Board the metrics of a run, from its metrics.json.

  Example:

      qa sync --input images/a.jpg
  """
  run_context = RunContext.from_click_run_context(ctx, config)
  if (run_context.output_dir / 'metrics.json').exists():
    with (run_context.output_dir / 'metrics.json').open('r') as f:
      metrics = json.load(f)
    notify_qa_database(**ctx.obj, metrics=metrics, is_pending=False, is_running=False)
    typer.secho(str(metrics), fg='green')



@app.command(
  rich_help_panel=PANEL,
  context_settings=dict(ignore_unknown_options=True),
)
def wait(
  ctx: typer.Context,
  output_id: Annotated[Optional[str], typer.Option('--output-id', help="QA-Board ID of the run to wait for.")] = None,
):
  """
  Wait until a pending run is finished. Exits with 1 if it failed.

  Used by `qa batch` when the same run is already pending (see --action-on-pending).
  """
  from ..api import get_output
  typer.secho("...waiting for previously started pending run. To run directly, use qa batch --action-on-pending=run")
  while True:
    output = get_output(output_id)
    if not output["is_pending"]:
      break
    time.sleep(5)
    typer.secho("...waiting")
  raise typer.Exit(0 if not output["is_failed"] else 1)
