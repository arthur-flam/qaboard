"""
An AI agent that adapts the template qa/main.py and qaboard.yaml to the user's code.

Built with Pydantic AI (https://ai.pydantic.dev), and any LLM behind an OpenAI-compatible API.
The agent can't run commands and can't write anywhere but qa/ and qaboard.yaml: its tools only
read the project (minus secrets, which are also redacted from what it reads) and stage changes in a ChangeSet,
that the user reviews before anything is written.
"""
import fnmatch
import os
import re
import ssl
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, List, Optional, Set

import yaml
from pydantic import BaseModel, Field

# Pydantic AI advertises its observability service in the terminal, not what users came for
os.environ.setdefault('PYDANTIC_AI_NO_BANNER', '1')
from pydantic_ai import Agent, ModelRetry, RunContext  # noqa: E402
from pydantic_ai.usage import UsageLimits  # noqa: E402
from rich.markup import escape  # noqa: E402

from .changes import CONTROL_CHARS, ChangeSet, UnsafePath, read_text, safe_path  # noqa: E402
from .detect import ProjectFacts  # noqa: E402
from .settings import LLM  # noqa: E402
from .ui import UI  # noqa: E402


# Values that look like credentials are replaced before anything is sent to the LLM
SECRET_VALUES = re.compile(
  r'-----BEGIN [A-Z ]*PRIVATE KEY-----[\s\S]*?(?:-----END [A-Z ]*PRIVATE KEY-----|\Z)'
  r'|\b(?:AKIA|ASIA)[0-9A-Z]{16}\b'
  r'|\bgh[pousr]_[A-Za-z0-9]{30,}'
  r'|\bgithub_pat_[A-Za-z0-9_]{30,}'
  r'|\bglpat-[A-Za-z0-9_-]{20,}'
  r'|\bsk-[A-Za-z0-9_-]{20,}'
  r'|\b[sr]k_(?:live|test)_[A-Za-z0-9]{16,}'
  r'|\bxox[abprs]-[A-Za-z0-9-]{10,}'
  r'|https://hooks\.slack\.com/services/[A-Za-z0-9/]+'
  r'|\bAIza[0-9A-Za-z_-]{35}'
  r'|\bya29\.[0-9A-Za-z_-]{20,}'
  r'|\bhf_[A-Za-z0-9]{30,}'
  r'|\bnpm_[A-Za-z0-9]{30,}'
  r'|\bpypi-[A-Za-z0-9_-]{50,}'
  r'|\bphx_[A-Za-z0-9]{20,}'
  r'|\beyJ[A-Za-z0-9_-]{10,}\.eyJ[A-Za-z0-9_-]{10,}\.[A-Za-z0-9_-]{10,}'
  # credentials in URLs: https://user:password@host
  r'|://(?P<url>[^/\s:@]+:[^/\s@]+)@'
  # password = "...", api_key: '...'
  r'|(?i:(?:password|passwd|secret|token|api_?key|access_?key|private_?key)\w*["\']?[ \t]{0,3}[:=][ \t]{0,3}["\'])(?P<quoted>(?!https?://)[^"\'\s/]{8,})(?=["\'])'
  # unquoted in config files, if it looks random enough (has a digit, no variable)
  r'|(?im:(?:password|passwd|secret|token|api_?key|access_?key|private_?key)\w*[ \t]{0,3}[:=][ \t]{0,3})(?P<unquoted>(?=[^\s"\'$({]*\d)[^\s"\'$({#,;]{12,})$'
)
READ_BUDGET = 400_000      # characters of the project the agent may read in total
MAX_LINES = 400            # per read
MAX_QUESTIONS = 3


def redact(text: str) -> str:
  def replace(match: re.Match) -> str:
    for group in ('url', 'quoted', 'unquoted'):
      if match.group(group):
        start, end = match.span(group)
        return match.group(0)[:start - match.start()] + '[REDACTED]' + match.group(0)[end - match.start():]
    return '[REDACTED]'
  return SECRET_VALUES.sub(replace, text)


class Outcome(BaseModel):
  summary: List[str] = Field(description="What you changed and why, one short sentence per item, for the user.")
  try_command: Optional[str] = Field(None, description="A command to try the integration on one real input, like `qa run --input path/to/input`. Only if you know a real input.")
  todo: List[str] = Field(default_factory=list, description="What the user still needs to do or check, if anything. Be concrete and brief.")


@dataclass
class Deps:
  changes: ChangeSet
  facts: ProjectFacts
  ui: UI
  read_paths: Set[str] = field(default_factory=set)
  read_budget: int = READ_BUDGET
  questions_left: int = MAX_QUESTIONS

  @property
  def root(self) -> Path:
    return self.changes.root


INSTRUCTIONS = """
You integrate a software project with QA-Board, an experiment tracking tool for algorithm engineers.
Users run their code on test inputs with the `qa` CLI, and compare outputs and metrics between commits in a web app.

A template integration is already staged: qaboard.yaml, qa/main.py, qa/batches.yaml and qa/metrics.yaml.
Your job: adapt it so that `qa run --input <some input>` really runs THIS project's code on one input.

How the integration works:
- `qa run -i INPUT` calls `run(context)` in qa/main.py (`project.entrypoint` in qaboard.yaml).
- `context.input_path` (absolute pathlib.Path to the input), `context.rel_input_path` (relative to the database),
  `context.output_dir` (Path, exists, save every output file there), `context.configs` (list of configurations,
  user-defined meaning, often file names or dicts of parameters), `context.params` (dict, all dict configs merged with
  tuning parameters), `context.platform`, `context.forwarded_args` (extra CLI args after `--`), `context.dryrun`
  (when true, only print what would be done, don't run anything).
- `run` returns a dict of metrics, e.g. {"is_failed": False, "psnr": 31.2}. Returning {"is_failed": True} marks a failure.
  It must not raise for expected failures: catch them, print why, and return {"is_failed": True}.
- Optionally `postprocess(runtime_metrics, context)` computes more metrics from the files in context.output_dir.
- Metrics returned must be described in qa/metrics.yaml (`available_metrics`, with label, smaller_is_better, target...),
  and listed in `main_metrics` / `summary_metrics` if they matter. Only add metrics the code really produces.
- qaboard.yaml: `inputs.database` is where inputs are stored, `inputs.globs` identifies inputs when running a batch
  on a folder, `outputs.visualizations` lists output files to show in the web app (images, plotly JSON, text, HTML...),
  `artifacts` lists build outputs needed to run (binaries, configs) saved by `qa save-artifacts` in CI.
- qa/batches.yaml defines named lists of inputs, run with `qa batch NAME`.

How to work:
1. Explore first: list files, read the README, the build files and the main entry points (CLI scripts, `main` functions,
   argparse/click/typer definitions, executables built by CMake or Make...). Find how the code is run on one input
   and what it outputs. Search before guessing. Read the staged template files before editing them.
2. Make qa/main.py call the project's code: import its Python API if it has a clean one, otherwise run its CLI or
   executable with subprocess (a list of arguments, check the return code, stream or print its output so it shows in
   logs, run it with cwd=context.output_dir or pass output paths inside context.output_dir). Honor context.dryrun.
   Parse the metrics the code prints or saves, if any, and return them.
3. Keep edits focused: keep the template's helpful comments and structure where they still apply, remove the
   placeholder parts that you replaced. Prefer `edit_file` for small changes to existing files.
4. Update qaboard.yaml (globs, visualizations for outputs the code writes, artifacts if there is a build), qa/metrics.yaml
   and qa/batches.yaml (only with input paths you actually saw or were told about) to match.
5. Ask the user (ask_user) only when something essential can't be found in the code, like which of several programs
   is the algorithm, or where test inputs are. Never ask for passwords, tokens or keys.

Rules: you can only write qaboard.yaml and files under qa/. Never put secrets in code. Don't invent files,
functions or command-line flags that you haven't seen in the project. Write clear, simple Python 3, with a few comments.
When done, give a short summary.
""".strip()


def build_model(llm: LLM):
  from openai import AsyncOpenAI, DefaultAsyncHttpxClient
  from pydantic_ai.models.openai import OpenAIChatModel
  from pydantic_ai.providers.openai import OpenAIProvider
  verify: Any = llm.verify
  if isinstance(verify, str):
    verify = ssl.create_default_context(capath=verify) if os.path.isdir(verify) else ssl.create_default_context(cafile=verify)
  client = AsyncOpenAI(
    base_url=llm.base_url,
    # Local servers (ollama, vLLM...) often don't need a key, but the client wants one
    api_key=llm.api_key or 'not-needed',
    http_client=DefaultAsyncHttpxClient(verify=verify, timeout=300),
    max_retries=2,
  )
  return OpenAIChatModel(llm.model or '', provider=OpenAIProvider(openai_client=client))


def make_agent(model) -> 'Agent[Deps, Outcome]':
  agent = Agent(model, deps_type=Deps, output_type=Outcome, instructions=INSTRUCTIONS, retries=5)

  # Tools are async, so they run one at a time on the event loop, even when the model calls several at once:
  # it keeps the output readable, and questions to the user don't overlap.
  @agent.tool
  async def list_files(ctx: RunContext[Deps], directory: str = '.', pattern: str = '*') -> str:
    """
    Lists the project's files (ignored and huge folders excluded) under a directory, recursively.
    `pattern` is a glob matched against file paths relative to the project's root, e.g. '*.py' or 'src/*/main.*'.
    """
    deps = ctx.deps
    activity(deps, '📂', f"list {directory if directory not in ('.', '') else 'files'}{'' if pattern == '*' else f' {pattern}'}")
    prefix = '' if directory in ('.', './', '') else directory.strip('/') + '/'
    staged = [c.rel for c in deps.changes.pending() if c.original is None]
    files = sorted(set(deps.facts.files) | set(staged))
    matches = [f for f in files if f.startswith(prefix) and (fnmatch.fnmatch(f, pattern) or fnmatch.fnmatch(Path(f).name, pattern))]
    if not matches:
      return f"No files match {pattern!r} under {directory!r}."
    shown = matches[:300]
    more = f"\n... and {len(matches) - len(shown)} more, narrow it down with directory or pattern." if len(matches) > len(shown) else ''
    return '\n'.join(shown) + more

  @agent.tool
  async def read_file(ctx: RunContext[Deps], path: str, start_line: int = 1, max_lines: int = 200) -> str:
    """Reads a text file of the project (its staged version if you changed it), with line numbers."""
    deps = ctx.deps
    activity(deps, '📖', f"read {path}")
    try:
      content = deps.changes.read(path)
    except UnsafePath as e:
      return f"Error: {e}"
    if content is None:
      return f"Error: {path} doesn't exist."
    if deps.read_budget <= 0:
      return "Error: you've read enough of the project. Finish with what you know."
    # Redacted before slicing: a private key's body has no recognizable marker on its own
    lines = redact(content).splitlines()
    start = max(1, start_line)
    end = min(len(lines), start - 1 + max(1, min(max_lines, MAX_LINES)))
    excerpt = '\n'.join(f"{i:>5}  {lines[i - 1]}" for i in range(start, end + 1))
    deps.read_budget -= len(excerpt)
    deps.read_paths.add(safe_path(deps.root, path).relative_to(deps.root).as_posix())
    header = f"{path}: lines {start}-{end} of {len(lines)}" if (start > 1 or end < len(lines)) else f"{path}: {len(lines)} lines"
    return f"{header}\n{excerpt}"

  @agent.tool
  async def search(ctx: RunContext[Deps], regex: str, path_pattern: str = '*') -> str:
    """Searches the project's text files with a Python regular expression. Returns matching lines as path:line: text."""
    deps = ctx.deps
    activity(deps, '🔎', f"search {regex!r}{'' if path_pattern == '*' else f' in {path_pattern}'}")
    try:
      compiled = re.compile(regex)
    except re.error as e:
      raise ModelRetry(f"Invalid regular expression: {e}")
    if deps.read_budget <= 0:
      return "Error: you've read enough of the project. Finish with what you know."
    hits: List[str] = []
    for rel in deps.facts.files:
      if not (fnmatch.fnmatch(rel, path_pattern) or fnmatch.fnmatch(Path(rel).name, path_pattern)):
        continue
      try:
        # Like read_file: no secrets, no symlinks out of the project
        content = read_text(safe_path(deps.root, rel))
      except (UnsafePath, OSError):
        continue
      for number, line in enumerate(redact(content or '').splitlines(), start=1):
        if compiled.search(line):
          hits.append(f"{rel}:{number}: {line.strip()[:200]}")
          if len(hits) >= 60:
            break
      if len(hits) >= 60:
        hits.append("... more matches, refine the search.")
        break
    result = '\n'.join(hits) if hits else "No matches."
    deps.read_budget -= len(result)
    return result

  @agent.tool
  async def write_file(ctx: RunContext[Deps], path: str, content: str) -> str:
    """
    Creates or replaces a whole file: qaboard.yaml or a file under qa/. Nothing is written on disk until the user approves.
    You must read an existing file before replacing it.
    """
    return stage(ctx.deps, path, content)

  @agent.tool
  async def edit_file(ctx: RunContext[Deps], path: str, old_text: str, new_text: str) -> str:
    """
    Replaces an exact, unique snippet of a file (qaboard.yaml or under qa/) with new text.
    Include enough surrounding lines in old_text to make it unique. Read the file first.
    """
    deps = ctx.deps
    try:
      content = deps.changes.read(path)
    except UnsafePath as e:
      raise ModelRetry(str(e))
    if content is None:
      raise ModelRetry(f"{path} doesn't exist, use write_file to create it.")
    count = content.count(old_text)
    if count != 1:
      raise ModelRetry(f"old_text was found {count} times in {path}, it must match exactly once. Read the file again and copy the text exactly.")
    return stage(deps, path, content.replace(old_text, new_text))

  @agent.tool
  async def ask_user(ctx: RunContext[Deps], question: str) -> str:
    """Asks the user a short question, only when something essential can't be found in the project."""
    deps = ctx.deps
    if deps.questions_left <= 0:
      return "You've asked enough questions: make a reasonable choice and mention it in the todo list."
    deps.questions_left -= 1
    if not deps.ui.interactive:
      return "The user isn't available: make a reasonable choice and mention it in the todo list."
    deps.ui.console.print(f"\n[bold magenta]🤖 {escape(question)}[/bold magenta]")
    answer = deps.ui.ask("Your answer (empty to let the AI decide)", default='')
    return answer or "No preference: make a reasonable choice and mention it in the todo list."

  return agent


def stage(deps: Deps, path: str, content: str) -> str:
  try:
    resolved = safe_path(deps.root, path, for_write=True)
  except UnsafePath as e:
    raise ModelRetry(str(e))
  rel = resolved.relative_to(deps.root).as_posix()
  exists = rel in deps.changes.changes or resolved.exists()
  if exists and rel not in deps.read_paths:
    raise ModelRetry(f"Read {rel} with read_file before changing it.")
  validate(rel, content)
  before = deps.changes.read(rel)
  deps.changes.stage(rel, content)
  added, removed = line_stats(before, deps.changes.read(rel) or '')
  activity(deps, '✏️ ', f"{rel} [green]+{added}[/green] [red]-{removed}[/red]", markup=True)
  return f"Staged {rel} (+{added} -{removed} lines)."


def line_stats(before: Optional[str], after: str):
  import difflib
  added = removed = 0
  for line in difflib.ndiff((before or '').splitlines(), after.splitlines()):
    added += line.startswith('+ ')
    removed += line.startswith('- ')
  return added, removed


def validate(rel: str, content: str):
  """Syntax errors are sent back to the model, which fixes them."""
  if CONTROL_CHARS.search(content.replace('\r\n', '\n')):
    raise ModelRetry(f"{rel} contains control characters. Write plain text, use escapes like \\x1b in strings if needed.")
  if rel.endswith('.py'):
    try:
      compile(content, rel, 'exec')
    except SyntaxError as e:
      raise ModelRetry(f"Python syntax error in {rel} line {e.lineno}: {e.msg}. Fix it.")
  if rel.endswith(('.yaml', '.yml')):
    try:
      data = yaml.safe_load(content)
    except yaml.YAMLError as e:
      raise ModelRetry(f"Invalid YAML in {rel}: {e}. Fix it.")
    if rel == 'qaboard.yaml' and not (isinstance(data, dict) and isinstance(data.get('project'), dict)):
      raise ModelRetry("qaboard.yaml must be a mapping with a `project` section. Keep the template's structure.")
  if SECRET_VALUES.search(content):
    raise ModelRetry(f"{rel} contains what looks like a secret. Never write secrets: read them from environment variables.")


def activity(deps: Deps, icon: str, message: str, markup: bool = False):
  deps.ui.console.print(f"  [dim]{icon} {message if markup else escape(message)}[/dim]")
  deps.ui.update(deps.ui.thinking())


def run_agent(agent: 'Agent[Deps, Outcome]', deps: Deps, prompt: str, message_history: Optional[list] = None):
  """Returns the result. Raises the model's errors, the caller tells the user what happened."""
  limits = UsageLimits(request_limit=60)
  with deps.ui.spinner(deps.ui.thinking()):
    return agent.run_sync(prompt, deps=deps, message_history=message_history, usage_limits=limits)


def project_brief(facts: ProjectFacts, answers: dict) -> str:
  """What we tell the agent about the project to start with."""
  top_level = sorted({f.split('/')[0] + ('/' if '/' in f else '') for f in facts.files})
  lines = [
    "Here is what we know about the project.",
    f"- Name: {answers.get('name')}",
    f"- Languages: {', '.join(facts.languages) or 'unknown'}",
    f"- Build and packaging: {', '.join(facts.build_systems) or 'nothing detected'}",
    f"- CI: {', '.join(facts.ci) or 'none detected'}",
    f"- Number of files: {len(facts.files)}",
    f"- Top-level entries: {', '.join(top_level[:80])}",
  ]
  if answers.get('database'):
    lines.append(f"- Test inputs are stored under: {answers['database']}")
  if answers.get('glob'):
    lines.append(f"- Inputs look like: {answers['glob']}")
  if answers.get('examples'):
    lines.append(f"- Example inputs (relative to the database): {', '.join(answers['examples'])}")
  if answers.get('hint'):
    lines.append(f"- The user says: {answers['hint']}")
  lines.append("\nAdapt the staged template to this project.")
  return '\n'.join(lines)
