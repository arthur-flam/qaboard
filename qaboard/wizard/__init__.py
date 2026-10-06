"""
`qa init`: a wizard that sets up a project with QA-Board.

1. Project: detects the git remote, default branch, languages, build and CI; asks where test inputs live.
2. Server: finds and checks the QA-Board server, optionally logs in to get an API token.
3. AI assistant (optional, `pip install qaboard[wizard]`): an agent adapts qa/main.py to the project's code,
   through any OpenAI-compatible API: OpenAI, a company gateway set by the site package, ollama, vLLM...
4. Review: every change is shown before anything is written, and written atomically.

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
from .ui import UI, is_interactive


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
    self.outcome = None                      # what the AI agent did
    self.todo: List[str] = []                # what the AI says the user should check

  def run(self) -> int:
    ui = self.ui
    ui.banner("qa init · set up QA-Board for this project" + ("  [dry run]" if self.dryrun else ""))

    configs = find_configs(self.root)
    if configs:
      ui.ok("This project already has a QA-Board configuration:")
      for _, path in configs:
        ui.hint(escape(str(path)))
      ui.hint(f"Edit it to change settings, see {DOCS}/project-init")
      return 0

    ui.steps_total = 3 if self.ai is False else 4
    facts = self.step_project()
    self.step_server()
    template = self.template_changes(facts)
    changes = copy.deepcopy(template)
    if self.ai is not False:
      changes = self.step_ai(facts, changes)
    return self.step_review(facts, changes)

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
      ui.ok(f"Found {inputs.count}{'+' if inputs.count >= 3000 else ''} [bold]{escape(inputs.glob)}[/bold] files, e.g. {escape(', '.join(examples))}")
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
  def step_server(self):
    ui = self.ui
    ui.step("QA-Board server")
    server = Server.from_settings()
    self.server = server
    online, message, config = False, '', {}
    if server.is_default:
      ui.hint(f"Ask your admins for its URL. To start one: {DOCS}/deploy")
      typed = ui.ask("QA-Board URL (empty if you don't have one yet)", default='') if ui.interactive else ''
      if not typed:
        ui.note("No server for now: `qa run` works without one, and you can run `qa init` again or set QABOARD_URL later.")
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
      ui.info("An AI assistant can adapt qa/main.py to your code. To enable it:")
      ui.note(f"[bold]{escape(INSTALL_AI)}[/bold]    or    [bold]{escape(RUN_AI)}[/bold]")
      ui.hint("Continuing with the template: it's commented, and easy to edit by hand.")
      return changes

    llm = LLM.from_settings(self.model)
    ui.note("It reads your code and wires qa/main.py and qaboard.yaml to it. It can't run commands, never reads files "
            "that look like secrets, can only change qa/ and qaboard.yaml, and you review everything.")
    if not self.ai and not ui.confirm(f"Let an AI adapt qa/main.py to your code? [dim](it reads your code through {escape(llm.host)})[/dim]", default=True):
      return changes

    configured = self.configure_llm(llm)
    if not configured:
      ui.note("Skipping the AI assistant, using the template.")
      return changes
    llm = configured

    from .agent import Deps, make_agent, build_model, run_agent, project_brief, READ_BUDGET
    hint = ui.ask("Anything it should know? (e.g. \"the CLI is build/denoise --in X --out Y\", empty to skip)", default='')
    if hint:
      self.answers['hint'] = hint
    ui.info(f"Working with [bold]{escape(llm.model or '')}[/bold] at {escape(llm.host)}. Press Ctrl+C to stop.")
    try:
      agent = make_agent(build_model(llm))
    except Exception as e:
      ui.fail(f"Could not set up the AI assistant: {escape(str(e))}")
      ui.note("Continuing with the template.")
      return changes
    template = copy.deepcopy(changes)
    accepted, accepted_outcome = copy.deepcopy(changes), None   # what we go back to if a round fails
    deps = Deps(changes=changes, facts=facts, ui=ui)
    prompt, history = project_brief(facts, self.answers, self.entrypoint), None
    while True:
      before_round = copy.deepcopy(changes)
      try:
        result = run_agent(agent, deps, prompt, history)
      except KeyboardInterrupt:
        ui.warn("Stopped the AI assistant.")
        if self.show_round(before_round, changes) and ui.confirm("Keep the changes it made in this round?", default=False):
          return changes
        self.outcome = accepted_outcome
        return accepted
      except Exception as e:
        ui.fail(f"The AI assistant failed: {escape(describe_llm_error(e))}")
        if ui.interactive and ui.confirm("Try again?", default=False):
          deps.changes = changes = copy.deepcopy(before_round)
          continue
        ui.note("Continuing with the template." if accepted_outcome is None else "Keeping the changes from the previous round.")
        self.outcome = accepted_outcome
        return accepted

      # Settings are only saved once they've proven to work
      self.save_settings("the AI settings")
      self.outcome = result.output
      self.todo = list(dict.fromkeys([*self.todo, *result.output.todo]))
      usage = result.usage() if callable(result.usage) else result.usage
      tokens = (getattr(usage, 'input_tokens', 0) or 0) + (getattr(usage, 'output_tokens', 0) or 0)
      requests = getattr(usage, 'requests', 0)
      ui.ok(f"Done in {requests} step{'s' if requests != 1 else ''}" + (f", {tokens / 1000:.0f}k tokens" if tokens >= 1000 else f", {tokens} tokens" if tokens else ''))
      self.show_outcome()
      changed = self.show_round(before_round, changes)
      if not ui.interactive:
        return changes
      if not changed:
        ui.info("The AI didn't change anything.")
      options = [('a', "Accept, then review all files")] if changed or history else []
      options += [('r', "Refine: tell the AI what to change"), ('t', "Throw away the AI changes, use the template")]
      action = ui.choose("What now?", options, default=options[0][0])
      if action == 'a':
        return changes
      if action == 't':
        self.outcome, self.todo = None, []
        return template
      accepted, accepted_outcome = copy.deepcopy(changes), self.outcome
      feedback = ui.ask("What should it change")
      prompt, history = feedback or "Double-check your work.", result.all_messages()
      deps.questions_left = 2
      deps.read_budget = READ_BUDGET

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
    ui.hint("It works with any OpenAI-compatible API: OpenAI, your company's LLM gateway, ollama, vLLM, LiteLLM...")
    if site_config('QABOARD_LLM_BASE_URL'):
      ui.ok(f"Using the LLM API at [bold]{escape(llm.host)}[/bold] [dim](from your settings)[/dim]")
    else:
      self.set_base_url(llm, ui.ask("API base URL", default=llm.base_url))

    models = None
    while True:
      if llm.is_insecure:
        ui.warn(f"{escape(llm.base_url)} doesn't use https: your code and API key would be sent unencrypted.")
        if not ui.confirm("Use it anyway?", default=False):
          return None
      if not llm.api_key and ui.interactive:
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
        llm.api_key = None
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
    return True

  def show_outcome(self):
    if not self.outcome:
      return
    body = Text()
    for item in self.outcome.summary:
      body.append("• ", style='green')
      body.append(f"{item}\n")
    if self.todo:
      body.append("\nTo check:\n", style='bold')
      for item in self.todo:
        body.append("• ", style='yellow')
        body.append(f"{item}\n")
    body.rstrip()
    self.ui.panel(body, title="🤖 What the AI did")

  # 4. Review ---------------------------------------------------------------
  def step_review(self, facts: ProjectFacts, changes: ChangeSet) -> int:
    ui = self.ui
    ui.step("Review")
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
    if ui.interactive and ui.confirm("Show the full files?", default=False):
      with ui.pager():
        for change in pending:
          ui.diff(change.rel, changes.diff(change))

    if self.dryrun:
      ui.info("Dry run: nothing was written.")
      return 0
    if not ui.confirm("Write these files?", default=True):
      raise Cancelled()
    try:
      changes.apply()
    except Exception as e:
      ui.fail(f"Nothing was written: {escape(str(e))}")
      return 1
    ui.ok(f"Wrote {', '.join(c.rel for c in pending)}")
    self.outro(facts, sorted({c.rel.split('/')[0] for c in pending}))
    return 0

  def outro(self, facts: ProjectFacts, written: List[str]):
    ui = self.ui
    candidates = [try_run_input(self.outcome.try_command) if self.outcome else None, *(self.answers.get('examples') or [])]
    # Only suggest to run on an input that we know exists
    try_input = next((c for c in candidates if c and self.database and (self.database / c).exists()), None)
    try_command = f"qa run --input {shlex.quote(try_input)}" if try_input else "qa run --input path/to/input"
    wired = self.outcome is not None

    steps = Table.grid(padding=(0, 2))
    steps.add_column(style='bold cyan', no_wrap=True)
    steps.add_column(style='dim')
    if not wired:
      steps.add_row(Text(f"$EDITOR {self.entrypoint}"), "make run(context) call your code")
    steps.add_row(Text(try_command), "run it on one input")
    steps.add_row("qa batch my-batch", "run on the inputs listed in qa/batches.yaml")
    if self.storage_is_ready():
      steps.add_row("qa --share batch my-batch", "share the results in QA-Board")
    parts: List[Any] = [
      Text(f"✔ QA-Board is set up, and {self.entrypoint} calls your code\n" if wired else f"✔ QA-Board files created. Next, make {self.entrypoint} call your code\n", style='bold green'),
      steps,
    ]
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
      footer.append("Commit the new files:\n", style='dim')
      footer.append(f"{cd}git add {' '.join(written)} && git commit -m 'Track results with QA-Board'\n", style='cyan')
    if self.server and self.server_online:
      footer.append(f"Once you run `qa --share batch` (or CI does), results show up at {self.server.url.rstrip('/')}/{self.answers['name']}\n", style='dim')
    if facts.ci:
      footer.append(f"Run `qa batch` in {facts.ci[0]} on each commit: {DOCS}/ci-integration\n", style='dim')
    footer.append(f"Show your outputs and metrics: {DOCS}/visualizations", style='dim')
    parts.append(footer)
    ui.panel(Group(*parts), title="🎉 Next steps", style='green')

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
