"""
`qa wizard` (or `qa init`): sets up a project with QA-Board, and helps improve the integration when run again.

Setup:
1. Project: detects the git remote, default branch, languages, build and CI; asks where test inputs live.
2. Server: finds and checks the QA-Board server, optionally logs in to get an API token.
3. AI assistant (optional, `pip install qaboard[wizard]`): an agent adapts qa/main.py to the project's code,
   through any OpenAI-compatible API: OpenAI, a company gateway set by the site package, ollama, vLLM...
4. Review: one approval for every change, shown before anything is written, and written atomically.

Run again on a project that uses QA-Board, it checks its health (entrypoint, inputs, batches, metrics, visualizations,
server, token) and offers to chat with the AI assistant, which edits qa/ and qaboard.yaml until the next review.

Modules:
- changes.py: staged changes, path safety, atomic writes - the only way the wizard writes to the project
- detect.py: what we find out about the project without asking
- settings.py: QA-Board server and LLM settings, saved in ~/.qaboard/secrets.yaml
- agent.py: the AI agent and its sandboxed tools
- ui.py: terminal UI
"""
import copy
import os
import shlex
import subprocess
import sys
from pathlib import Path
from typing import Any, Dict, List, Optional

import typer
import yaml
from rich.console import Group
from rich.markup import escape
from rich.table import Table
from rich.text import Text

from ..config import find_configs
from ..site_config import site_config, site_qaboard_config, site_qaboard_config_path
from .changes import ChangeSet, UnsafePath, diff_text
from .detect import ProjectFacts, detect, git, guess_inputs
from .settings import LLM, Server, chat_models, save_user_settings, shadowing_settings, suggest_model, user_settings_path
from .ui import GUIDE_GREETINGS, UI, is_interactive


SAMPLE_PROJECT = Path(__file__).resolve().parent.parent / 'sample_project'
TEMPLATE_FILES = ('qa/main.py', 'qa/batches.yaml', 'qa/metrics.yaml')
DOCS = 'https://samsung.github.io/qaboard/docs'
INSTALL_AI = "pip install 'qaboard[wizard]'"
RUN_AI = "uvx --from 'qaboard[wizard]' qa init"
DEFAULT_STORAGE = '/mnt/qaboard'


class Cancelled(Exception):
  pass


def run_wizard(dryrun: bool = False, assume_yes: bool = False, ai: Optional[bool] = None, model: Optional[str] = None, root: Optional[Path] = None) -> int:
  ui = UI(interactive=is_interactive(assume_yes))
  # `qa` opens permissions for outputs shared by teams, but these are source files in a repository
  os.umask(0o022)
  if not ui.interactive and not assume_yes:
    ui.console.print("[dim]No terminal: answering with the defaults, like with --yes.[/dim]")
  # Without a terminal, code is only sent to an LLM with --ai
  if ai is None and not ui.interactive:
    ai = False
  if ai and not ai_preflight(ui, model):
    return 1
  try:
    return Wizard(ui, dryrun, ai, model, root or Path.cwd()).run()
  except (KeyboardInterrupt, EOFError, Cancelled):
    ui.console.print("\n[bold]👋 Cancelled.[/bold] Nothing was written to your project.")
    return 130


def ai_available() -> bool:
  try:
    import pydantic_ai  # noqa: F401
  except ImportError:
    return False
  return True


def ai_preflight(ui: UI, model: Optional[str]) -> bool:
  """With --ai, fail before asking anything if the assistant can't work."""
  if not ai_available():
    ui.fail(f"The AI assistant needs a few more packages: [bold]{escape(INSTALL_AI)}[/bold]")
    return False
  llm = LLM.from_settings(model)
  if not ui.interactive and not llm.api_key and not llm.is_local:
    ui.fail(f"No API key for {escape(llm.host)}: set QABOARD_LLM_API_KEY" + (" or OPENAI_API_KEY" if llm.is_openai else '') + ", or run `qa init` in a terminal.")
    return False
  return True


class Wizard:
  def __init__(self, ui: UI, dryrun: bool, ai: Optional[bool], model: Optional[str], root: Path):
    self.ui = ui
    self.dryrun = dryrun
    self.ai = ai
    self.model = model
    self.root = root.resolve()
    self.answers: Dict[str, Any] = {}
    self.to_save: Dict[str, str] = {}
    self.server: Optional[Server] = None
    self.server_online = False
    self.database: Optional[Path] = None     # where inputs are, to check example inputs exist
    self.entrypoint = 'qa/main.py'
    self.chat: Optional[Chat] = None         # the conversation with the AI assistant
    self.wired = False                       # whether the AI changed the entrypoint
    self.guiding = False                     # the project already used QA-Board
    self.try_input: Optional[str] = None     # an input the AI suggests to try
    self.todo: List[str] = []                # what the AI says the user should check

  def run(self) -> int:
    ui = self.ui
    dry = "  [dry run]" if self.dryrun else ""
    configs = find_configs(self.root)
    if configs:
      ui.banner("qa wizard · your project uses QA-Board: let's check it, and make it better" + dry, greetings=GUIDE_GREETINGS)
      return self.guide(configs)
    ui.banner("qa wizard · set up QA-Board for this project" + dry)
    ui.steps = ['Project', 'Server', *([] if self.ai is False else ['AI assistant']), 'Review']
    facts = self.step_project()
    self.step_server()
    template = self.template_changes(facts)
    changes = copy.deepcopy(template)
    if self.ai is not False:
      changes = self.step_ai(facts, changes)
    return self.step_review(facts, changes, template)

  # Guide: the project already uses QA-Board ---------------------------------
  def guide(self, configs: List) -> int:
    ui = self.ui
    self.guiding = True
    config_path = configs[-1][1]
    self.root = config_path.parent.resolve()
    config: Dict[str, Any] = merged(site_qaboard_config(), *[c for c, _ in configs])
    project = section(config, 'project')
    self.answers['name'] = project.get('name') or self.root.name
    self.entrypoint = str(project.get('entrypoint') or 'qa/main.py')
    with ui.spinner("Checking your project…"):
      facts = detect(self.root)
      checks = self.health(config, config_path)
    table = Table.grid(padding=(0, 1))
    table.add_column(no_wrap=True)
    table.add_column(style='dim', no_wrap=True)
    table.add_column()
    for icon, label, detail in checks:
      table.add_row(icon, label, detail)
    ui.panel(table, title=f"🩺 {escape(self.answers['name'])}")
    if not ui.interactive:
      ui.hint("Run `qa wizard` in a terminal to fix or improve things with its help.")
      return 0

    changes = ChangeSet(self.root, extra_writable=('.gitignore',))
    while True:
      pending = len(changes.pending())
      options = []
      if ai_available():
        options.append(('c', "Chat with the AI assistant: wire your code, metrics, visualizations, batches, CI..."))
      options += [('s', "Check the QA-Board server and your API token"), ('a', "Set up the AI assistant: API, key, model")]
      if pending:
        options.append(('r', f"Review and write the {pending} changed file{'s' if pending > 1 else ''}"))
      options.append(('q', "Quit"))
      if not ai_available():
        self.hint_install_ai()
      default = 'r' if pending else ('c' if ai_available() else 'q')
      action = ui.choose("What would you like to do?", options, default=default)
      if action == 'c':
        self.guide_chat(facts, changes)
      elif action == 's':
        self.step_server(storage=False)
      elif action == 'a':
        if self.configure_llm(LLM.from_settings(self.model)):
          self.save_settings("the AI settings")
      elif action == 'r':
        return self.step_review(facts, changes)
      else:
        if pending and not ui.confirm(f"Quit without writing the {pending} changed file{'s' if pending > 1 else ''}?", default=False):
          continue
        return 0

  def guide_chat(self, facts: ProjectFacts, changes: ChangeSet):
    ui = self.ui
    if self.chat:
      self.chat.loop()
      return
    if not self.start_chat(facts, changes) or not self.chat:
      return
    ui.hint("For instance: \"make qa/main.py run my CLI\", \"add a PSNR metric\", \"show output.png in the web app\", "
            "\"how do I run this in GitLab CI?\"")
    message = ui.chat_input()
    if not message:
      return
    from .agent import project_brief
    if self.chat.say(project_brief(facts, self.answers, self.entrypoint, setup=False) + f"\n\nThe user asks: {message}"):
      self.save_settings("the AI settings")
    self.chat.loop()

  def health(self, config: Dict[str, Any], config_path: Path) -> List[tuple]:
    """What's set up, and what could be better: (icon, label, detail)."""
    ok, todo, bad = '[green]✔[/green]', '[yellow]▲[/yellow]', '[red]✘[/red]'
    checks = [(ok, 'config', escape(short_path(config_path)))]

    entrypoint = self.root / self.entrypoint
    try:
      code = entrypoint.read_text(errors='replace')
    except OSError:
      code = None
    if code is None:
      checks.append((bad, 'entrypoint', f"{escape(self.entrypoint)} is missing"))
    elif 'def run(' not in code:
      checks.append((bad, 'entrypoint', f"{escape(self.entrypoint)} has no run(context) function"))
    elif 'to run *your* code using the context' in code:
      checks.append((todo, 'entrypoint', f"{escape(self.entrypoint)} is still the template: it doesn't call your code yet"))
    else:
      checks.append((ok, 'entrypoint', escape(self.entrypoint)))

    inputs = section(config, 'inputs')
    database = None
    if inputs.get('database'):
      try:
        from ..conventions import location_from_spec
        database = location_from_spec(inputs['database'], {'project': self.answers['name'], 'subproject': ''})
        database = database if database.is_absolute() else self.root / database
      except Exception:
        database = None
    self.database = database
    globs = inputs.get('globs', inputs.get('glob'))
    if database and database.is_dir():
      checks.append((ok, 'inputs', escape(str(database)) + (f" [dim]({escape(str(globs))})[/dim]" if globs else '')))
    else:
      checks.append((todo, 'inputs', f"{escape(str(database)) if database else 'no inputs.database'}: not a folder we can read here"))

    batch_files = inputs.get('batches') or []
    batch_files = [batch_files] if isinstance(batch_files, str) else batch_files
    names: List[str] = []
    for spec in batch_files:
      if not isinstance(spec, str) or spec == 'super':
        continue
      path = Path(spec) if Path(spec).is_absolute() else self.root / spec
      try:
        names += [k for k in (yaml.safe_load(path.read_text()) or {}) if not str(k).startswith('.')]
      except Exception:
        pass
    checks.append((ok, 'batches', escape(', '.join(names[:6]) + (', ...' if len(names) > 6 else ''))) if names else (todo, 'batches', "no batches yet: add some to run `qa batch NAME`"))

    outputs = section(config, 'outputs')
    metrics: Dict[str, Any] = {}
    metrics_spec = outputs.get('metrics')
    if isinstance(metrics_spec, str):
      try:
        metrics = (yaml.safe_load((self.root / metrics_spec).read_text()) or {}).get('available_metrics') or {}
      except Exception:
        metrics = {}
    own = [m for m in metrics if m != 'is_failed']
    checks.append((ok, 'metrics', escape(', '.join(own[:6]) + (', ...' if len(own) > 6 else ''))) if own else (todo, 'metrics', "only is_failed: describe the metrics your code computes"))
    visualizations = outputs.get('visualizations') or []
    checks.append((ok, 'outputs', f"{len(visualizations)} visualization{'s' if len(visualizations) > 1 else ''}") if visualizations else (todo, 'outputs', "no visualizations: show your output files in the web app"))

    server = Server.from_settings()
    online, message, _ = (False, '', {}) if server.is_default else server.probe()
    self.server, self.server_online = server, online
    if server.is_default:
      checks.append((todo, 'server', "not set up: choose s below, or set QABOARD_URL"))
    elif not online:
      checks.append((todo, 'server', f"{escape(server.url)}: {escape(message)}"))
    else:
      user = server.whoami(server.token) if server.token else None
      checks.append((ok, 'server', escape(server.url) + (f" [dim](logged in as {escape(user)})[/dim]" if user else " [dim](no API token)[/dim]")))

    if not ai_available():
      checks.append((todo, 'AI assistant', f"not installed: {escape(INSTALL_AI)}"))
    else:
      llm = LLM.from_settings(self.model)
      ready = llm.api_key or llm.is_local or llm.provider == 'anthropic'
      checks.append((ok if ready else todo, 'AI assistant', f"{escape(llm.model or 'model to choose')} · {escape(llm.label)}" if ready else "not set up yet"))
    return checks

  # 1. Project --------------------------------------------------------------
  def step_project(self) -> ProjectFacts:
    ui = self.ui
    ui.step("Your project")
    top = git(self.root, 'rev-parse', '--show-toplevel')
    if top and Path(top).resolve() != self.root:
      ui.warn(f"You're in a subfolder of the git repository [bold]{escape(top)}[/bold].")
      ui.hint("qaboard.yaml usually lives at the root of the repository. Monorepos can add a qaboard.yaml per subproject later.")
      if ui.confirm("Set up QA-Board at the root of the repository?", default=ui.interactive):
        self.root = Path(top).resolve()

    with ui.spinner("Looking around…"):
      facts = detect(self.root)

    rows = [('folder', escape(str(facts.root)))]
    if facts.is_git:
      rows.append(('git remote', escape(facts.remote_url) if facts.remote_url else '[yellow]none[/yellow]'))
      count = len(facts.dirty_files)
      rows.append(('git status', f"[yellow]{count} uncommitted change{'s' if count > 1 else ''}[/yellow]" if count else '[green]clean[/green]'))
    else:
      rows.append(('git', '[yellow]not a git repository[/yellow]'))
    rows.append(('languages', ', '.join(facts.languages) or '[dim]?[/dim]'))
    if facts.build_systems:
      rows.append(('build', ', '.join(facts.build_systems)))
    rows.append(('CI', ', '.join(facts.ci) or '[dim]none found[/dim]'))
    site = site_qaboard_config()
    if site:
      rows.append(('site defaults', f"{', '.join(site)} [dim]({escape(short_path(site_qaboard_config_path()))})[/dim]"))
    ui.facts(rows)
    ui.console.print()

    if not facts.is_git:
      ui.warn("QA-Board organizes results by git commit. You can continue, and run [bold]git init[/bold] later.")
      if not ui.confirm("Continue without git?", default=True):
        raise Cancelled()
    if facts.dirty_files:
      ui.hint("The wizard only adds qaboard.yaml and qa/, and shows you every change before writing anything.")

    name = facts.project_name or facts.root.name
    self.answers.update(name=name, reference_branch=facts.reference_branch, url=facts.remote_url)
    if facts.is_git:
      ui.info(f"Project [bold]{escape(name)}[/bold], results compared to the latest commit on [bold]{escape(facts.reference_branch)}[/bold]")
      if not ui.confirm("Right?", default=True):
        self.answers['name'] = ui.ask("Project name", default=name)
        self.answers['reference_branch'] = ui.ask("Reference branch", default=facts.reference_branch)
    else:
      self.answers['name'] = ui.ask("Project name", default=name)

    self.check_existing_qa_dir()
    self.ask_inputs(site)
    return facts

  def check_existing_qa_dir(self):
    main = self.root / 'qa' / 'main.py'
    if not main.exists():
      return
    try:
      defines_run = 'def run(' in main.read_text(errors='replace')
    except OSError:
      defines_run = False
    if defines_run:
      self.ui.info("qa/main.py already defines run(): it stays the entrypoint.")
    else:
      self.entrypoint = 'qa/qaboard_main.py'
      self.ui.info(f"qa/ already holds your own files: the QA-Board entrypoint will be [bold]{self.entrypoint}[/bold].")

  def ask_inputs(self, site: Dict[str, Any]):
    ui = self.ui
    site_database = (site.get('inputs') or {}).get('database')
    if site_database:
      try:
        from ..conventions import location_from_spec
        base = location_from_spec(site_database, {'project': self.answers['name'], 'subproject': ''})
      except Exception:
        ui.info("Test inputs are stored where your site's defaults say.")
        return
      self.database = base
      folder = ui.ask(f"Which folder under {escape(str(base))} holds this project's inputs? (relative, empty to skip)", default='')
      if folder:
        self.look_at_inputs(base / folder, prefix=folder.strip('/'))
      return

    folder = ui.ask("Where are your test inputs? (a folder, empty to skip)", default='')
    if not folder:
      return
    path = Path(folder).expanduser()
    path = (path if path.is_absolute() else Path.cwd() / path).resolve()
    self.database = path
    # Inside the repository, a relative path works for everyone, in CI too
    try:
      self.answers['database'] = path.relative_to(self.root).as_posix() or '.'
    except ValueError:
      self.answers['database'] = str(path)
    if path.is_dir():
      self.look_at_inputs(path)
    else:
      ui.warn(f"{escape(str(path))} isn't a folder we can read from here. We'll use it anyway, check it later in qaboard.yaml.")

  def look_at_inputs(self, folder: Path, prefix: str = ''):
    ui = self.ui
    if not folder.is_dir():
      ui.warn(f"{escape(str(folder))} isn't a folder we can read from here.")
      return
    with ui.spinner("Looking at your inputs…"):
      inputs = guess_inputs(folder)
    if not inputs.glob:
      ui.hint("No input files found there yet.")
      return
    examples = [f"{prefix}/{e}" if prefix else e for e in inputs.examples]
    if inputs.use_parent_folder:
      ui.ok(f"Found {inputs.count}+ folders of [bold]{escape(inputs.glob)}[/bold]-like sequences, e.g. {escape(', '.join(examples))}")
      ui.hint("Each folder will be one input (inputs.use_parent_folder).")
    else:
      ui.ok(f"Found {inputs.count}{'+' if inputs.count >= 3000 else ''} [bold]{escape(inputs.glob)}[/bold] file{'s' if inputs.count != 1 else ''}, e.g. {escape(', '.join(examples))}")
    if inputs.use_parent_folder and not ui.confirm("Is each folder one input?", default=True):
      self.answers['glob'] = ui.ask("Which files are inputs? (glob)", default=f"*{Path(inputs.glob).suffix}")
      self.answers['use_parent_folder'] = False
      self.answers['examples'] = []
      return
    self.answers['glob'] = ui.ask("Which files identify inputs? (glob)", default=inputs.glob)
    # A different glob: the folders were probably not the inputs
    self.answers['use_parent_folder'] = inputs.use_parent_folder and self.answers['glob'] == inputs.glob
    self.answers['examples'] = examples

  # 2. Server ---------------------------------------------------------------
  def step_server(self, storage: bool = True):
    ui = self.ui
    ui.step("QA-Board server")
    server = Server.from_settings()
    self.server = server
    online, message = False, ''
    config: Dict[str, Any] = {}
    if server.is_default:
      ui.hint(f"Ask your admins for its URL. To start one: {DOCS}/deploy")
      typed = ui.ask("QA-Board URL (empty if you don't have one yet)", default='') if ui.interactive else ''
      if not typed:
        ui.note("No server for now: `qa run` works without one, and you can run `qa wizard` again or set QABOARD_URL later.")
        if storage:
          self.ask_storage()
        return
      with ui.spinner("Calling the server…"):
        online, message, config = server.probe_typed(typed)
    else:
      with ui.spinner(f"Calling {server.url}…"):
        online, message, config = server.probe()

    while not online:
      ui.warn(f"Can't reach {escape(server.url)}: {escape(message)}")
      if not ui.interactive or not ui.confirm("Try another URL?", default=True):
        ui.note("No problem: `qa run` works offline, results will show up once the server is reachable.")
        break
      typed = ui.ask("QA-Board URL", default=server.url)
      with ui.spinner("Calling the server…"):
        online, message, config = server.probe_typed(typed)
    self.server_online = online

    if online:
      login = config.get('login_type')
      ui.ok(f"Connected to [bold]{escape(server.url)}[/bold]" + (f" [dim](login: {escape(str(login))})[/dim]" if login else ''))
      if server.url != Server.from_settings().url:
        self.to_save['QABOARD_URL'] = server.url
      self.step_token(server, config)
    self.save_settings("the server settings")
    if storage:
      self.ask_storage()

  def step_token(self, server: Server, config: Dict[str, Any]):
    ui = self.ui
    if server.token:
      with ui.spinner("Checking your API token…"):
        user = server.whoami(server.token)
      if user:
        ui.ok(f"Logged in as [bold]{escape(user)}[/bold] with your API token")
        return
      ui.warn("Your API token (QA_TOKEN) isn't valid on this server.")
    ui.hint("An API token lets `qa` and your scripts act as you: tag milestones, delete outputs...")
    if config.get('login_type') == 'SAML':
      ui.hint(f"This server uses single sign-on: to get a token, see {DOCS}/installation#api-token")
      return
    if not ui.confirm("Log in now to get an API token?", default=False):
      return
    if server.is_insecure:
      ui.warn(f"{escape(server.url)} doesn't use https: your password would be sent unencrypted.")
      if not ui.confirm("Send it anyway?", default=False):
        return
    for _ in range(3):
      username = ''
      while not username:
        username = ui.ask("Username", default=os.environ.get('USER', os.environ.get('USERNAME', '')))
      password = ui.ask("Password [dim](used once to create the token, never saved)[/dim]", password=True)
      try:
        with ui.spinner("Logging in…"):
          token = server.login_for_token(username, password)
          user = server.whoami(token) or username
      except Exception as e:
        ui.fail(f"Could not log in: {escape(str(e))}")
        if not ui.confirm("Try again?", default=True):
          return
        continue
      server.token = token
      self.to_save['QA_TOKEN'] = token
      ui.ok(f"Got an API token for [bold]{escape(user)}[/bold] 🔑")
      return

  def ask_storage(self):
    """Where `qa --share` and CI save results, unless the site says."""
    ui = self.ui
    if site_qaboard_config().get('storage'):
      return
    ui.hint(f"Shared results (`qa --share`, CI) go to a folder the QA-Board server can read too. The template uses {DEFAULT_STORAGE}.")
    storage = ui.ask(f"Where should shared results go? (empty for {DEFAULT_STORAGE})", default='')
    if storage:
      self.answers['storage'] = str(Path(storage).expanduser())

  def save_settings(self, what: str):
    ui = self.ui
    if not self.to_save:
      return
    path = short_path(user_settings_path())
    for name, shadows in sorted(shadowing_settings(list(self.to_save)).items()):
      ui.warn(f"{', '.join(shadows)} {'is' if len(shadows) == 1 else 'are'} set and will win over the {name} saved in {path}: change {'it' if len(shadows) == 1 else 'them'} too.")
    names = ', '.join(sorted(self.to_save))
    if self.dryrun:
      ui.hint(f"Dry run: would save {names} to {path}")
    # Without a terminal we never change the user's settings
    elif ui.confirm(f"Save {what} to {path}?", default=ui.interactive):
      try:
        save_user_settings(self.to_save)
        ui.ok(f"Saved {names} [dim](readable only by you)[/dim]")
      except Exception as e:
        ui.fail(f"Could not save them: {escape(str(e))}")
    self.to_save = {}

  # Template ----------------------------------------------------------------
  def template_changes(self, facts: ProjectFacts) -> ChangeSet:
    changes = ChangeSet(self.root, extra_writable=('.gitignore',))
    changes.stage('qaboard.yaml', self.qaboard_yaml())
    for rel in TEMPLATE_FILES:
      target = self.entrypoint if rel == 'qa/main.py' else rel
      if (self.root / target).exists():
        if rel != 'qa/main.py':
          self.ui.hint(f"{target} already exists, we keep yours.")
        continue
      content = (SAMPLE_PROJECT / rel).read_text().replace('Edit qa/main.py', f'Edit {self.entrypoint}')
      if rel == 'qa/batches.yaml' and self.answers.get('examples'):
        content = content.replace("    - A.jpg\n    - B.jpg\n", ''.join(f"    - {yaml_scalar(e)}\n" for e in self.answers['examples']))
      changes.stage(target, content)
    # `qa run` saves results in output/, they don't belong in git
    if facts.is_git and git(self.root, 'check-ignore', '-q', 'output/x') is None:
      gitignore = self.root / '.gitignore'
      try:
        current = gitignore.read_text(errors='replace') if gitignore.is_file() else ''
      except OSError:
        current = ''
      try:
        changes.stage('.gitignore', current + ('' if not current or current.endswith('\n') else '\n') + "# Local results of `qa run`\noutput/\n")
      except (UnsafePath, OSError):
        self.ui.hint("Add output/ to your .gitignore: `qa run` saves results there.")
    return changes

  def qaboard_yaml(self) -> str:
    from ..init import use_site_defaults
    content = (SAMPLE_PROJECT / 'qaboard.yaml').read_text()
    answers = self.answers
    content = content.replace('name: user/sample_project', f"name: {yaml_scalar(answers['name'])}")
    if answers.get('url'):
      content = content.replace('url: git@github.com/user/sample_project', f"url: {yaml_scalar(answers['url'])}")
    else:
      content = content.replace('  url: git@github.com/user/sample_project', '  # url: <your git remote, e.g. git@gitlab.example.com:group/project.git>')
    content = content.replace('reference_branch: master', f"reference_branch: {yaml_scalar(answers['reference_branch'])}")
    if self.entrypoint != 'qa/main.py':
      content = content.replace('entrypoint: qa/main.py', f"entrypoint: {self.entrypoint}")
    if answers.get('database'):
      key = 'windows' if os.name == 'nt' else 'linux'
      default = '    windows: C://\n' if os.name == 'nt' else '    linux: /\n'
      content = content.replace(default, f"    {key}: {yaml_scalar(answers['database'])}\n")
    if answers.get('glob'):
      content = content.replace("  # globs: '*.jpg'\n", f"  globs: {yaml_scalar(answers['glob'])}\n")
    if answers.get('use_parent_folder'):
      content = content.replace("  # use_parent_folder: false\n", "  use_parent_folder: true\n", 1)
    if answers.get('storage'):
      key = 'windows' if os.name == 'nt' else 'linux'
      content = content.replace(f"storage:\n  linux: {DEFAULT_STORAGE}\n", f"storage:\n  {key}: {yaml_scalar(answers['storage'])}\n")
    site = copy.deepcopy(site_qaboard_config())
    if site:
      # What the user just answered wins over the site's defaults
      inputs: Dict[str, Any] = site['inputs'] if isinstance(site.get('inputs'), dict) else {}
      if answers.get('glob'):
        for key in ('globs', 'glob', 'use_parent_folder'):
          inputs.pop(key, None)
      if answers.get('storage'):
        site.pop('storage', None)
      if self.entrypoint != 'qa/main.py' and isinstance(site.get('project'), dict):
        site['project'].pop('entrypoint', None)
      content = use_site_defaults(content, {k: v for k, v in site.items() if v != {}})
    return content

  # 3. AI -------------------------------------------------------------------
  def step_ai(self, facts: ProjectFacts, changes: ChangeSet) -> ChangeSet:
    ui = self.ui
    ui.step("AI assistant")
    if not ai_available():
      self.hint_install_ai()
      ui.hint("Continuing with the template: it's commented, and easy to edit by hand.")
      return changes

    ui.note("It reads your code and wires qa/main.py and qaboard.yaml to it. It can't run commands, never reads files "
            "that look like secrets, can only change qa/ and qaboard.yaml, and you review everything before it's written.")
    if not self.ai and not ui.confirm(f"Let an AI adapt {self.entrypoint} to your code?", default=True):
      return changes
    chat = self.start_chat(facts, changes)
    if not chat:
      return changes
    hint = ui.ask("Anything it should know? (e.g. \"the CLI is build/denoise --in X --out Y\", empty to skip)", default='')
    if hint:
      self.answers['hint'] = hint
    from .agent import project_brief
    if chat.say(project_brief(facts, self.answers, self.entrypoint, setup=True)):
      # Settings are only saved once they've proven to work
      self.save_settings("the AI settings")
    if ui.interactive:
      chat.loop()
    return changes

  def start_chat(self, facts: ProjectFacts, changes: ChangeSet) -> Optional['Chat']:
    llm = self.configure_llm(LLM.from_settings(self.model))
    if not llm:
      self.ui.note("Skipping the AI assistant.")
      return None
    try:
      self.chat = Chat(self, llm, facts, changes)
    except Exception as e:
      self.ui.fail(f"Could not set up the AI assistant: {escape(str(e))}")
      return None
    self.ui.info(f"Working with [bold]{escape(llm.model or '')}[/bold] · {escape(llm.label)}. Ctrl+C stops it.")
    return self.chat

  def hint_install_ai(self):
    self.ui.info("An AI assistant can adapt qa/main.py to your code, add metrics, visualizations... To enable it:")
    self.ui.note(f"[bold]{escape(INSTALL_AI)}[/bold]    or    [bold]{escape(RUN_AI)}[/bold]")

  def show_round(self, before: ChangeSet, after: ChangeSet) -> bool:
    """Shows what a round of the AI changed. Returns whether it changed anything."""
    changed = False
    for rel, change in sorted(after.changes.items()):
      previous = before.changes[rel].content if rel in before.changes else change.original
      if change.content == previous:
        continue
      changed = True
      diff = diff_text(rel, previous, change.content)
      removed = sum(1 for line in diff.splitlines() if line.startswith('-') and not line.startswith('---'))
      # Mostly rewritten: the new file reads better than a wall of red
      if previous and removed > len(previous.splitlines()) / 2:
        self.ui.show_file(f"{rel} (rewritten by the AI)", change.content, rel)
      else:
        self.ui.diff(f"{rel} (changes by the AI)", diff)
    return changed

  def configure_llm(self, llm: LLM) -> Optional[LLM]:
    ui = self.ui
    if site_config('QABOARD_LLM_BASE_URL') or site_config('QABOARD_LLM_PROVIDER'):
      ui.ok(f"Using {escape(llm.label)} [dim](from your settings)[/dim]")
    elif ui.interactive:
      default = 'a' if llm.provider == 'anthropic' else ('o' if llm.is_openai else 'c')
      choice = ui.choose("Which API?", [
        ('a', "Anthropic: Claude"),
        ('o', "OpenAI"),
        ('c', "Another OpenAI-compatible API: your company's gateway, ollama, vLLM, LiteLLM..."),
      ], default=default)
      if choice == 'a' and llm.provider != 'anthropic':
        llm = LLM.from_settings(self.model, provider='anthropic')
      elif choice in ('o', 'c') and llm.provider != 'openai':
        llm = LLM.from_settings(self.model, provider='openai')
      if choice == 'c':
        self.set_base_url(llm, ui.ask("API base URL", default='' if llm.is_openai else llm.base_url))
      if llm.provider != LLM.guess_provider():
        self.to_save['QABOARD_LLM_PROVIDER'] = llm.provider

    models = None
    # Claude: the SDK also finds credentials by itself (ANTHROPIC_AUTH_TOKEN, `ant auth login`...)
    sdk_credentials = llm.provider == 'anthropic' and not llm.api_key and llm.list_models()[0] is not None
    while True:
      if llm.is_insecure:
        ui.warn(f"{escape(llm.base_url)} doesn't use https: your code and API key would be sent unencrypted.")
        if not ui.confirm("Use it anyway?", default=False):
          return None
      if not llm.api_key and ui.interactive and not sdk_credentials:
        if not self.ask_key(llm):
          return None
      with ui.spinner(f"Checking {llm.host}…"):
        models, message, status = llm.list_models()
      if models is None and status == 404 and not llm.base_url.endswith('/v1'):
        candidate, _, _ = llm.list_models(llm.base_url + '/v1')
        if candidate is not None and ui.confirm(f"{escape(llm.base_url)} answers 404. Did you mean {escape(llm.base_url)}/v1?", default=True):
          # Same server: the key stays valid
          llm.base_url = llm.base_url + '/v1'
          if 'QABOARD_LLM_BASE_URL' in self.to_save or 'QABOARD_LLM_API_KEY' in self.to_save:
            self.to_save['QABOARD_LLM_BASE_URL'] = llm.base_url
          continue
      if models is not None:
        count = len(chat_models(models))
        ui.ok(f"Connected to [bold]{escape(llm.host)}[/bold] ({count} model{'s' if count != 1 else ''})")
        break
      ui.warn(f"{escape(llm.host)}: {escape(message)}")
      if not ui.interactive:
        return None
      action = ui.choose("What now?", [
        ('e', "Edit the URL"), ('k', "Enter another key"), ('t', "Try anyway (some gateways don't list models)"), ('s', "Skip the AI"),
      ], default='k' if status in (401, 403) else 'e')
      if action == 'e':
        self.set_base_url(llm, ui.ask("API base URL", default=llm.base_url))
      elif action == 'k':
        llm.api_key, sdk_credentials = None, False
      elif action == 't':
        break
      else:
        return None

    listed = chat_models(models or [])
    if self.model:
      # Asked for explicitly: used as is
      llm.model = self.model
      if listed and self.model not in listed:
        ui.warn(f"{escape(llm.host)} doesn't list {escape(self.model)}, trying it anyway.")
    elif not llm.model or (listed and llm.model not in listed):
      if llm.model and listed:
        ui.warn(f"{escape(llm.host)} doesn't list the model {escape(llm.model)}.")
      if listed and ui.interactive:
        ui.hint(f"Available: {escape(', '.join(listed[:15]))}{', ...' if len(listed) > 15 else ''}")
      llm.model = ui.ask("Model", default=suggest_model(listed) or llm.model or '')
      if not llm.model:
        return None
      self.to_save['QABOARD_LLM_MODEL'] = llm.model
    return llm

  def set_base_url(self, llm: LLM, url: str):
    url = url.strip().rstrip('/')
    if not url or url == llm.base_url:
      return
    if '://' not in url:
      url = f"{'http' if url.startswith(('localhost', '127.0.0.1')) else 'https'}://{url}"
    llm.base_url, llm.api_key = url, None
    self.to_save['QABOARD_LLM_BASE_URL'] = url
    self.to_save.pop('QABOARD_LLM_API_KEY', None)

  def ask_key(self, llm: LLM) -> bool:
    ui = self.ui
    if llm.key_url:
      ui.hint(f"Get a key at {escape(llm.key_url)}")
    elif llm.is_openai:
      ui.hint("Get a key at https://platform.openai.com/api-keys")
    elif llm.is_anthropic_api:
      ui.hint("Get a key at https://platform.claude.com, or log in with `ant auth login`")
    if llm.is_local:
      llm.api_key = ui.ask(f"API key for {escape(llm.host)} [dim](empty if your server doesn't need one)[/dim]", password=True) or None
      if not llm.api_key:
        return True
    else:
      llm.api_key = ui.ask(f"API key for {escape(llm.host)} [dim](empty to skip the AI)[/dim]", password=True) or None
      if not llm.api_key:
        return False
    self.to_save['QABOARD_LLM_API_KEY'] = llm.api_key
    # A saved key goes with its API: never sent to another one configured later
    self.to_save['QABOARD_LLM_BASE_URL'] = llm.base_url
    self.to_save['QABOARD_LLM_PROVIDER'] = llm.provider
    return True

  # 4. Review ---------------------------------------------------------------
  def step_review(self, facts: ProjectFacts, changes: ChangeSet, template: Optional[ChangeSet] = None) -> int:
    """One review, one approval, for every change to the files QA-Board owns."""
    ui = self.ui
    ui.step("Review")
    while True:
      pending = changes.pending()
      if not pending:
        ui.info("Nothing to change.")
        return 0
      rows = []
      for change in pending:
        added, removed = changes.stats(change)
        status = '[green]new[/green]' if change.original is None else '[yellow]modified[/yellow]'
        rows.append((status, f"{escape(change.rel)}  [green]+{added}[/green]" + (f" [red]-{removed}[/red]" if removed else '')))
      ui.facts(rows)
      if self.dryrun:
        ui.info("Dry run: nothing was written.")
        return 0
      if not ui.interactive:
        break
      options = [('w', f"Write {'it' if len(pending) == 1 else f'these {len(pending)} files'}"), ('d', "Show the changes")]
      if self.chat:
        options.append(('c', "Keep chatting with the assistant"))
        if template is not None and any(changes.read(c.rel) != template.read(c.rel) for c in pending if c.rel in template.changes):
          options.append(('t', "Use the plain template instead of the AI's changes"))
      options.append(('q', "Quit without writing anything"))
      action = ui.choose("All good?", options, default='w')
      if action == 'd':
        with ui.pager():
          for change in pending:
            ui.diff(change.rel, changes.diff(change))
      elif action == 'c' and self.chat:
        self.chat.loop()
      elif action == 't' and template is not None:
        changes = copy.deepcopy(template)
        self.chat, self.todo, self.try_input = None, [], None
        ui.ok("Back to the plain template.")
      elif action == 'q':
        raise Cancelled()
      else:
        break
    try:
      changes.apply()
    except Exception as e:
      ui.fail(f"Nothing was written: {escape(str(e))}")
      return 1
    ui.ok(f"Wrote {', '.join(c.rel for c in pending)}")
    entrypoint = changes.changes.get(self.entrypoint)
    self.wired = bool(self.chat) and entrypoint is not None and template is not None and \
      self.entrypoint in template.changes and entrypoint.content != template.changes[self.entrypoint].content
    self.outro(facts, sorted({c.rel.split('/')[0] for c in pending}), changes)
    return 0

  def outro(self, facts: ProjectFacts, written: List[str], changes: ChangeSet):
    ui = self.ui
    if self.chat:
      self.try_input = self.chat.deps.try_input or self.try_input
      self.todo = list(dict.fromkeys([*self.todo, *self.chat.deps.todo]))
    candidates = [self.try_input, *(self.answers.get('examples') or [])]
    # Only suggest to run on an input that we know exists
    try_input = next((c for c in candidates if c and self.database and (self.database / c).exists()), None)
    try_command = f"qa run --input {shlex.quote(try_input)}" if try_input else "qa run --input path/to/input"
    wired = self.wired

    steps = Table.grid(padding=(0, 2))
    steps.add_column(style='bold cyan', no_wrap=True)
    steps.add_column(style='dim')
    if not wired and not self.guiding:
      steps.add_row(Text(f"$EDITOR {self.entrypoint}"), "make run(context) call your code")
    steps.add_row(Text(try_command), "run it on one input")
    steps.add_row("qa batch my-batch", "run on the inputs listed in qa/batches.yaml")
    if self.storage_is_ready():
      steps.add_row("qa --share batch my-batch", "share the results in QA-Board")
    if self.guiding:
      title = "✔ Your QA-Board integration is updated. Give it a try:\n"
    elif wired:
      title = f"✔ QA-Board is set up, and {self.entrypoint} calls your code\n"
    else:
      title = f"✔ QA-Board files created. Next, make {self.entrypoint} call your code\n"
    parts: List[Any] = [Text(title, style='bold green'), steps]
    if self.todo:
      todo = Text("\nTo check:\n", style='bold')
      for item in self.todo:
        todo.append("• ", style='yellow')
        todo.append(f"{item}\n")
      todo.rstrip()
      parts.append(todo)
    footer = Text("\n")
    if facts.is_git:
      cd = f"cd {shlex.quote(str(self.root))} && " if self.root != Path.cwd().resolve() else ''
      footer.append("Commit the changes:\n" if self.guiding else "Commit the new files:\n", style='dim')
      message = 'Improve the QA-Board integration' if self.guiding else 'Track results with QA-Board'
      footer.append(f"{cd}git add {' '.join(written)} && git commit -m '{message}'\n", style='cyan')
    if self.server and self.server_online:
      footer.append(f"Once you run `qa --share batch` (or CI does), results show up at {self.server.url.rstrip('/')}/{self.answers['name']}\n", style='dim')
    if facts.ci:
      footer.append(f"Run `qa batch` in {facts.ci[0]} on each commit: {DOCS}/ci-integration\n", style='dim')
    footer.append(f"Show your outputs and metrics: {DOCS}/visualizations", style='dim')
    parts.append(footer)
    ui.panel(Group(*parts), title="🎉 Next steps", style='green')

    ui.celebrate()
    if try_input and ui.interactive and ui.confirm(f"Try [bold]{escape(try_command)}[/bold] now?", default=True):
      self.try_run(try_input)

  def try_run(self, input: str):
    ui = self.ui
    ui.console.rule(style='dim')
    args = ['--offline'] if not self.server_online else []
    # Never a shell, and only `qa run --input PATH`. Started like `qa`, so that configuration errors are shown.
    code = "import sys; sys.argv[0] = 'qa'; from qaboard.cli import main; main()"
    env = dict(os.environ)
    if not self.server_online:
      # --offline: no need to warn that we don't know where the server is
      env.setdefault('QABOARD_URL', self.server.url if self.server else 'http://localhost:5151')
    process = subprocess.run([sys.executable, '-c', code, *args, 'run', '--input', input], cwd=self.root, env=env)
    ui.console.rule(style='dim')
    if process.returncode == 0:
      ui.ok("[bold]It ran![/bold] Outputs and logs are under output/. Next: [bold]qa batch my-batch[/bold]")
    else:
      ui.fail(f"The run failed, see above. Fix {self.entrypoint}, then: qa run --input {escape(shlex.quote(input))}")

  def storage_is_ready(self) -> bool:
    if site_qaboard_config().get('storage'):
      return True
    return Path(self.answers.get('storage') or DEFAULT_STORAGE).is_dir()


class Chat:
  """
  A conversation with the AI assistant. Its edits accumulate in the ChangeSet: nothing is written before
  the user reviews them all at once.
  """
  def __init__(self, wizard: 'Wizard', llm: LLM, facts: ProjectFacts, changes: ChangeSet):
    from .agent import Deps, make_agent, build_model
    model, self.settings = build_model(llm)
    self.agent = make_agent(model)
    self.wizard = wizard
    self.ui = wizard.ui
    self.deps = Deps(changes=changes, facts=facts, ui=wizard.ui)
    self.initial = copy.deepcopy(changes)
    self.history: Optional[list] = None
    self.turns = 0

  def say(self, prompt: str) -> bool:
    """One turn of the conversation. Returns whether it worked."""
    from .agent import READ_BUDGET, run_agent
    ui = self.ui
    before = copy.deepcopy(self.deps.changes)
    try:
      result = run_agent(self.agent, self.deps, prompt, self.history, self.settings)
    except KeyboardInterrupt:
      self.restore(before)
      ui.warn("Stopped. What the assistant changed in this turn was dropped.")
      return False
    except Exception as e:
      self.restore(before)
      ui.fail(f"The AI assistant failed: {escape(describe_llm_error(e))}")
      if ui.interactive:
        ui.hint("Send your message again to retry.")
      return False
    self.history = result.all_messages()
    self.turns += 1
    self.deps.read_budget, self.deps.questions_left = READ_BUDGET, 2
    ui.assistant(result.output or "Done.")
    self.wizard.show_round(before, self.deps.changes)
    usage = result.usage() if callable(result.usage) else result.usage
    tokens = (getattr(usage, 'input_tokens', 0) or 0) + (getattr(usage, 'output_tokens', 0) or 0)
    requests = getattr(usage, 'requests', 0)
    ui.console.print(f"[dim]  {requests} step{'s' if requests != 1 else ''}" + (f" · {tokens / 1000:.0f}k tokens" if tokens >= 1000 else '') + "[/dim]")
    return True

  def restore(self, snapshot: ChangeSet):
    # The same ChangeSet object: the agent keeps working on it
    self.deps.changes.changes = copy.deepcopy(snapshot.changes)

  def loop(self):
    """The user talks with the assistant until they're done."""
    ui = self.ui
    ui.hint("Ask for anything about the integration. An empty line when you're done: you'll review all the changes at once. "
            "/diff shows them, /reset drops them.")
    while True:
      message = ui.chat_input()
      if not message or message in ('/done', '/quit', '/exit', '/review'):
        return
      if message in ('/diff', '/changes'):
        if not self.wizard.show_round(self.initial, self.deps.changes):
          ui.info("No changes yet.")
      elif message == '/reset':
        self.restore(self.initial)
        ui.ok("Dropped all the changes of this conversation.")
      elif message.startswith('/'):
        ui.hint("Commands: /diff, /reset, or an empty line when you're done.")
      else:
        self.say(message)


def section(config: Dict[str, Any], key: str) -> Dict[str, Any]:
  value = config.get(key)
  return value if isinstance(value, dict) else {}


def merged(*configs: Dict[str, Any]) -> Dict[str, Any]:
  """Configurations merged key by key, later ones win (lists are replaced)."""
  result: Dict[str, Any] = {}
  for config in configs:
    for key, value in (config or {}).items():
      if isinstance(value, dict) and isinstance(result.get(key), dict):
        result[key] = merged(result[key], value)
      else:
        result[key] = value
  return result


def try_run_input(command: Optional[str]) -> Optional[str]:
  """The input of a `qa run --input PATH` command, None for any other command."""
  args = safe_split(command)
  if len(args) == 4 and args[:2] == ['qa', 'run'] and args[2] in ('--input', '-i') and not args[3].startswith('-'):
    return args[3]
  return None


def safe_split(command: Optional[str]) -> List[str]:
  try:
    return shlex.split(command) if command else []
  except ValueError:
    return []


def short_path(path: Optional[Path]) -> str:
  """~/... instead of /home/user/..."""
  if not path:
    return ''
  try:
    return '~/' + Path(path).expanduser().resolve().relative_to(Path.home().resolve()).as_posix()
  except (ValueError, RuntimeError):
    return str(path)


def yaml_scalar(value: str) -> str:
  """A value written safely in YAML, quoted only if needed."""
  dumped = yaml.safe_dump(value, default_flow_style=True, width=10_000, allow_unicode=True).strip()
  return dumped[:-4].strip() if dumped.endswith('\n...') or dumped.endswith('...') else dumped


def describe_llm_error(e: Exception) -> str:
  """LLM errors, in a few words."""
  name = type(e).__name__
  status = getattr(e, 'status_code', None)
  if status in (401, 403):
    return "the API key was rejected"
  if status == 404:
    return "model not found (or the base URL is wrong)"
  if status == 429:
    return "rate limited or out of credits"
  if status:
    body = getattr(e, 'body', None)
    message = None
    if isinstance(body, dict):
      error = body.get('error')
      message = body.get('message') or (error.get('message') if isinstance(error, dict) else error)
    return f"HTTP {status}" + (f": {str(message)[:200]}" if message else '')
  if name == 'UsageLimitExceeded':
    return "it took too many steps"
  if name == 'UnexpectedModelBehavior':
    return f"the model didn't follow the tools protocol ({e}). Try a more capable model."
  message = str(e).splitlines()[0] if str(e) else name
  return message[:300]


__all__: List[str] = ['run_wizard']
