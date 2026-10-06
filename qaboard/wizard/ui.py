"""
Terminal UI for the wizard, built with rich.
Everything goes to stderr: stdout stays clean for scripts. Without a terminal, or with --yes,
questions are answered with their defaults and the answers are printed, so logs tell what happened.
"""
import os
import random
import shutil
import sys
import time
from contextlib import contextmanager
from typing import Any, Iterator, List, Optional, Sequence

from rich.console import Console
from rich.markup import escape
from rich.padding import Padding
from rich.panel import Panel
from rich.prompt import Confirm, Prompt
from rich.syntax import Syntax
from rich.table import Table
from rich.text import Text

from .changes import visible


LOGO = r"""
   ___    _          ___                   _
  / _ \  /_\   ___  | _ ) ___  __ _  _ _ __| |
 | (_) |/ _ \ |___| | _ \/ _ \/ _` || '_/ _` |
  \__\_\/_/ \_\     |___/\___/\__,_||_| \__,_|
"""
# The banner's gradient, left to right
GRADIENT = ('#4c6ef5', '#7950f2', '#be4bdb', '#f06595')
ACCENT = '#7950f2'
SPARKLES = '✦✧⋆✶·'
CONFETTI_COLORS = ('#ff6b6b', '#fcc419', '#51cf66', '#339af0', '#cc5de8', '#f06595')

GUIDE_GREETINGS = (
  "Welcome back! Let's see how your project is doing.",
  "Back for more? Let's make your results even easier to read.",
  "Good to see you again. Here's how your integration looks.",
)

GREETINGS = (
  "Let's get your results out of the terminal and into a dashboard.",
  "Two minutes from now, every commit gets its own results page.",
  "Stop comparing screenshots in Slack. Let's set things up.",
  "No more results_final_v3_REAL/. Let's set things up.",
)

THINKING = (
  "Reading the code", "Following the imports", "Looking for a main()", "Squinting at the build files",
  "Connecting the dots", "Drafting qa/main.py", "Double-checking paths", "Thinking about metrics",
  "Counting pixels", "Asking the rubber duck", "Untangling argparse",
)


def blend(colors: Sequence[str], t: float) -> str:
  """A color between those of a gradient, t in [0, 1]."""
  t = min(max(t, 0.0), 1.0) * (len(colors) - 1)
  i = min(int(t), len(colors) - 2)
  a, b = colors[i], colors[i + 1]
  rgb = [round(int(a[k:k + 2], 16) + (int(b[k:k + 2], 16) - int(a[k:k + 2], 16)) * (t - i)) for k in (1, 3, 5)]
  return '#' + ''.join(f'{c:02x}' for c in rgb)


def gradient(text: str, colors: Sequence[str] = GRADIENT, bold: bool = True) -> Text:
  """Text colored with a horizontal gradient, the same for every line, so that blocks of text line up."""
  lines = text.split('\n')
  width = max((len(line) for line in lines), default=1) or 1
  out = Text()
  for n, line in enumerate(lines):
    for x, char in enumerate(line):
      out.append(char, style=f"{'bold ' if bold else ''}{blend(colors, x / width)}")
    if n < len(lines) - 1:
      out.append('\n')
  return out


def can_animate(console: Console) -> bool:
  """Animations only in a real terminal, never in CI or logs."""
  return (console.is_terminal and not console.is_dumb_terminal and not os.environ.get('CI')
          and not os.environ.get('NO_COLOR') and not os.environ.get('QA_NO_ANIMATIONS'))


class UI:
  def __init__(self, interactive: bool, console: Optional[Console] = None):
    self.console = console or Console(stderr=True, highlight=False)
    self.interactive = interactive
    self.animate = interactive and can_animate(self.console)
    self._status: Optional[Any] = None
    self.steps: List[str] = []
    self.step_index = 0

  # --- Layout -------------------------------------------------------------
  def banner(self, subtitle: str, greetings: Sequence[str] = GREETINGS):
    logo = LOGO.strip('\n')
    greeting = random.choice(greetings)

    lines = logo.splitlines()
    logo_width = max(len(line) for line in lines)
    width = logo_width + 6

    def frame(sparkles: int, seed: Optional[int] = None) -> Panel:
      rng = random.Random(seed)
      # a blank line above and below the logo, and a margin on its right: sparkles go there, never on the letters
      canvas = [[' '] * width, *[list(line.ljust(width)) for line in lines], [' '] * width]
      spots = [(y, x) for y in range(len(canvas)) for x in range(width)
               if canvas[y][x] == ' ' and (y in (0, len(canvas) - 1) or x > logo_width)]
      body = Text()
      stars = dict(((y, x), rng.choice(SPARKLES)) for y, x in rng.sample(spots, min(sparkles, len(spots))))
      for y, row in enumerate(canvas):
        for x, char in enumerate(row):
          if (y, x) in stars:
            body.append(stars[(y, x)], style=f'bold {rng.choice(GRADIENT)}')
          else:
            body.append(char, style=f'bold {blend(GRADIENT, x / logo_width)}')
        body.append('\n')
      body.append(f"\n{greeting}\n", style='italic')
      body.append(subtitle, style='dim')
      return Panel(body, border_style=ACCENT, padding=(0, 2), expand=False)

    if self.animate:
      from rich.live import Live
      with Live(frame(0), console=self.console, refresh_per_second=30, transient=True) as live:
        for i in range(16):
          live.update(frame(2 + i % 6))
          time.sleep(0.04)
    self.console.print(frame(5, seed=len(greeting)))

  def step(self, title: str):
    """A tracker of the wizard's steps: done, current, to do."""
    self.step_index += 1
    self.console.print()
    if not self.steps:
      self.console.rule(f"[bold]{escape(title)}[/bold]", align='left', style=ACCENT)
      return
    tracker = Text()
    for n, name in enumerate(self.steps, start=1):
      if n < self.step_index:
        tracker.append(f"✔ {name}", style='green')
      elif n == self.step_index:
        tracker.append(f"◉ {name}", style=f'bold {ACCENT}')
      else:
        tracker.append(f"○ {name}", style='dim')
      if n < len(self.steps):
        tracker.append("  ─  ", style='dim')
    self.console.rule(tracker, align='left', style=ACCENT, characters='─')

  def assistant(self, markdown: str):
    """What the AI assistant says."""
    from rich.markdown import Markdown
    self.console.print(Panel(Markdown(visible(markdown.strip()) or '…'), title="[bold]✨ assistant[/bold]", title_align='left',
                             border_style=ACCENT, padding=(0, 1)))

  def celebrate(self):
    """A little confetti, in a real terminal."""
    if not self.animate:
      return
    from rich.live import Live
    width = min(self.console.width, 72)

    def frame() -> Text:
      text = Text()
      for _ in range(2):
        for _ in range(width):
          text.append(random.choice(' ' * 6 + '*✦✧•⋆+'), style=random.choice(CONFETTI_COLORS))
        text.append('\n')
      return text
    with Live(frame(), console=self.console, refresh_per_second=24, transient=True) as live:
      for _ in range(16):
        live.update(frame())
        time.sleep(0.05)

  def ok(self, message: str):
    self.console.print(f"[green]✔[/green] {message}")

  def info(self, message: str):
    self.console.print(f"[blue]•[/blue] {message}")

  def hint(self, message: str):
    # wrapped lines are indented too
    self.console.print(Padding(Text.from_markup(message, style='dim'), (0, 0, 0, 2)))

  def note(self, message: str):
    """Like hint, but important enough to be readable on any theme."""
    self.console.print(Padding(Text.from_markup(message), (0, 0, 0, 2)))

  def warn(self, message: str):
    self.console.print(f"[yellow]▲[/yellow] {message}")

  def fail(self, message: str):
    self.console.print(f"[red]✘[/red] {message}")

  @contextmanager
  def pager(self) -> Iterator[None]:
    """Long outputs go to $PAGER (less...) in a terminal."""
    if self.interactive and os.environ.get('PAGER', 'less') and shutil.which(os.environ.get('PAGER', 'less').split()[0]):
      os.environ.setdefault("LESS", "-R")  # less shows colors
      with self.console.pager(styles=True):
        yield
    else:
      yield

  def facts(self, rows: Sequence[tuple]):
    table = Table.grid(padding=(0, 2))
    table.add_column(style='dim', justify='right')
    table.add_column()
    for label, value in rows:
      table.add_row(label, value)
    self.console.print(table)

  def panel(self, body, title: str = '', style: str = '#4263eb'):
    self.console.print(Panel(body, title=title, title_align='left', border_style=style, padding=(1, 2)))

  def show_file(self, title: str, content: str, path: str):
    lexer = Syntax.guess_lexer(path, code=content)
    self.console.print(Panel(
      Syntax(visible(content), lexer, theme='ansi_dark', background_color='default', word_wrap=False),
      title=f"[bold]{escape(title)}[/bold]", title_align='left', border_style='dim',
    ))

  def diff(self, title: str, diff: str):
    self.console.print(Panel(
      Syntax(visible(diff), 'diff', theme='ansi_dark', background_color='default', word_wrap=False),
      title=f"[bold]{escape(title)}[/bold]", title_align='left', border_style='dim',
    ))

  # --- Questions ----------------------------------------------------------
  def _auto(self, question: str, answer: str):
    self.console.print(f"[dim]? {question}[/dim] " + (f"{escape(answer)} [dim](default)[/dim]" if answer else "[dim]skipped[/dim]"))

  def ask(self, question: str, default: str = '', password: bool = False) -> str:
    if not self.interactive:
      self._auto(question, '***' if password and default else default)
      return default
    with self.paused():
      prompt = Prompt(f"[bold]?[/bold] {question}", password=password, console=self.console, show_default=not password)
      prompt.prompt_suffix = suffix(question)
      answer = prompt(default=default or None)
    return (answer or '').strip()

  def chat_input(self) -> str:
    """What the user says to the assistant. Empty without a terminal."""
    if not self.interactive:
      return ''
    with self.paused():
      try:
        return self.console.input(f"\n[bold {ACCENT}]›[/bold {ACCENT}] ").strip()
      except (EOFError, KeyboardInterrupt):
        # ends the conversation, not the wizard: the changes are still reviewed
        return ''

  def confirm(self, question: str, default: bool = True) -> bool:
    if not self.interactive:
      self._auto(question, 'yes' if default else 'no')
      return default
    with self.paused():
      prompt = Confirm(f"[bold]?[/bold] {question}", console=self.console)
      prompt.prompt_suffix = suffix(question)
      return prompt(default=default)

  def choose(self, question: str, options: List[tuple], default: str) -> str:
    """options: (key, description). Returns the key."""
    if not self.interactive:
      self._auto(question, default)
      return default
    with self.paused():
      for key, description in options:
        self.console.print(f"  [bold cyan]{key}[/bold cyan]  {description}")
      prompt = Prompt(f"[bold]?[/bold] {question}", choices=[k for k, _ in options], console=self.console)
      prompt.prompt_suffix = suffix(question)
      return prompt(default=default)

  # --- Spinners -----------------------------------------------------------
  @contextmanager
  def spinner(self, message: str) -> Iterator[None]:
    with self.console.status(message, spinner='star', spinner_style=ACCENT) as status:
      self._status = status
      try:
        yield
      finally:
        self._status = None

  def update(self, message: str):
    if self._status:
      self._status.update(message)

  def thinking(self) -> str:
    return random.choice(THINKING) + '…'

  @contextmanager
  def paused(self) -> Iterator[None]:
    """Prompts can't be shown under a spinner."""
    status = self._status
    if status:
      status.stop()
    try:
      yield
    finally:
      if status:
        status.start()


def suffix(question: str) -> str:
  """No "?:" at the end of questions."""
  return ' ' if Text.from_markup(question).plain.rstrip().endswith(('?', ')')) else ': '


def is_interactive(assume_yes: bool) -> bool:
  return not assume_yes and sys.stdin.isatty() and sys.stderr.isatty()

