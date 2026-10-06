"""
Terminal UI for the wizard, built with rich.
Everything goes to stderr: stdout stays clean for scripts. Without a terminal, or with --yes,
questions are answered with their defaults and the answers are printed, so logs tell what happened.
"""
import random
import sys
from contextlib import contextmanager
from typing import Any, Iterator, List, Optional, Sequence

from rich.console import Console
from rich.markup import escape
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
LOGO_COLORS = ('#5c7cfa', '#4c6ef5', '#4263eb', '#3b5bdb', '#364fc7')

GREETINGS = (
  "Let's get your results out of the terminal and into a dashboard.",
  "Two minutes from now, every commit gets its own results page.",
  "Stop comparing screenshots in Slack. Let's set things up.",
  "No more results_final_v3_REAL/. Let's set things up.",
)

THINKING = (
  "Reading the code", "Following the imports", "Looking for a main()", "Squinting at the build files",
  "Connecting the dots", "Drafting qa/main.py", "Double-checking paths", "Thinking about metrics",
)


class UI:
  def __init__(self, interactive: bool, console: Optional[Console] = None):
    self.console = console or Console(stderr=True, highlight=False)
    self.interactive = interactive
    self._status: Optional[Any] = None
    self.step_index = 0
    self.steps_total = 0

  # --- Layout -------------------------------------------------------------
  def banner(self, subtitle: str):
    logo = Text()
    for line, color in zip(LOGO.strip('\n').splitlines(), LOGO_COLORS):
      logo.append(line + '\n', style=f'bold {color}')
    logo.append(f"\n{random.choice(GREETINGS)}\n", style='italic')
    logo.append(subtitle, style='dim')
    self.console.print(Panel(logo, border_style='#4263eb', padding=(0, 2), expand=False))

  def step(self, title: str):
    self.step_index += 1
    counter = f"{self.step_index}/{self.steps_total}" if self.steps_total else str(self.step_index)
    self.console.print()
    self.console.rule(f"[bold]{counter} · {escape(title)}[/bold]", align='left', style='#4263eb')

  def ok(self, message: str):
    self.console.print(f"[green]✔[/green] {message}")

  def info(self, message: str):
    self.console.print(f"[blue]•[/blue] {message}")

  def hint(self, message: str):
    self.console.print(f"  [dim]{message}[/dim]")

  def warn(self, message: str):
    self.console.print(f"[yellow]▲[/yellow] {message}")

  def fail(self, message: str):
    self.console.print(f"[red]✘[/red] {message}")

  def facts(self, rows: Sequence[tuple]):
    table = Table.grid(padding=(0, 2))
    table.add_column(style='dim', justify='right')
    table.add_column()
    for label, value in rows:
      table.add_row(label, value)
    self.console.print(table)

  def panel(self, body, title: str = '', style: str = '#4263eb'):
    self.console.print(Panel(body, title=title, title_align='left', border_style=style, padding=(1, 2)))

  def diff(self, title: str, diff: str):
    self.console.print(Panel(
      Syntax(visible(diff), 'diff', theme='ansi_dark', background_color='default', word_wrap=True),
      title=f"[bold]{escape(title)}[/bold]", title_align='left', border_style='dim',
    ))

  # --- Questions ----------------------------------------------------------
  def _auto(self, question: str, answer: str):
    self.console.print(f"[dim]? {escape(question)}[/dim] {escape(answer)} [dim](default)[/dim]")

  def ask(self, question: str, default: str = '', password: bool = False) -> str:
    if not self.interactive:
      self._auto(question, '***' if password and default else default)
      return default
    with self.paused():
      answer = Prompt.ask(f"[bold]?[/bold] {question}", default=default or None, password=password, console=self.console, show_default=not password)
    return (answer or '').strip()

  def confirm(self, question: str, default: bool = True) -> bool:
    if not self.interactive:
      self._auto(question, 'yes' if default else 'no')
      return default
    with self.paused():
      return Confirm.ask(f"[bold]?[/bold] {question}", default=default, console=self.console)

  def choose(self, question: str, options: List[tuple], default: str) -> str:
    """options: (key, description). Returns the key."""
    if not self.interactive:
      self._auto(question, default)
      return default
    with self.paused():
      for key, description in options:
        self.console.print(f"  [bold cyan]{key}[/bold cyan]  {description}")
      return Prompt.ask(f"[bold]?[/bold] {question}", choices=[k for k, _ in options], default=default, console=self.console)

  # --- Spinners -----------------------------------------------------------
  @contextmanager
  def spinner(self, message: str) -> Iterator[None]:
    with self.console.status(message, spinner='dots') as status:
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


def is_interactive(assume_yes: bool) -> bool:
  return not assume_yes and sys.stdin.isatty() and sys.stderr.isatty()

