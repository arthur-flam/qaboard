"""
`qa batch`: runs on all the inputs of batches, with a runner (local, LSF, dask, celery...).
"""
import os
import re
import sys
import json
import uuid
import datetime
from shlex import quote
from pathlib import Path
from typing import Annotated, Any, Dict, List, Optional

import typer

from .app import app
from .options import default_batches_files_list, Runner, ActionOnExisting, ActionOnPending
from .options import lsf_config, dask_config, default_runner
from .options import default_lsf_queue, default_lsf_max_threads, default_lsf_max_memory, default_lsf_resources
from .options import default_lsf_priority, default_lsf_options, default_lsf_concurrency, default_dask_concurrency
from .options import default_local_concurrency, default_local_timeout
from .options import default_action_on_existing, default_action_on_pending
from ..run import RunContext
from ..runners import Job, JobGroup
from ..conventions import make_batch_conf_dir, serialize_config
from ..utils import load_tuning_search, getenvs
from ..api import url_to_dir, print_url, get_outputs, notify_qa_database, serialize_paths
from ..iterators import iter_inputs, iter_parameters
from ..config import project, subproject, config, commit_id, outputs_commit, is_ci, on_windows
from ..config import get_default_database, default_batch_label, default_platform
from ..config import get_default_configuration, default_input_type

PANEL_BATCHES = "Batches"
PANEL_LIST = "Listing, without running"
PANEL_EXISTING = "Existing results"
PANEL_RUNNER = "Runners"
PANEL_LOCAL = "Local runner"
PANEL_LSF = "LSF runner"
PANEL_DASK = "Dask runner"


@app.command(
  rich_help_panel="Run your code",
  context_settings=dict(
    ignore_unknown_options=True,
    allow_interspersed_args=True,
  ),
)
def batch(
  ctx: typer.Context,
  batch_names: Annotated[Optional[List[str]], typer.Argument(
    metavar='[BATCHES]... [-- FORWARDED_ARGS...]', show_default=False,
    help="Batches to run, then extra arguments for your code (after --, or starting with -).",
  )] = None,
  batches: Annotated[List[str], typer.Option(
    '--batch', '-b', metavar='BATCH', rich_help_panel=PANEL_BATCHES,
    help="Batch to run, like the BATCHES arguments. Repeat for several batches.",
  )] = [],
  batches_files: Annotated[List[Path], typer.Option(
    '--batches-file', parser=Path, metavar='PATH', rich_help_panel=PANEL_BATCHES,
    help="YAML files defining batches. Defaults to `inputs.batches` in qaboard.yaml.",
  )] = default_batches_files_list,
  tuning_search_dict: Annotated[Optional[str], typer.Option(
    '--tuning-search', metavar='JSON', rich_help_panel=PANEL_BATCHES,
    help="""Tuning parameters to explore, as JSON: {"parameter_search": {"gain": [1, 2]}}. Optional: "search_type": "grid" (default) or "sampler" with "search_options": {"n_iter": 10}.""",
  )] = None,
  tuning_search_file: Annotated[Optional[Path], typer.Option(
    '--tuning-search-file', parser=Path, metavar='PATH', rich_help_panel=PANEL_BATCHES,
    help="YAML or JSON file describing the tuning parameters to explore.",
  )] = None,
  prefix_outputs_path: Annotated[Optional[Path], typer.Option(
    '--prefix-outputs-path', parser=Path, metavar='PATH', rich_help_panel=PANEL_BATCHES,
    help="Custom prefix for the outputs, they will be at $prefix/$output_path.",
  )] = None,
  list_contexts: Annotated[bool, typer.Option(
    '--list', rich_help_panel=PANEL_LIST,
    help="Print as JSON the details of each run we would do.",
  )] = False,
  list_output_dirs: Annotated[bool, typer.Option(
    '--list-output-dirs', rich_help_panel=PANEL_LIST,
    help="Print the output directory of each run, one per line.",
  )] = False,
  list_inputs: Annotated[bool, typer.Option(
    '--list-inputs', rich_help_panel=PANEL_LIST,
    help="Print the path of each input, one per line.",
  )] = False,
  action_on_existing: Annotated[ActionOnExisting, typer.Option(
    '--action-on-existing', rich_help_panel=PANEL_EXISTING,
    help="When successful runs already exist: run them again, only postprocess, sync (re-read metrics from the output dir), or skip them. assert-exists runs nothing: it fails if some runs don't exist.",
  )] = default_action_on_existing,
  action_on_pending: Annotated[ActionOnPending, typer.Option(
    '--action-on-pending', rich_help_panel=PANEL_EXISTING,
    help="When the same runs are pending: wait for them (then run those that failed), sync (use their results), skip them, or run them anyway (can cause races).",
  )] = default_action_on_pending,
  runner: Annotated[Runner, typer.Option(
    '--runner', rich_help_panel=PANEL_RUNNER,
    help="Where runs are executed. windows: on Windows hosts, via Jenkins.",
  )] = default_runner,
  no_wait: Annotated[bool, typer.Option(
    '--no-wait', rich_help_panel=PANEL_RUNNER,
    help="Return as soon as the jobs are sent, don't wait for their completion.",
  )] = False,
  local_concurrency: Annotated[Optional[int], typer.Option(
    '--local-concurrency', rich_help_panel=PANEL_LOCAL,
    help="joblib's n_jobs: 0=unlimited, 2=2 at a time, -1=#cpu-1.",
  )] = default_local_concurrency,
  local_timeout: Annotated[int, typer.Option(
    '--local-timeout', rich_help_panel=PANEL_LOCAL,
    help="Timeout for local runs, in seconds.",
  )] = default_local_timeout,
  lsf_max_threads: Annotated[int, typer.Option(
    '--lsf-max-threads', rich_help_panel=PANEL_LSF,
    help="Restrict the number of threads to use. 0=no restriction.",
  )] = default_lsf_max_threads,
  lsf_max_memory: Annotated[int, typer.Option(
    '--lsf-max-memory', metavar='MB', rich_help_panel=PANEL_LSF,
    help="Restrict the memory to use, in MB. 0=no restriction.",
  )] = default_lsf_max_memory,
  lsf_queue: Annotated[Optional[str], typer.Option(
    '--lsf-queue', rich_help_panel=PANEL_LSF,
    help="LSF queue (bsub -q).",
  )] = default_lsf_queue,
  lsf_resources: Annotated[Optional[str], typer.Option(
    '--lsf-resources', rich_help_panel=PANEL_LSF,
    help="LSF resources restrictions (bsub -R).",
  )] = default_lsf_resources,
  lsf_priority: Annotated[Optional[int], typer.Option(
    '--lsf-priority', rich_help_panel=PANEL_LSF,
    help="LSF priority (bsub -sp).",
  )] = default_lsf_priority,
  lsf_options: Annotated[Optional[str], typer.Option(
    '--lsf-options', rich_help_panel=PANEL_LSF,
    help="Other bsub options, as one string like '-W 24:00'. Added after all other flags.",
  )] = default_lsf_options,
  lsf_concurrency: Annotated[int, typer.Option(
    '--lsf-concurrency', rich_help_panel=PANEL_LSF,
    help="Max number of jobs from this batch running at the same time. 0=unlimited.",
  )] = default_lsf_concurrency,
  dask_concurrency: Annotated[Optional[int], typer.Option(
    '--dask-concurrency', rich_help_panel=PANEL_DASK,
    help="Max number of runs from this batch running at the same time on dask workers.",
  )] = default_dask_concurrency,
):
  """
  Run your code on all the inputs of batches, defined in batches files.

  Each run is a `qa run` executed by a runner: locally in parallel, on LSF, dask, celery...
  Waits for all runs to finish, and exits with 1 if any failed.

  Examples:

      qa batch my-batch
      qa --share --label new-denoiser batch my-batch other-batch --runner local
      qa batch my-batch --list
      qa batch my-batch -- --flag-for-your-code
  """
  from ..runners import runners
  from .run import use_click_compat_context
  use_click_compat_context(ctx) # for the iter_inputs() and metadata() functions of user code
  if not batches_files:
    typer.secho('WARNING: Could not find how to identify input tests.', fg='red', err=True, bold=True)
    typer.secho('Consider adding to qaboard.yaml somelike like:\n```\ninputs:\n  batches: batches.yaml\n```', fg='red', err=True)
    typer.secho('Where batches.yaml is formatted like in https://samsung.github.io/qaboard/docs/batches-running-on-multiple-inputs', fg='red', err=True)
    return

  filtered_batch_names = []
  forwarded_args = ctx.obj.get("forwarded_args", [])
  batch_names = batch_names or []
  for i, arg in enumerate(batch_names):
    if arg == "--": # explicit separator → everything after is forwarded args
      forwarded_args = batch_names[i + 1 :]
      break
    # Implicit separator → everything from here is forwarded args
    # We do this for backward compat mostly... not sure batch names should start with "-"!
    elif arg.startswith("-"):
      forwarded_args = batch_names[i:]
      break
    else:
      filtered_batch_names.append(arg)
  batches = list(filtered_batch_names) + list(batches)

  if not batches:
    typer.secho('ERROR: you must provide a batch', fg='red', err=True, bold=True)
    typer.secho('Use either `qa batch BATCH`, `qa batch BATCH_1 BATCH_2` or `qa batch --batch BATCH_2 --batch BATCH_2`', fg='red', err=True)
    raise typer.Exit(1)

  print_url(ctx)
  # if the commit does not exist or there are network errors it will return empty data
  existing_outputs = get_outputs(ctx.obj, ignore_errors=True)
  command_id = os.environ.get('QA_BATCH_COMMAND_ID', str(uuid.uuid4())) # unique IDs for triggered runs makes it easier to wait/cancel them
  if 'QA_BATCH_COMMAND_ID' in os.environ:
    # some projects have run() trigger further "qa batch" commands, notably in "pipelines"
    # so if we keep it defined we'll end up with deadlocks as those batch wait for the current batch to end...
    del os.environ['QA_BATCH_COMMAND_ID']

  os.environ['QA_BATCH']= 'true' # triggered runs will be less verbose than with just `qa run`
  os.environ['QA_BATCHES_FILES'] = json.dumps([str(b) for b in batches_files])
  dryrun = ctx.obj['dryrun'] or list_output_dirs or list_inputs or list_contexts
  should_notify_qa_database = (is_ci or ctx.obj['share']) and not (dryrun or ctx.obj['offline'])
  if should_notify_qa_database:
    command_data = {
      "command_created_at_datetime":  datetime.datetime.utcnow().isoformat(),
      "argv": sys.argv,
      "runner": runner,
      **ctx.obj,
    }
    if runner == 'dask' and dask_config.get('scheduler_address'):
      # so that the backend can stop the batch
      command_data['scheduler_address'] = dask_config['scheduler_address']
    job_url = getenvs(('BUILD_URL', 'CI_JOB_URL', 'CIRCLE_BUILD_URL', 'TRAVIS_BUILD_WEB_URL')) # jenkins, gitlabCI, circleCI, travisCI
    if job_url:
      command_data['job_url'] = job_url
    if not os.environ.get('QA_BATCH_COMMAND_HIDE_LOGS'):
      notify_qa_database(object_type='batch', command={command_id: command_data}, **ctx.obj)


  tuning_search, filetype = load_tuning_search(tuning_search_dict, tuning_search_file)

  # Separate base configuration from CLI overrides
  base_runner_options: Dict[str, Any] = {
    "command_id": command_id,
    "type": default_runner,
  }
  cli_runner_overrides: Dict[str, Any] = {
    "type": runner,  # CLI --runner flag should have highest priority
  }

  def option(key, value, default):
    """Options given on the CLI override the runner options of each batch, defaults don't."""
    if value != default:
      cli_runner_overrides[key] = value
    else:
      base_runner_options[key] = value

  # Each runner should add what it cares about...
  # TODO: Having --runner-X prefixes makes it all a mess, but still the help text is useful
  # TODO: It would be nice to generate the CLI help depending on the runner that's chosen
  if runner == 'lsf':
    option("queue", lsf_queue, default_lsf_queue)
    option("priority", lsf_priority, default_lsf_priority)
    option("max_threads", lsf_max_threads, default_lsf_max_threads)
    option("max_memory", lsf_max_memory, default_lsf_max_memory)
    option("resources", lsf_resources, default_lsf_resources)
    option("options", lsf_options, default_lsf_options)
    option("concurrency", lsf_concurrency, default_lsf_concurrency)
    if 'concurrency_strategy' in lsf_config:
      base_runner_options['concurrency_strategy'] = lsf_config['concurrency_strategy']
    # Job arrays keep some bookkeeping there
    base_runner_options['batch_dir'] = str(ctx.obj['batch_dir'])

    # These are always set for LSF
    cli_runner_overrides.update({
      "project": lsf_config.get('project', str(project) if project else "qaboard"),
      "user": ctx.obj['user'],
    })

  if runner == "local":
    option("concurrency", local_concurrency, default_local_concurrency)
    option("timeout", local_timeout, default_local_timeout)

  if runner == 'dask':
    # scheduler_address, cluster (dask_jobqueue.LSFCluster kwargs)...
    base_runner_options.update(dask_config)
    # By default dask workers are LSF jobs, sent with the same queue/project... as the LSF runner
    base_runner_options['lsf'] = {
      "project": lsf_config.get('project', str(project) if project else "qaboard"),
      **{k: v for k, v in lsf_config.items() if k in ('queue', 'project', 'resources', 'max_memory', 'options')},
    }
    # We save logs there
    base_runner_options['batch_dir'] = str(ctx.obj['batch_dir'])
    if dask_concurrency != default_dask_concurrency:
      cli_runner_overrides["concurrency"] = dask_concurrency

  if runner == 'local' or runner == 'celery':
    cli_runner_overrides["cwd"] = ctx.obj['previous_cwd'] if 'previous_cwd' in ctx.obj else os.getcwd()

  # For backward compatibility, combine for JobGroup
  default_runner_options = {**base_runner_options, **cli_runner_overrides}

  jobs = JobGroup(job_options=default_runner_options)

  total_runs = 0
  existing_runs, missing_runs = 0, []
  inputs_iter = iter_inputs(batches, batches_files, ctx.obj['database'], ctx.obj['configurations'], ctx.obj['platform'], base_runner_options, config, ctx.obj['inputs_settings'], cli_runner_overrides=cli_runner_overrides)
  for run_context in inputs_iter:
    input_configuration_str = serialize_config(run_context.configurations)
    for tuning_params, tuning_str, tuning_hash in iter_parameters(tuning_search, filetype=filetype, extra_parameters=ctx.obj['extra_parameters']):
      if not prefix_outputs_path:
          batch_conf_dir = make_batch_conf_dir(
            outputs_commit,
            ctx.obj["batch_label"],
            run_context.platform,
            run_context.configurations,
            tuning_params,
            ctx.obj['share']
          )
      else:
          # FIXME: not 100% correct if there is tuning.. but who uses this flag anyway?
          #        worse case batch and outputs will be in slightly different folders...
          batch_conf_dir = outputs_commit / prefix_outputs_path
          if tuning_params:
              batch_conf_dir = batch_conf_dir / tuning_hash
      from ..conventions import output_dirs_for_input_part
      run_context.output_dir = batch_conf_dir / output_dirs_for_input_part(run_context.rel_input_path, run_context.database, config)
      if forwarded_args:
        run_forwarded_args = [a for a in forwarded_args if not a in ("--keep-previous", "--no-postprocess", "--save-manifests-in-database")]
        if run_forwarded_args:
          run_context.extra_parameters = {"forwarded_args": run_forwarded_args, **tuning_params}
        else:
          run_context.extra_parameters = tuning_params
      else:
        run_context.extra_parameters = tuning_params

      if list_inputs:
        print(run_context.input_path)
        break

      # In the past we could assume a given run had a unique output dir,
      # so we could identify platform+input+config+tuning tuples describing runs by their output dir
      # But now the directory can depend on the username...
      # In most cases we don't care: worse case re-running will lead to some orphan output dirs on disk and wasted compute
      # But when trying to get results from older `qa batch`, we care...
      # We could remove the feature flag, maybe when it's used a bit more and we check there is no noticeable runtime cost..
      if not 'QA_BATCH_COMPLEX_MATCHING' in os.environ:
        matching_existing_outputs = [o for o in existing_outputs.values() if url_to_dir(o['output_dir_url']) == run_context.output_dir]
        matching_existing_output = matching_existing_outputs[0] if matching_existing_outputs else None
      else:
        from ..api import matching_output
        matching_existing_output = matching_output(run_context, list(existing_outputs.values()))
      if action_on_existing=='assert-exists':
        # The run exists if QA-Board knows it, or if it has results on disk (e.g. --offline)
        if not matching_existing_output and not run_context.ran():
          missing_runs.append(run_context)
          continue
        if matching_existing_output:
          run_context.output_dir = RunContext.from_api_output(matching_existing_output).output_dir

      if list_output_dirs:
        print(run_context.output_dir)
        break

      if action_on_existing=='assert-exists':
        existing_runs += 1
        continue # nothing to run

      is_pending = matching_existing_output['is_pending'] if matching_existing_output else False
      is_failed = matching_existing_output['is_failed'] if matching_existing_output else run_context.is_failed()
      ran_before = True if matching_existing_output else run_context.ran()
      should_run = not is_pending and (action_on_existing=='run' or is_failed or not ran_before)
      if not should_run and action_on_existing=='skip' and not is_pending:
        continue
      if is_pending and action_on_pending == 'skip':
          continue

      if not forwarded_args:
        forwarded_args_cli = None
      else:
        forwarded_args_cli = ' '.join(cli_quote(a) for a in forwarded_args)

      if input_configuration_str == get_default_configuration(ctx.obj['inputs_settings']):
        configuration_cli = None
      else:
        # We can't use --config, or "-c A -c B" until we ensure all clients updated a version supporting it
        configuration_cli = f"--configuration {cli_quote(input_configuration_str)}"

      if not tuning_params:
        tuning_cli = None
      else:
        tuning_cli = f"--tuning {cli_quote(tuning_str)}"

      platform_cli = None
      Runner = runners[run_context.job_options['type']]
      if getattr(Runner, "platform", default_platform) != default_platform:
        run_context.platform = getattr(Runner, "platform")
      if run_context.platform != default_platform:
        platform_cli = f'--platform "{run_context.platform}"'
      # We could serialize properly the run_context/runner_options, and e.g. call "qa --pickled-cli" and use the CLI command below just for logs...
      args = [
          "qa",
          '--share' if ctx.obj["share"] else None,
          '--offline' if ctx.obj['offline'] else None,
          f'--label "{ctx.obj["raw_batch_label"]}"' if ctx.obj["raw_batch_label"] != default_batch_label else None,
          platform_cli,
          f'--type "{run_context.type}"' if run_context.type != default_input_type else None,
          f'--database "{run_context.database.as_posix()}"' if run_context.database != get_default_database(ctx.obj['inputs_settings']) else None,
          configuration_cli,
          tuning_cli,
          'run' if should_run else action_on_existing,
          f'--input "{run_context.rel_input_path}"',
          f'--output "{run_context.output_dir}"' if prefix_outputs_path else None,
          # we can't use "--" here directly since we want to allow "qa batch mybatch --some-flag-for-qa-run-like --save-manifests-in-database"
          # we would need to be a little bit accurate in how we handle this...
          forwarded_args_cli if forwarded_args_cli else None,
      ]
      command = ' '.join([arg for arg in args if arg is not None])
      if "QA_BATCH_QUIET" not in os.environ:
        typer.secho(command, fg='cyan', err=True)
        typer.secho(f"   {run_context.output_dir}", fg='blue', err=True)
      if 'QA_TESTING' in os.environ:
        # we want to make sure we test the current code
        command = re.sub('^qa', 'python -m qaboard', command)
      if str(subproject) != '.':
        command = f"cd {subproject} && {command}"

      run_context.command = command
      run_context.job_options['command_id'] = command_id
      job = Job(run_context)

      if should_notify_qa_database and not is_pending:
        total_runs += 1
        if total_runs > 1_000 and not os.environ.get("QA_BATCH_ALLOW_MANY_RUNS"):
          typer.secho("ERROR: Sorry you are sending too many runs at once (>1000). Consider using --offline or get an approval (QA_BATCH_ALLOW_MANY_RUNS=1).", fg='red', err=True)
          raise typer.Exit(1)
        # TODO: accumulate and send all at once to avoid 100s of requests?
        db_output = notify_qa_database(**{
          **ctx.obj,
          **run_context.obj, # for now we don't want to worry about backward compatibility, and input_path being abs vs relative...
          "is_pending": True,
          "data": {
            "job_options": run_context.job_options,
          }
        })
        if db_output: # Note: the ID is already in the matching job above
          job.id = db_output["id"]
      if is_pending:
        assert matching_existing_output
        wait_command = f"qa wait --output-id {matching_existing_output['id']}"
        if action_on_pending=="sync":
          job.id = matching_existing_output['id']
          job.run_context.command = wait_command
        elif action_on_pending=="wait":
          job.run_context.command = f"{wait_command} || {job.run_context.command}"
          if action_on_existing=="skip":
            job.run_context.command = wait_command
        else:
          assert action_on_pending=="run"
      jobs.append(job)

  if action_on_existing=='assert-exists':
    if missing_runs:
      typer.secho(f"ERROR: {len(missing_runs)} runs can't be found, neither in QA-Board nor in their output directory:", err=True, fg="red")
      for run_context in missing_runs:
        typer.secho(f"       {run_context.rel_input_path} {serialize_config(run_context.configurations)} {run_context.extra_parameters or ''}", err=True, fg="red")
      raise typer.Exit(1)
    if not dryrun:
      typer.secho(f"All the runs exist ({existing_runs}).", err=True, fg="green")
      return

  if list_contexts:
    print(json.dumps([serialize_paths(j.run_context.asdict()) for j in jobs], indent=2))
    return

  if not dryrun:
    is_failed = jobs.start(
      blocking=not no_wait,
      qa_context=ctx.obj,
    )

    from ..gitlab import gitlab_token, update_gitlab_status
    if gitlab_token and jobs and is_ci and 'QABOARD_TUNING' not in os.environ:
      name = f"QA {subproject.name}" if subproject else 'QA'
      from ..api import qaboard_url
      target_url = f"{qaboard_url}/{config['project']['name']}/commit/{commit_id}"
      label = ctx.obj["batch_label"]
      if label != "default":
        name += f" | {label}"
        target_url += f"?batch={label}"
      update_gitlab_status(
        state='failed' if is_failed else 'success',
        name=name,
        target_url=target_url,
        description=f"{len(jobs)} results",
      )

    if is_failed and not no_wait:
      del os.environ['QA_BATCH'] # restore verbosity
      if should_notify_qa_database:
        print_url(ctx, status="failure")
      raise typer.Exit(1)
    else:
      if should_notify_qa_database:
        print_url(ctx)


def cli_quote(arg: str) -> str:
  """Quotes an argument for the shell that will run the command: sh on linux, cmd on windows."""
  if not on_windows:
    return quote(arg)
  from ..compat import escaped_for_cli
  return escaped_for_cli(arg)
