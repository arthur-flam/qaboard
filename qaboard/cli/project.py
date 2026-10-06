"""
Commands about the project: get, init, save-artifacts.
"""
import os
import json
from pathlib import Path
from typing import Annotated, Any, Dict, List, Optional

import typer

from .app import app
from .options import OptionalInputPath, OutputPath
from ..run import RunContext
from ..api import notify_qa_database
from ..config import config, root_qatools, default_batches_files
from ..config import artifacts_commit, artifacts_commit_root

PANEL = "Project"

# What `qa get` shows without a variable. Any other variable can still be asked for by name.
SUMMARY_VARIABLES = (
  'project', 'project_root', 'subproject', 'root_qatools',
  'commit_id', 'commit_branch', 'is_ci', 'user', 'platform',
  'batch_label', 'input_type', 'database', 'configurations', 'extra_parameters',
  'outputs_commit', 'artifacts_commit', 'batch_dir', 'batch_conf_dir',
  'default_batches_files', 'default_runner',
  # Only with --input
  'input_path', 'rel_input_path', 'output_dir', 'input_metadata',
)

# Old names
ALIASES = {
  "branch_ci_dir": "artifacts_branch_root",
  "commit_ci_dir": "outputs_commit",
}


def get_variables(ctx: typer.Context) -> Dict[str, Any]:
  """
  Everything `qa get` can print. Later sources take precedence:
  the qaboard.yaml configuration, defaults, the context of the command (labels...), and with --input the run.
  """
  import importlib
  from . import options
  # `qaboard.config` is shadowed by the configuration dict in qaboard/__init__.py
  config_module = importlib.import_module('qaboard.config')
  variables: Dict[str, Any] = {}
  for module in (options, config_module):
    variables.update({k: v for k, v in vars(module).items() if not k.startswith('_')})
  variables.update(ctx.obj)
  if ctx.params.get('input_path'):
    from .run import use_click_compat_context
    use_click_compat_context(ctx) # for the metadata() function of user code
    try:
      run_context = RunContext.from_click_run_context(ctx, config)
      variables.update(run_context.asdict())
    except Exception:
      pass
  return variables


def to_json(value: Any) -> str:
  from ..api import serialize_paths
  return json.dumps(serialize_paths(value), default=str, indent=2, sort_keys=True)


@app.command(rich_help_panel=PANEL)
def get(
  ctx: typer.Context,
  variable: Annotated[Optional[str], typer.Argument(
    metavar='[VARIABLE]', show_default=False,
    help="Name of the variable, e.g. commit_id, outputs_commit, batch_dir, output_dir (with --input)...",
  )] = None,
  input_path: OptionalInputPath = None,
  output_path: OutputPath = None,
  as_json: Annotated[bool, typer.Option('--json', help="Print the value as JSON.")] = False,
):
  """
  Print the value of a variable: commit, storage locations, output directory of a run...

  Without VARIABLE, prints as JSON the most useful variables.

  Examples:

      qa get commit_id
      qa --label my-test get batch_dir
      qa get output_dir --input images/a.jpg
      qa get
  """
  variables = get_variables(ctx)
  if variable is None:
    typer.echo(to_json({k: variables[k] for k in SUMMARY_VARIABLES if k in variables}))
    return
  variable = ALIASES.get(variable, variable)
  if variable not in variables:
    typer.secho(f"Could not find {variable}", err=True, fg='red')
    typer.secho("Run `qa get` to see the main variables.", err=True, dim=True)
    raise typer.Exit(1)
  value = variables[variable]
  if as_json:
    typer.echo(to_json(value))
  else:
    print(value)


@app.command(rich_help_panel=PANEL)
def init(
  ctx: typer.Context,
  yes: Annotated[bool, typer.Option(
    '--yes', '-y',
    help="Don't ask questions, use what the wizard detects. Implied without a terminal.",
  )] = False,
  ai: Annotated[Optional[bool], typer.Option(
    '--ai/--no-ai', show_default=False,
    help="Let an AI assistant adapt qa/main.py to your code, through an OpenAI-compatible API "
         "(QABOARD_LLM_BASE_URL, QABOARD_LLM_API_KEY, QABOARD_LLM_MODEL). Needs `pip install qaboard\\[wizard]`. "
         "Default: ask, or no without a terminal.",
  )] = None,
  model: Annotated[Optional[str], typer.Option(
    '--model', show_default=False,
    help="LLM used by the AI assistant. Default: QABOARD_LLM_MODEL, or ask.",
  )] = None,
):
  """
  Set up QA-Board for your project, with a wizard: creates qaboard.yaml and an entrypoint in qa/.

  Run it at the root of your git repository. Nothing is written before you review the changes.
  With `qa --dryrun init`, nothing is written at all.
  """
  from ..wizard import run_wizard
  code = run_wizard(dryrun=ctx.obj['dryrun'], assume_yes=yes, ai=ai, model=model)
  if code:
    raise typer.Exit(code)


@app.command(rich_help_panel=PANEL)
def save_artifacts(
  ctx: typer.Context,
  groups: Annotated[Optional[List[str]], typer.Argument(
    metavar='[GROUPS]...', show_default=False,
    help="Artifacts groups to save, from `artifacts` in qaboard.yaml. Default: all.",
  )] = None,
  files: Annotated[List[str], typer.Option(
    '--file', '-f', metavar='GLOB',
    help="Save those files instead of the artifacts from qaboard.yaml. Supports globs, quote them for your shell.",
  )] = [],
  excluded_groups: Annotated[List[str], typer.Option(
    '--exclude', metavar='GROUP',
    help="Don't save this artifacts group.",
  )] = [],
  # Do we use this? yes in the API, but let's deprecate and remove for other uses...
  artifacts_path: Annotated[str, typer.Option(
    '--out', '-o', metavar='PATH',
    help="Where to save the files given with --file, relative to the commit's artifacts.",
  )] = '',
):
  """
  Save the artifacts of the commit (binaries, configs...), as defined in qaboard.yaml.

  They are needed to run tuning experiments from QA-Board, and can be compared between commits.

  Examples:

      qa save-artifacts
      qa save-artifacts binaries configs
      qa save-artifacts --file 'build/*.so'
  """
  import filecmp
  from ..config import is_in_git_repo, qatools_config_paths
  from ..utils import copy, file_info
  from ..compat import cased_path

  typer.secho(f"Saving artifacts in: {artifacts_commit if not artifacts_path else artifacts_path}", bold=True, underline=True)

  artifacts = {}

  if files:
    artifacts = {f"__{f}": {"glob": f} for f in files}
  else:
    if 'artifacts' not in config:
      config['artifacts'] = {}
    # We support both qaboard.yaml and qatools.yaml for backward compatibility with SIRC's projects
    # Default artifacts
    config['artifacts']['__qaboard.yaml'] = {"glob": ['qaboard.yaml', 'qatools.yaml']}
    config['artifacts']['__qatools'] = {"glob": ['qatools/*', 'qa/*']}
    # Handle sub-projects
    root = root_qatools or Path()
    config['artifacts']['__sub-qaboard.yaml'] = {"glob": [
      str(p.relative_to(root).parent / name) for p in qatools_config_paths for name in ('qaboard.yaml', 'qatools.yaml')
    ]}
    config['artifacts']['__metrics.yaml'] = {"glob": config.get('outputs', {}).get('metrics')}
    config['artifacts']['__batches.yaml'] = {"glob": [str(p) for p in default_batches_files]}
    config['artifacts']['__envrc'] = {"glob": ['.envrc', '*/.envrc']} # we don't use ** since it's so slow...
    if groups:
      if excluded_groups:
        groups = [g for g in groups if g not in excluded_groups]
      artifacts = {g: config['artifacts'][g] for g in groups if g in config['artifacts'].keys()}
    else:
      artifacts = config['artifacts']
  if 'QA_VERBOSE_VERBOSE' in os.environ: print(artifacts)
  if not is_in_git_repo:
      typer.secho(
          "You are not in a git repository, maybe in an artifacts folder. `save_artifacts` is unavailable.",
          fg='yellow', dim=True)
      raise typer.Exit(1)

  for artifact_name, artifact_config in artifacts.items():
    typer.secho(f'Saving artifacts: {artifact_name}', bold=True)
    manifest_path = artifacts_commit / 'manifests' / f'{artifact_name}.json'
    try:
      manifest_path.parent.mkdir(parents=True, exist_ok=True)
    except Exception as e:
      typer.secho(f"ERROR: {e}", fg='red')
      typer.secho("We could not create one the folders required to save the artifacts..", fg='red', dim=True)
      typer.secho("The disk could be full, or just the quota for the current user...", fg='red', dim=True)
      raise typer.Exit(1)
    if manifest_path.exists():
      with manifest_path.open() as f:
        try:
          manifest = json.load(f)
        except Exception:
          manifest = {}
    else:
      manifest = {}

    nb_files = 0
    globs: Any = artifact_config.get('globs', artifact_config.get('glob', []))
    if not isinstance(globs, list):
      globs = [globs]

    for g in globs:
      if not g: continue
      for path in Path('.').glob(g):
        path = cased_path(path)
        if not path.is_file():
          continue
        if artifacts_path:
          destination = artifacts_commit_root / artifacts_path / path
        else:
          destination = artifacts_commit_root / path
        if 'QA_VERBOSE_VERBOSE' in os.environ:
          print(destination)
        if destination.exists() and filecmp.cmp(str(path), str(destination), shallow=True):
          # when working on subprojects, the artifact might be copied already,
          # but manifests are saved per-subproject
          if path.as_posix() not in manifest:
            manifest[path.as_posix()] = file_info(path, config=config)
          continue
        if 'QA_VERBOSE' in os.environ or ctx.obj['dryrun']:
          typer.secho(str(path), dim=True)
        if not ctx.obj['dryrun']:
          copy(path, destination)
          manifest[path.as_posix()] = file_info(path, config=config)
          nb_files += 1

    if not ctx.obj['dryrun']:
      with manifest_path.open('w') as f:
        json.dump(manifest, f)
    if nb_files > 0:
      typer.secho(f"{nb_files} files copied")

  if os.name == "nt" and not ctx.obj['dryrun']:
    from ..compat import fix_linux_permissions
    fix_linux_permissions(artifacts_commit)

  # if the commit was deleted, this notification will mark it as good again
  notify_qa_database(object_type='commit', **ctx.obj)
