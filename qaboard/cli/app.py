"""
The `qa` application and its global options, given before the command: `qa [GLOBAL OPTIONS] COMMAND [OPTIONS]`.
The commands are defined in the other modules of this package.
"""
import os
import sys
import json
from pathlib import Path
from typing import Annotated, Any, Dict, List, Optional

import typer
from typer.core import TyperGroup

from ..conventions import make_batch_dir, make_batch_conf_dir
from ..conventions import deserialize_config, get_settings
from ..config import config_has_error, ignore_config_errors
from ..config import project, project_root, subproject, config
from ..config import get_default_database, default_batch_label, default_platform
from ..config import get_default_configuration, default_input_type
from ..config import outputs_commit, artifacts_commit, root_qatools
from ..config import user, is_ci


# Help panels, to find options and commands faster
PANEL_INPUTS = "Inputs and configurations"
PANEL_TUNING = "Tuning parameters"
PANEL_BEHAVIOR = "Behavior"

class QaGroup(TyperGroup):
  # Order in `qa --help`, by help panel
  commands_order = (
    'run', 'batch', 'postprocess', 'sync', 'wait',
    'check-bit-accuracy', 'check-bit-accuracy-manifest',
    'optimize',
    'get', 'init', 'save-artifacts',
  )

  def list_commands(self, ctx):
    names = super().list_commands(ctx)
    order = {name: idx for idx, name in enumerate(self.commands_order)}
    return sorted(names, key=lambda name: order.get(name, len(order)))

  def get_params(self, ctx):
    params = super().get_params(ctx)
    for param in params:
      # Options of `qa` itself can't come from environment variables. QA_VERSION may well be set for something else...
      if param.name in ('version', 'install_completion', 'show_completion'):
        param.allow_from_autoenv = False  # type: ignore
        param.show_envvar = False  # type: ignore
    return params


app = typer.Typer(
  name="qa",
  help="""
  Run your code on inputs, compute metrics, compare and share results with [bold]QA-Board[/bold].

  Global options go [bold]before[/bold] the command: `qa --share --label my-test batch my-batch`.
  Every option can also be set with an environment variable, shown in each command's --help.
  """,
  epilog="Docs: https://samsung.github.io/qaboard/docs/using-the-qa-cli",
  no_args_is_help=True,
  cls=QaGroup,
  rich_markup_mode="rich",
  # Locals can be huge (configs, metrics...), and contain secrets
  pretty_exceptions_show_locals=False,
)


def version_callback(value: bool):
  if value:
    from .. import __version__
    typer.echo(__version__)
    raise typer.Exit()


@app.callback()
def qa(
  ctx: typer.Context,
  platform: Annotated[str, typer.Option(
    '--platform', rich_help_panel=PANEL_INPUTS,
    help="Platform the code runs for, given to your code as `context.platform`.",
  )] = default_platform,
  configurations: Annotated[List[str], typer.Option(
    '--configuration', '--config', '-c', metavar='CONFIG', rich_help_panel=PANEL_INPUTS,
    help="Configuration given to your code (string or JSON), repeat for several. Defaults to the input type's `configurations`.",
  )] = [],
  label: Annotated[str, typer.Option(
    '--label', '-l', rich_help_panel=PANEL_INPUTS,
    help="Name for this batch of results, to group and compare experiments.",
  )] = default_batch_label,
  input_type: Annotated[str, typer.Option(
    '--type', rich_help_panel=PANEL_INPUTS,
    help="Input type, from `inputs.types` in qaboard.yaml.",
  )] = default_input_type,
  database: Annotated[Optional[Path], typer.Option(
    '--database', parser=Path, metavar='PATH', rich_help_panel=PANEL_INPUTS,
    help="Where inputs are stored. Defaults to the input type's `database`.",
  )] = None,
  tuning: Annotated[List[str], typer.Option(
    '--tuning', metavar='JSON', rich_help_panel=PANEL_TUNING,
    help="Extra parameters for tuning, as JSON. Given to your code in `context.params`.",
  )] = [],
  tuning_filepath: Annotated[List[Path], typer.Option(
    '--tuning-filepath', parser=Path, metavar='PATH', rich_help_panel=PANEL_TUNING,
    help="File with extra parameters for tuning, as JSON.",
  )] = [],
  dryrun: Annotated[bool, typer.Option(
    '--dryrun', rich_help_panel=PANEL_BEHAVIOR,
    help="Only show what would be done.",
  )] = False,
  share: Annotated[bool, typer.Option(
    '--share', rich_help_panel=PANEL_BEHAVIOR,
    help="Show the results in QA-Board, not just save them locally. Implied in CI.",
  )] = False,
  offline: Annotated[bool, typer.Option(
    '--offline', rich_help_panel=PANEL_BEHAVIOR,
    help="Don't tell QA-Board about runs and their status.",
  )] = False,
  version: Annotated[Optional[bool], typer.Option(
    '--version', callback=version_callback, is_eager=True,
    help="Show the version and exit.",
  )] = None,
):
  """Entrypoint to running your algo, launching batches..."""
  # We want all paths to be relative to top-most qaboard.yaml
  # it should be located at the root of the git repository
  if config_has_error and not ignore_config_errors:
    typer.secho('Please fix the error(s) above in qaboard.yaml', fg='red', err=True, bold=True)
    raise typer.Exit(1)

  # Commands get `ctx.obj`, we use it as a scratchpad. It's also what is sent to QA-Board's API.
  if not ctx.obj:
    ctx.obj = {}

  will_show_help = '-h' in sys.argv or '--help' in sys.argv
  noop_command = 'init' in sys.argv
  if root_qatools and root_qatools != Path().resolve() and not will_show_help and not noop_command:
    ctx.obj['previous_cwd'] = os.getcwd()
    typer.echo(typer.style("Working directory changed to: ", fg='blue') + typer.style(str(root_qatools), fg='blue', bold=True), err=True)
    os.chdir(root_qatools)

  # We want open permissions on outputs and artifacts
  # it makes collaboration among multiple users / automated tools so much easier...
  os.umask(0)

  ctx.obj['project'] = project
  ctx.obj['project_root'] = project_root
  ctx.obj['subproject'] = subproject
  ctx.obj['user'] = user
  ctx.obj['dryrun'] = dryrun
  ctx.obj['share'] = share
  ctx.obj['offline'] = offline

  ctx.obj['outputs_commit'] = outputs_commit
  ctx.obj['artifacts_commit'] = artifacts_commit
  ctx.obj['ci_commit_dir'] = artifacts_commit # backward compat for some HW_ALG tests...
  ctx.obj['commit_ci_dir'] = artifacts_commit # backward compat for some HW_ALG tests...
  # Note: to support multiple databases per project,
  # either use / as database, or somehow we need to hash the db in the output path.
  ctx.obj['raw_batch_label'] = label
  ctx.obj['batch_label'] = label if (not share or is_ci) else f"@{user}| {label}"
  ctx.obj['platform'] = platform

  ctx.obj['input_type'] = input_type
  ctx.obj['inputs_settings'] = get_settings(input_type, config)
  ctx.obj['database'] = database if database else get_default_database(ctx.obj['inputs_settings'])
  # configuration singular is for backward compatibility to a time where there was a single str config
  ctx.obj['configuration'] = ':'.join(configurations) if configurations else get_default_configuration(ctx.obj['inputs_settings'])
  # we should refactor the str configuration away completely, and do a much simpler parsing, like
  #   deserialize_config = lambda configurations: return [maybe_json_loads(c) for c in configurations]
  ctx.obj['configurations'] = deserialize_config(ctx.obj['configuration'])
  ctx.obj['extra_parameters'] = parse_tuning(tuning, tuning_filepath)

  # batch runs will override this since batches may have different configurations
  ctx.obj['batch_conf_dir'] = make_batch_conf_dir(outputs_commit, ctx.obj['batch_label'], platform, ctx.obj['configurations'], ctx.obj['extra_parameters'], share)
  ctx.obj['batch_dir'] = make_batch_dir(outputs_commit, ctx.obj['batch_label'], platform, ctx.obj['configurations'], ctx.obj['extra_parameters'], share)

  os.environ.update({
    "QA_LABEL": ctx.obj['raw_batch_label'],
  })

  # For convenience, we allow users to change environment variables using {ENV: {VAR: value}}
  # in configurations or tuning parameters
  environment_variables = {}
  for c in ctx.obj['configurations']:
    if not isinstance(c, dict): continue
    if 'ENV' in c: environment_variables.update(c['ENV'])
  if 'ENV' in ctx.obj['extra_parameters']:
    environment_variables.update(ctx.obj['extra_parameters']['ENV'])
  os.environ.update(environment_variables)

  # We manage colors ourselves since we redirect std streams to both the original stream and a log file.
  # Colors in log files are shown in QA-Board. https://no-color.org disables them.
  no_color = bool(os.environ.get('NO_COLOR'))
  ctx.color = not no_color
  ctx.obj['color'] = (is_ci or share) and not no_color


def parse_tuning(tuning: List[str], tuning_filepath: List[Path]) -> dict:
  """
  Merges the --tuning parameters. Strings and lists are not parameters but configurations:
  they are collected in "_configs" and appended to `context.configs`, not exposed in `context.params`.
  Otherwise there is no way to do tuning when we want to tune "str" values.
    --tuning '"hello"' --tuning '{"key": "value"}' --tuning '["world"]'
    => {'_configs': ['hello', 'world'], 'key': 'value'}
  If the user would want [base,tuning] vs [base], he can try the tuning search
    _config: [["base", "tuning"], "base"]
  """
  from ..utils import merge
  extra_parameters: dict = {}
  for params_str in [*(tp.read_text() for tp in tuning_filepath), *tuning]:
    params = json.loads(params_str)
    if isinstance(params, str):
      extra_parameters.setdefault("_configs", []).append(params)
    elif isinstance(params, list):
      extra_parameters.setdefault("_configs", []).extend(params)
    else:
      merge(params, extra_parameters)
  return extra_parameters



# We want to allow both
# 1. Late "--list" args like "qa batch my-batch --list", which requires allow_interspersed_args=True
# 2. "--" to specify args to be forwarded in "qa batch", which would require allow_interspersed_args=False
# So the only good way to solve it is to handle -- before the CLI parser even runs...
def split_dashdash() -> List[str]:
  if '--' in sys.argv:
      idx = sys.argv.index('--')
      pre = sys.argv[:idx]
      post = sys.argv[idx + 1:]
      sys.argv = pre  # The parser will see only "before"
      return post
  return []


def init_sentry() -> bool:
  """In CI, report errors to the Sentry project set by the site (QABOARD_SENTRY_DSN), if any. Returns whether it's enabled."""
  from ..site_config import site_config, as_requests_verify
  dsn = site_config("QABOARD_SENTRY_DSN")
  if not os.environ.get("CI") or not dsn:
    return False
  import sentry_sdk
  options: Dict[str, Any] = {}
  if as_requests_verify(site_config("QABOARD_SENTRY_VERIFY")) is False:
    import urllib3
    urllib3.disable_warnings()
    class InsecureHttpTransport(sentry_sdk.transport.HttpTransport):
      def _get_pool_options(self, *args, **kwargs):
        options = super()._get_pool_options(*args, **kwargs)
        options["cert_reqs"] = "CERT_NONE"
        return options
    options["transport"] = InsecureHttpTransport
  sentry_sdk.init(dsn=dsn, traces_sample_rate=1.0, **options)
  return True


def main():
  sentry_enabled = init_sentry()
  from ..compat import ensure_cli_backward_compatibility
  ensure_cli_backward_compatibility()
  forwarded_args = split_dashdash()
  try:
    app(
      obj={"forwarded_args": forwarded_args},
      # Every option can be given as an environment variable: QA_LABEL, QA_BATCH_RUNNER...
      auto_envvar_prefix='QA',
      prog_name='qa',
    )
  except Exception as e:
    # typer replaces sys.excepthook to print tracebacks, without calling the one Sentry installed
    if sentry_enabled:
      import sentry_sdk
      sentry_sdk.capture_exception(e)
      sentry_sdk.flush()
    raise
