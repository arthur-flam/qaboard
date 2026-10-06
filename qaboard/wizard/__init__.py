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
from rich.markup import escape
from rich.console import Group
from rich.table import Table
from rich.text import Text

from ..config import find_configs
from ..site_config import site_qaboard_config, site_qaboard_config_path
from .changes import ChangeSet, diff_text
from .detect import ProjectFacts, detect, git, guess_inputs
from .settings import LLM, Server, save_user_settings, suggest_model, user_settings_path
from .ui import UI, is_interactive


SAMPLE_PROJECT = Path(__file__).resolve().parent.parent / 'sample_project'
TEMPLATE_FILES = ('qa/main.py', 'qa/batches.yaml', 'qa/metrics.yaml')
DOCS = 'https://samsung.github.io/qaboard/docs'
INSTALL_AI = "pip install 'qaboard[wizard]'"


class Cancelled(Exception):
  pass


def run_wizard(dryrun: bool = False, assume_yes: bool = False, ai: Optional[bool] = None, model: Optional[str] = None, root: Optional[Path] = None) -> int:
  ui = UI(interactive=is_interactive(assume_yes))
  # `qa` opens permissions for outputs shared by teams, but these are source files in a repository
  os.umask(0o022)
  try:
    return Wizard(ui, dryrun, ai, model, root or Path.cwd()).run()
  except (KeyboardInterrupt, EOFError, Cancelled):
    ui.console.print("\n[bold]👋 Cancelled.[/bold] Nothing was written to your project.")
    return 130


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
    self.outcome = None     # what the AI agent did

  def run(self) -> int:
    ui = self.ui
    ui.banner("qa init · set up QA-Board for this project" + ("  [dry run]" if self.dryrun else ""))

    configs = find_configs(self.root)
    if configs:
      ui.ok("This project already has a QA-Board configuration:")
      for _, path in configs:
        ui.hint(str(path))
      ui.hint(f"Edit it to change settings. Docs: {DOCS}/project-init")
      return 0

    ui.steps_total = 3 if self.ai is False else 4
    facts = self.step_project()
    self.step_server()
    template = self.template_changes(facts)
    changes = copy.deepcopy(template)
    if self.ai is not False:
      changes = self.step_ai(facts, changes)
    return self.step_review(facts, template, changes)

  # 1. Project --------------------------------------------------------------
  def step_project(self) -> ProjectFacts:
    ui = self.ui
    ui.step("Your project")
    top = git(self.root, 'rev-parse', '--show-toplevel')
    if top and Path(top).resolve() != self.root:
      ui.warn(f"You're in a subfolder of the git repository [bold]{escape(top)}[/bold].")
      ui.hint("qaboard.yaml usually lives at the root of the repository (subprojects can add their own later).")
      if ui.confirm("Set up QA-Board at the root of the repository?", default=True):
        self.root = Path(top).resolve()
        configs = find_configs(self.root)
        if configs:
          ui.ok(f"The repository already has a QA-Board configuration: {configs[0][1]}")
          raise typer.Exit(0)

    with ui.spinner("Looking around…"):
      facts = detect(self.root)

    rows = [('folder', escape(str(facts.root)))]
    if facts.is_git:
      rows.append(('git remote', escape(facts.remote_url) if facts.remote_url else '[yellow]none[/yellow]'))
      rows.append(('git status', f"[yellow]{len(facts.dirty_files)} uncommitted change{'s' if len(facts.dirty_files) > 1 else ''}[/yellow]" if facts.dirty_files else '[green]clean[/green]'))
    else:
      rows.append(('git', '[yellow]not a git repository[/yellow]'))
    rows.append(('languages', ', '.join(facts.languages) or '[dim]?[/dim]'))
    if facts.build_systems:
      rows.append(('build', ', '.join(facts.build_systems)))
    rows.append(('CI', ', '.join(facts.ci) or '[dim]none found[/dim]'))
    ui.facts(rows)
    ui.console.print()

    if not facts.is_git:
      ui.warn("QA-Board organizes results by git commit. You can continue, and run [bold]git init[/bold] later.")
      if not ui.confirm("Continue without git?", default=True):
        raise Cancelled()
    if facts.dirty_files:
      ui.hint("The wizard only adds qaboard.yaml and qa/, and shows you every change before writing anything.")

    self.answers['name'] = ui.ask("Project name", default=facts.project_name or facts.root.name)
    self.answers['reference_branch'] = ui.ask("Reference branch (results are compared to its latest commit)", default=facts.reference_branch)
    self.answers['url'] = facts.remote_url

    site_inputs = (site_qaboard_config().get('inputs') or {})
    if site_inputs.get('database'):
      ui.info(f"Test inputs: stored where your site's defaults say ([bold]{escape(str(site_inputs['database']))}[/bold]).")
      return facts

    database = ui.ask("Where are your test inputs? (a folder, empty to skip)", default='')
    if database:
      path = Path(database).expanduser()
      path = path if path.is_absolute() else (Path.cwd() / path)
      if not path.is_dir():
        ui.warn(f"{escape(str(path))} isn't a folder we can read here. We'll use it anyway, check it later in qaboard.yaml.")
        self.answers['database'] = str(path)
      else:
        with ui.spinner("Looking at your inputs…"):
          glob, examples = guess_inputs(path)
        self.answers['database'] = str(path.resolve())
        if glob:
          ui.ok(f"Found [bold]{escape(glob)}[/bold] files, e.g. {escape(', '.join(examples))}")
          self.answers['glob'] = ui.ask("Which files are inputs? (glob)", default=glob)
          self.answers['examples'] = examples
        else:
          ui.hint("No input files found there yet.")
    return facts

  # 2. Server ---------------------------------------------------------------
  def step_server(self):
    ui = self.ui
    ui.step("QA-Board server")
    server = Server.from_settings()
    if server.is_default:
      ui.hint("Ask your admins for the URL, or start a server with docker compose: " + f"{DOCS}/deploy")
      url = ui.ask("QA-Board URL", default=server.url).rstrip('/')
      if url != server.url:
        server.url = url
        server.is_default = False
      if ui.interactive:
        self.to_save['QABOARD_URL'] = server.url

    for attempt in range(3):
      with ui.spinner(f"Calling {server.url}…"):
        online, message, config = server.probe()
      if online:
        login = config.get('login_type')
        ui.ok(f"Connected to [bold]{escape(server.url)}[/bold]" + (f" [dim](login: {escape(str(login))})[/dim]" if login else ''))
        break
      ui.warn(f"Can't reach {escape(server.url)}: {escape(message)}")
      if attempt == 2 or not ui.interactive or not ui.confirm("Try another URL?", default=True):
        ui.hint("No problem: `qa run` works offline, results will show up once the server is reachable.")
        break
      server.url = ui.ask("QA-Board URL", default=server.url).rstrip('/')
      server.is_default = False
      self.to_save['QABOARD_URL'] = server.url
    self.server, self.server_online = server, online

    if online:
      self.step_token(server, config)
    self.save_settings("the server settings")

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
    for _ in range(3):
      username = ui.ask("Username", default=os.environ.get('USER', os.environ.get('USERNAME', '')))
      password = ui.ask("Password [dim](used once to create the token, never saved)[/dim]", password=True)
      try:
        with ui.spinner("Logging in…"):
          token = server.login_for_token(username, password)
      except Exception as e:
        ui.fail(f"Could not log in: {escape(str(e))}")
        if not ui.confirm("Try again?", default=True):
          return
        continue
      server.token = token
      self.to_save['QA_TOKEN'] = token
      ui.ok(f"Got an API token for [bold]{escape(username)}[/bold] 🔑")
      return

  def save_settings(self, what: str):
    ui = self.ui
    if not self.to_save:
      return
    path = user_settings_path()
    names = ', '.join(sorted(self.to_save))
    if self.dryrun:
      ui.hint(f"Dry run: would save {names} to {path}")
    # Without a terminal we never change the user's settings
    elif ui.confirm(f"Save {what} to {path}? [dim](readable only by you)[/dim]", default=ui.interactive):
      try:
        save_user_settings(self.to_save)
        ui.ok(f"Saved {names}")
      except Exception as e:
        ui.fail(f"Could not save them: {escape(str(e))}")
    self.to_save = {}

  # Template ----------------------------------------------------------------
  def template_changes(self, facts: ProjectFacts) -> ChangeSet:
    ui = self.ui
    changes = ChangeSet(self.root)
    changes.stage('qaboard.yaml', self.qaboard_yaml())
    for rel in TEMPLATE_FILES:
      if (self.root / rel).exists():
        ui.warn(f"{rel} already exists, we keep yours.")
        continue
      content = (SAMPLE_PROJECT / rel).read_text()
      if rel == 'qa/batches.yaml' and self.answers.get('examples'):
        content = content.replace("    - A.jpg\n    - B.jpg\n", ''.join(f"    - {yaml_scalar(e)}\n" for e in self.answers['examples']))
      changes.stage(rel, content)
    return changes

  def qaboard_yaml(self) -> str:
    from ..init import use_site_defaults
    content = (SAMPLE_PROJECT / 'qaboard.yaml').read_text()
    answers = self.answers
    content = content.replace('name: user/sample_project', f"name: {yaml_scalar(answers['name'])}")
    if answers.get('url'):
      content = content.replace('url: git@github.com/user/sample_project', f"url: {yaml_scalar(answers['url'])}")
    content = content.replace('reference_branch: master', f"reference_branch: {yaml_scalar(answers['reference_branch'])}")
    if answers.get('database'):
      key = 'windows' if os.name == 'nt' else 'linux'
      default = '    windows: C://\n' if os.name == 'nt' else '    linux: /\n'
      content = content.replace(default, f"    {key}: {yaml_scalar(answers['database'])}\n")
    if answers.get('glob'):
      content = content.replace("  # globs: '*.jpg'\n", f"  globs: {yaml_scalar(answers['glob'])}\n")
    site = site_qaboard_config()
    if site:
      self.ui.info(f"Using the site defaults from {escape(str(site_qaboard_config_path()))} for: {', '.join(site)}")
      content = use_site_defaults(content, site)
    return content

  # 3. AI -------------------------------------------------------------------
  def step_ai(self, facts: ProjectFacts, changes: ChangeSet) -> ChangeSet:
    ui = self.ui
    ui.step("AI assistant")
    try:
      import pydantic_ai  # noqa: F401
    except ImportError:
      if self.ai:
        ui.fail(f"The AI assistant needs a few more packages: [bold]{INSTALL_AI}[/bold]")
        raise typer.Exit(1)
      ui.info("An AI assistant can adapt qa/main.py to your code. To enable it:")
      ui.hint(f"{INSTALL_AI}    or    uvx --from 'qaboard[wizard]' qa init")
      ui.hint("Continuing with the template, it's easy to edit by hand.")
      return changes

    ui.hint("It reads your code and wires qa/main.py and qaboard.yaml to it. It can't run commands, "
            "never reads files that look like secrets, can only change qa/ and qaboard.yaml, and you review everything.")
    # Without a terminal, we don't send code to an LLM unless asked with --ai
    if not self.ai and not ui.confirm("Use the AI assistant?", default=ui.interactive):
      return changes

    llm = self.configure_llm()
    if not llm:
      return changes
    self.save_settings("the AI settings")

    from .agent import Deps, make_agent, build_model, run_agent, project_brief
    hint = ui.ask("Anything it should know? (e.g. \"the CLI is build/denoise --in X --out Y\", empty to skip)", default='')
    if hint:
      self.answers['hint'] = hint
    ui.info(f"Working with [bold]{escape(llm.model or '')}[/bold] at {escape(llm.host)}. Press Ctrl+C to stop.")
    agent = make_agent(build_model(llm))
    template = copy.deepcopy(changes)
    deps = Deps(changes=changes, facts=facts, ui=ui)
    prompt, history = project_brief(facts, self.answers), None
    while True:
      try:
        result = run_agent(agent, deps, prompt, history)
      except KeyboardInterrupt:
        ui.warn("Stopped the AI assistant.")
        return template if not ui.confirm("Keep the changes it made so far?", default=False) else changes
      except Exception as e:
        ui.fail(f"The AI assistant failed: {escape(describe_llm_error(e))}")
        if ui.interactive and ui.confirm("Try again?", default=False):
          continue
        ui.hint("Continuing with the template.")
        return template
      self.outcome = result.output
      usage = result.usage() if callable(result.usage) else result.usage
      tokens = (getattr(usage, 'input_tokens', 0) or 0) + (getattr(usage, 'output_tokens', 0) or 0)
      ui.ok(f"Done in {getattr(usage, 'requests', '?')} steps" + (f", {tokens / 1000:.0f}k tokens" if tokens else ''))
      self.show_outcome()
      if not ui.interactive:
        return changes
      for change in changes.pending():
        before = template.changes[change.rel].content if change.rel in template.changes else change.original
        if change.content != before:
          ui.diff(f"{change.rel} (AI changes to the template)" if change.rel in template.changes else change.rel, diff_text(change.rel, before, change.content))
      action = ui.choose("What now?", [
        ('a', "Accept, then review all files"),
        ('r', "Refine: tell the AI what to change"),
        ('t', "Throw away the AI changes, use the template"),
      ], default='a')
      if action == 'a':
        return changes
      if action == 't':
        self.outcome = None
        return template
      feedback = ui.ask("What should it change?")
      prompt, history = feedback or "Double-check your work.", result.all_messages()
      deps.questions_left = 2

  def configure_llm(self) -> Optional[LLM]:
    ui = self.ui
    llm = LLM.from_settings(self.model)
    ui.hint("It works with any OpenAI-compatible API: OpenAI, your company's LLM gateway, ollama, vLLM, LiteLLM...")
    base_url = ui.ask("API base URL", default=llm.base_url).rstrip('/')
    if base_url != llm.base_url:
      llm = LLM(base_url=base_url, api_key=None, model=llm.model, verify=llm.verify, key_url=llm.key_url)
      self.to_save['QABOARD_LLM_BASE_URL'] = base_url

    for attempt in range(3):
      if not llm.api_key and not llm.is_local:
        if not ui.interactive:
          ui.fail("No API key: set QABOARD_LLM_API_KEY" + (" or OPENAI_API_KEY" if llm.is_openai else ''))
          return None
        if llm.key_url:
          ui.hint(f"Get a key at {llm.key_url}")
        elif llm.is_openai:
          ui.hint("Get a key at https://platform.openai.com/api-keys")
        llm.api_key = ui.ask(f"API key for {escape(llm.host)} [dim](empty to skip the AI)[/dim]", password=True)
        if not llm.api_key:
          return None
        self.to_save['QABOARD_LLM_API_KEY'] = llm.api_key
      with ui.spinner(f"Checking {llm.host}…"):
        models, message = llm.list_models()
      if models is not None:
        ui.ok(f"Connected to [bold]{escape(llm.host)}[/bold] ({len(models)} models)")
        break
      ui.warn(f"{escape(llm.host)}: {escape(message)}")
      if message == "the API key was rejected" and ui.interactive and attempt < 2:
        llm.api_key = None
        self.to_save.pop('QABOARD_LLM_API_KEY', None)
        continue
      # Some gateways don't list models: we can still try
      if not ui.confirm("Try anyway?", default=False):
        self.to_save.pop('QABOARD_LLM_API_KEY', None)
        return None
      break

    if not llm.model or (models and llm.model not in models):
      if llm.model and models:
        ui.warn(f"{escape(llm.host)} doesn't list the model {escape(llm.model)}.")
      default = suggest_model(models or []) or llm.model or ''
      if models and ui.interactive:
        ui.hint(f"Available: {escape(', '.join(models[:12]))}{', ...' if len(models) > 12 else ''}")
      llm.model = ui.ask("Model", default=default)
      if not llm.model:
        return None
      self.to_save['QABOARD_LLM_MODEL'] = llm.model
    return llm

  def show_outcome(self):
    if not self.outcome:
      return
    body = Text()
    for item in self.outcome.summary:
      body.append("• ", style='green')
      body.append(f"{item}\n")
    if self.outcome.todo:
      body.append("\nTo check:\n", style='bold')
      for item in self.outcome.todo:
        body.append("• ", style='yellow')
        body.append(f"{item}\n")
    body.rstrip()
    self.ui.panel(body, title="🤖 What the AI did")

  # 4. Review ---------------------------------------------------------------
  def step_review(self, facts: ProjectFacts, template: ChangeSet, changes: ChangeSet) -> int:
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
    self.outro(facts)
    return 0

  def outro(self, facts: ProjectFacts):
    ui = self.ui
    example = (self.answers.get('examples') or [None])[0]
    try_command = (self.outcome.try_command if self.outcome else None) or (f"qa run --input {shlex.quote(example)}" if example else None)
    name = self.answers['name']
    steps = Table.grid(padding=(0, 2))
    steps.add_column(style='bold cyan', no_wrap=True)
    steps.add_column(style='dim')
    steps.add_row(try_command or "qa run --input path/to/input", "run your code on one input")
    if not self.outcome:
      steps.add_row("$EDITOR qa/main.py", "call your code from run(context)")
    steps.add_row("qa batch my-batch", "run on the inputs listed in qa/batches.yaml")
    steps.add_row("qa --share batch my-batch", "see the results in QA-Board")
    footer = Text()
    if facts.is_git:
      footer.append("Commit the new files: ", style='dim')
      footer.append("git add qaboard.yaml qa && git commit -m 'Track results with QA-Board'\n", style='cyan')
    if self.server and self.server_online:
      footer.append("Your project: ", style='dim')
      footer.append(f"{self.server.url.rstrip('/')}/{name}\n")
    if facts.ci:
      footer.append(f"In {facts.ci[0]}, run `qa batch` on each commit: {DOCS}/ci-integration\n", style='dim')
    footer.append(f"Next, show your outputs and metrics: {DOCS}/visualizations", style='dim')
    ui.panel(Group(Text("🎉 You're all set!\n", style='bold green'), steps, Text(), footer), title="Next steps", style='green')

    args = safe_split(try_command)
    # Never a shell: only `qa` and its arguments
    if args and args[0] == 'qa' and ui.interactive and ui.confirm(f"Try `{try_command}` now?", default=True):
      args = args[1:]
      if not self.server_online:
        args = ['--offline', *args]
      ui.console.rule(style='dim')
      subprocess.run([sys.executable, '-m', 'qaboard', *args], cwd=self.root)
      ui.console.rule(style='dim')


def safe_split(command: Optional[str]) -> List[str]:
  try:
    return shlex.split(command) if command else []
  except ValueError:
    return []


def yaml_scalar(value: str) -> str:
  """A value written safely in YAML, quoted only if needed."""
  dumped = yaml.safe_dump(value, default_flow_style=True, width=10_000).strip()
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
  if name == 'UsageLimitExceeded':
    return "it took too many steps"
  if name == 'UnexpectedModelBehavior':
    return f"the model didn't follow the tools protocol ({e}). Try a more capable model."
  message = str(e).splitlines()[0] if str(e) else name
  return message[:300]


__all__: List[str] = ['run_wizard']
