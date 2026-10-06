"""
An AI agent that adapts the template qa/main.py and qaboard.yaml to the user's code.

Built with Pydantic AI (https://ai.pydantic.dev), and any LLM behind an OpenAI-compatible API.
The agent can't run commands and can't write anywhere but qa/ and qaboard.yaml: its tools only
read the project (minus secrets, which are also redacted from what it reads) and stage changes in a ChangeSet,
that the user reviews before anything is written.
"""
import dataclasses
import fnmatch
import os
import re
import ssl
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional, Set

import yaml

# Pydantic AI advertises its observability service in the terminal, not what users came for
os.environ.setdefault('PYDANTIC_AI_NO_BANNER', '1')
from pydantic_ai import Agent, ModelRetry, RunContext  # noqa: E402
from pydantic_ai.usage import UsageLimits  # noqa: E402
from rich.markup import escape  # noqa: E402

from .changes import CONTROL_CHARS, ChangeSet, UnsafePath, read_text, safe_path  # noqa: E402
from .detect import ProjectFacts  # noqa: E402
from .settings import LLM  # noqa: E402
from .ui import UI  # noqa: E402


# password, db_password, aws_secret_access_key, client_secret... but not tokenizer, num_tokens or passwords_list
KEY_NAME = r'(?<![A-Za-z0-9])(?:password|passwd|secret|token|api[_-]?key|access[_-]?key|private[_-]?key)(?:[_-][A-Za-z0-9]{1,12}){0,3}'

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
  # password = "...", DB_PASSWORD=..., client_secret: '...'. Values must look random (have a digit), so that
  # tokenizer = "bert-base-uncased" or "token_type": "access_token" stay readable.
  r'|(?i:' + KEY_NAME + r'["\']?[ \t]{0,3}[:=][ \t]{0,3}["\'])(?P<quoted>(?=[^"\'\s]{0,200}\d)(?!https?://)[^"\'\s/]{8,200})(?=["\'])'
  # unquoted, in config files: only characters keys are made of, up to the end of the line
  r'|(?im:' + KEY_NAME + r'[ \t]{0,3}[:=][ \t]{0,3}(?P<unquoted>(?=[A-Za-z0-9+/=_-]{0,200}\d)[A-Za-z0-9+/=_-]{12,200})(?=[ \t]*\r?$))'
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


@dataclass
class Deps:
  changes: ChangeSet
  facts: ProjectFacts
  ui: UI
  read_paths: Set[str] = field(default_factory=set)
  read_budget: int = READ_BUDGET
  questions_left: int = MAX_QUESTIONS
  # What the agent tells the wizard, besides its answers
  try_input: Optional[str] = None
  todo: List[str] = field(default_factory=list)

  @property
  def root(self) -> Path:
    return self.changes.root


INSTRUCTIONS = """
You are the QA-Board wizard's assistant. You help engineers integrate their project with QA-Board, an experiment
tracking tool for algorithm engineers: they run their code on test inputs with the `qa` CLI, and compare outputs and
metrics between commits in a web app. You talk with the user in a terminal: be friendly, concrete and brief.

How the integration works:
- `qa run -i INPUT` calls `run(context)` in the entrypoint (`project.entrypoint` in qaboard.yaml, usually qa/main.py).
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
- qa/batches.yaml defines named lists of inputs, run with `qa batch NAME`. Results are shared with `qa --share batch`, and in CI.
- Docs: https://samsung.github.io/qaboard/docs (visualizations, computing-quantitative-metrics, batches-running-on-multiple-inputs,
  ci-integration, specifying-configurations, tuning-workflows).

How to work:
1. Explore before answering or editing: list files, read the README, the build files and the entry points (CLI scripts,
   `main` functions, argparse/click/typer definitions, executables built by CMake or Make...). Search before guessing.
   Read a file before editing it.
2. To make the entrypoint call the project's code: import its Python API if it has a clean one, otherwise run its CLI or
   executable with subprocess (a list of arguments, check the return code, print its output so it shows in logs, write
   outputs inside context.output_dir). Honor context.dryrun. Parse the metrics the code prints or saves, if any.
3. Keep edits focused: keep helpful comments and structure, remove placeholders you replaced. Prefer `edit_file`.
4. Keep qaboard.yaml, qa/metrics.yaml and qa/batches.yaml consistent with the code (only input paths you saw or were told about).
5. When you know a real input to try the integration on, call `suggest_try_input`. Record what the user must still do
   or check with `add_todo`.
6. When something essential can't be found in the code, ask: with `ask_user` in the middle of a task, or simply end your
   answer with the question. Never ask for passwords, tokens or keys.

Your edits are only staged: the user reviews all of them at the end and writes them in one go, so never tell them a file
was saved. End each answer with a short summary of what you changed (if anything) and, when useful, one suggestion of
what to do next. Use short Markdown: a few bullets, `code`, no headings, no tables.

Rules: you can only write qaboard.yaml and files under qa/. Never put secrets in code. Don't invent files, functions or
command-line flags that you haven't seen in the project. Write clear, simple Python 3, with a few comments.
""".strip()


# Models that take Anthropic's server-side refusal fallback ("fallbacks": "default")
FALLBACK_MODELS = ('claude-fable-5-1', 'claude-opus-5-5', 'claude-opus-5', 'claude-sonnet-5-5')


def build_model(llm: LLM):
  """The Pydantic AI model for the configured API, and its settings."""
  if llm.provider == 'anthropic':
    return build_anthropic_model(llm)
  from openai import AsyncOpenAI, DefaultAsyncHttpxClient
  from pydantic_ai.models.openai import OpenAIChatModel
  from pydantic_ai.providers.openai import OpenAIProvider
  verify: Any = llm.verify
  if verify is True:
    # Like requests (used to check the API): internal CAs are often given with REQUESTS_CA_BUNDLE, httpx ignores it
    verify = os.environ.get('REQUESTS_CA_BUNDLE') or os.environ.get('CURL_CA_BUNDLE') or True
  if isinstance(verify, str):
    verify = ssl.create_default_context(capath=verify) if os.path.isdir(verify) else ssl.create_default_context(cafile=verify)
  client = AsyncOpenAI(
    base_url=llm.base_url,
    # Local servers (ollama, vLLM...) often don't need a key, but the client wants one
    api_key=llm.api_key or 'not-needed',
    http_client=DefaultAsyncHttpxClient(verify=verify, timeout=300),
    max_retries=2,
  )
  return OpenAIChatModel(llm.model or '', provider=OpenAIProvider(openai_client=client)), {}


def build_anthropic_model(llm: LLM):
  """Claude, through the official anthropic SDK."""
  from pydantic_ai.models.anthropic import AnthropicModel
  from pydantic_ai.providers.anthropic import AnthropicProvider
  if llm.verify is True and (os.environ.get('REQUESTS_CA_BUNDLE') or os.environ.get('CURL_CA_BUNDLE')):
    llm = dataclasses.replace(llm, verify=os.environ.get('REQUESTS_CA_BUNDLE') or os.environ.get('CURL_CA_BUNDLE') or True)
  model = AnthropicModel(llm.model or '', provider=AnthropicProvider(anthropic_client=llm.anthropic_client(asynchronous=True)))
  settings: Dict[str, Any] = {}
  if llm.is_anthropic_api:
    # Agentic coding: high effort (Claude Opus 5.5 defaults to medium). The instructions and tools are the same
    # every turn: cached, which makes a long chat much cheaper.
    settings.update(anthropic_effort='high', anthropic_cache=True)
    if llm.model in FALLBACK_MODELS:
      # If a safety classifier declines a request, another model continues it instead of stopping
      settings.update(anthropic_betas=['server-side-fallback-2026-07-01'], extra_body={'fallbacks': 'default'})
  return model, settings


def make_agent(model) -> 'Agent[Deps, str]':
  agent = Agent(model, deps_type=Deps, output_type=str, instructions=INSTRUCTIONS, retries=5)

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
    except (UnsafePath, OSError) as e:
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
      if not content or not compiled.search(content):
        continue
      # Redacting is slow: only for files that match
      for number, line in enumerate(redact(content).splitlines(), start=1):
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
    except (UnsafePath, OSError) as e:
      raise ModelRetry(str(e))
    if content is None:
      raise ModelRetry(f"{path} doesn't exist, use write_file to create it.")
    count = content.count(old_text)
    if count != 1:
      raise ModelRetry(f"old_text was found {count} times in {path}, it must match exactly once. Read the file again and copy the text exactly.")
    return stage(deps, path, content.replace(old_text, new_text))

  @agent.tool
  async def suggest_try_input(ctx: RunContext[Deps], input_path: str) -> str:
    """Records a real input (relative to inputs.database) the user can try the integration on with `qa run --input`."""
    if not input_path or input_path.startswith('-') or '\n' in input_path:
      raise ModelRetry("Give a single input path, relative to the database.")
    ctx.deps.try_input = input_path
    return "Noted: the wizard will offer to run it."

  @agent.tool
  async def add_todo(ctx: RunContext[Deps], item: str) -> str:
    """Records something the user must still do or check (shown at the end). One short, concrete sentence."""
    if item and item not in ctx.deps.todo:
      ctx.deps.todo.append(item.strip()[:300])
    return "Noted."

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
  try:
    before = deps.changes.read(rel)
    deps.changes.stage(rel, content)
  except (UnsafePath, OSError) as e:
    raise ModelRetry(str(e))
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


def run_agent(agent: 'Agent[Deps, str]', deps: Deps, prompt: str, message_history: Optional[list] = None, model_settings: Optional[dict] = None):
  """Returns the result. Raises the model's errors, the caller tells the user what happened."""
  limits = UsageLimits(request_limit=60)
  with deps.ui.spinner(deps.ui.thinking()):
    return agent.run_sync(prompt, deps=deps, message_history=message_history, usage_limits=limits, model_settings=model_settings or None)  # type: ignore[arg-type]


def project_brief(facts: ProjectFacts, answers: dict, entrypoint: str = 'qa/main.py', setup: bool = True) -> str:
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
  if entrypoint != 'qa/main.py':
    lines.append(f"- The entrypoint is {entrypoint}, not qa/main.py: qa/main.py belongs to the project, don't change it.")
  if answers.get('database'):
    lines.append(f"- Test inputs are stored under: {answers['database']}")
  if answers.get('glob'):
    lines.append(f"- Inputs are identified by: {answers['glob']}" + (" (each input is the folder containing it)" if answers.get('use_parent_folder') else ''))
  if answers.get('examples'):
    lines.append(f"- Example inputs (relative to the database): {', '.join(answers['examples'])}")
  if setup:
    lines.append("\nThe wizard staged a template integration: qaboard.yaml, the entrypoint, qa/batches.yaml and qa/metrics.yaml. "
                 "Adapt it so that `qa run --input <some input>` really runs this project's code on one input.")
  else:
    lines.append(f"\nThe project already uses QA-Board: read qaboard.yaml and {entrypoint} before anything else. "
                 "Then help the user with what they ask: improving the integration, metrics, visualizations, batches, CI, "
                 "or explaining how QA-Board works.")
  if answers.get('hint'):
    lines.append(f"\nThe user says: {answers['hint']}")
  return '\n'.join(lines)
