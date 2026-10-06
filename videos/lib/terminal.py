"""
Records real terminal sessions as asciicasts (https://docs.asciinema.org/manual/asciicast/v2/).

Commands really run, in bash, in a pseudo-terminal: what you see in the video is what the tools print.
Only the typing is simulated, at a human pace. A scene's steps, from the storyboard:

    - run: ls results*            # types the command, presses enter, waits for the prompt
    - type: qa wizard             # types without pressing enter
    - enter: true
    - expect: "Which API"         # waits until the output matches (a regex)
    - answer: {when: "Right\\?", text: "y"}   # waits for a question, types the answer and enter
    - pause: 1.5                  # a moment for the viewer
    - key: ctrl-c                 # or enter, escape, up, down
    - mark: wizard-ai             # a named moment: narration lines can start there (at: {mark: wizard-ai})
"""
import json
import os
import pty
import random
import re
import select
import signal
import subprocess
import threading
import time
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

KEYS = {'enter': '\r', 'ctrl-c': '\x03', 'ctrl-d': '\x04', 'escape': '\x1b', 'up': '\x1b[A', 'down': '\x1b[B', 'tab': '\t'}
ANSI = re.compile(r'\x1b\[[0-9;?]*[ -/]*[@-~]|\x1b\][^\x07]*\x07|\x1b[()][A-Z0-9]|\r')


class Session:
  def __init__(self, cwd: Path, env: Dict[str, str], cols: int = 100, rows: int = 28, ps1: str = '$ ', prompt: str = '$', seed: int = 0):
    """ps1: bash's prompt, with its escapes. prompt: how it reads on screen, to know when a command is done."""
    self.cols, self.rows = cols, rows
    self.random = random.Random(seed)
    self.events: List[Tuple[float, str]] = []
    self.screen = ''           # everything printed, without escape codes, to wait for patterns
    self.mark = 0              # waits only look at what was printed after the mark
    self.lock = threading.Lock()
    self.t0 = time.monotonic()
    self.recording = False
    self.marks: Dict[str, float] = {}   # named moments, to sync the narration
    self.prompt_re = re.compile(re.escape(prompt.rstrip()) + r'\s*$')

    master, slave = pty.openpty()
    import fcntl, struct, termios
    fcntl.ioctl(slave, termios.TIOCSWINSZ, struct.pack('HHHH', rows, cols, 0, 0))
    env = {**env, 'TERM': 'xterm-256color', 'COLORTERM': 'truecolor', 'COLUMNS': str(cols), 'LINES': str(rows), 'PS1': ps1}
    self.process = subprocess.Popen(
      ['/bin/bash', '--noprofile', '--norc', '-i'], cwd=cwd, env=env,
      stdin=slave, stdout=slave, stderr=slave, start_new_session=True, close_fds=True,
    )
    os.close(slave)
    self.master = master
    self.reader = threading.Thread(target=self._read, daemon=True)
    self.reader.start()
    self.expect_prompt(timeout=10)

  def _read(self):
    while True:
      try:
        ready, _, _ = select.select([self.master], [], [], 0.05)
        if not ready:
          if self.process.poll() is not None:
            return
          continue
        data = os.read(self.master, 65536)
      except OSError:
        return
      if not data:
        return
      text = data.decode('utf-8', errors='replace')
      with self.lock:
        if self.recording:
          self.events.append((time.monotonic() - self.t0, text))
        self.screen += ANSI.sub('', text)

  # --- Recording ----------------------------------------------------------------
  def start_recording(self):
    """What happened before (setup commands) isn't part of the video: the recording starts with a clear screen."""
    with self.lock:
      self.events, self.screen, self.mark = [], '', 0
      self.t0 = time.monotonic()
      self.recording = True
    self.send('clear\r')
    self.expect_prompt(10)
    with self.lock:
      # drop everything before the screen is cleared (the `clear` command itself)
      for i, (t, data) in enumerate(self.events):
        position = data.find('\x1b[2J')
        if position >= 0:
          start = data.rfind('\x1b[H', 0, position)
          self.events = [(0.0, data[start if start >= 0 else position:])] + [(0.0, d) for _, d in self.events[i + 1:]]
          break
      self.t0 = time.monotonic()

  def cast(self, max_idle: float = 1.2, title: str = '') -> Dict[str, Any]:
    """The asciicast, with long waits shortened (a batch running, an LLM thinking...). Marks move with them."""
    events, shift, last, shifts = [], 0.0, 0.0, [(0.0, 0.0)]
    for t, data in self.events:
      original = t
      t -= shift
      if t - last > max_idle:
        shift += t - last - max_idle
        t = last + max_idle
        shifts.append((original, shift))
      events.append([round(t, 4), data])
      last = t
    def compressed(t: float) -> float:
      applied = max((s for at, s in shifts if at <= t), default=0.0)
      return round(max(0.0, t - applied), 3)
    marks = {name: compressed(t) for name, t in self.marks.items()}
    return {'version': 2, 'width': self.cols, 'height': self.rows, 'title': title, 'events': events,
            'duration': events[-1][0] if events else 0.0, 'marks': marks}

  # --- Actions --------------------------------------------------------------------
  def send(self, text: str):
    os.write(self.master, text.encode())

  def type(self, text: str, cps: float = 16):
    """Types like a person: a varying pace, a bit slower after spaces and punctuation."""
    for char in text:
      self.send(char)
      delay = self.random.uniform(0.6, 1.4) / cps
      if char in ' -/.':
        delay *= 1.6
      time.sleep(delay)

  def expect(self, pattern: str, timeout: float = 120) -> str:
    regex = re.compile(pattern, re.MULTILINE)
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
      with self.lock:
        match = regex.search(self.screen, self.mark)
        if match:
          self.mark = match.end()
          return match.group(0)
      time.sleep(0.03)
    with self.lock:
      tail = self.screen[-800:]
    raise TimeoutError(f"Timed out waiting for {pattern!r}. Last output:\n{tail}")

  def expect_prompt(self, timeout: float = 600):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
      with self.lock:
        rest = self.screen[self.mark:]
        if self.prompt_re.search(rest):
          self.mark = len(self.screen)
          return
      time.sleep(0.05)
    with self.lock:
      tail = self.screen[-800:]
    raise TimeoutError(f"Timed out waiting for the prompt. Last output:\n{tail}")

  def run_step(self, step: Dict[str, Any]):
    if 'run' in step:
      time.sleep(step.get('before', 0.5))
      self.type(step['run'], step.get('cps', 16))
      time.sleep(0.25)
      self.send('\r')
      if step.get('wait', True):
        self.expect_prompt(step.get('timeout', 600))
      time.sleep(step.get('after', 0.6))
    elif 'type' in step:
      self.type(step['type'], step.get('cps', 16))
    elif 'enter' in step:
      self.send('\r')
    elif 'expect' in step:
      self.expect(step['expect'], step.get('timeout', 120))
    elif 'answer' in step:
      answer = step['answer']
      self.expect(answer['when'], answer.get('timeout', 120))
      time.sleep(answer.get('think', 0.7))
      self.type(str(answer.get('text', '')), answer.get('cps', 14))
      time.sleep(0.2)
      self.send('\r')
    elif 'pause' in step:
      time.sleep(step['pause'])
    elif 'key' in step:
      self.send(KEYS[step['key']])
    elif 'mark' in step:
      self.marks[step['mark']] = time.monotonic() - self.t0
    else:
      raise ValueError(f"Unknown step: {step}")

  def close(self):
    try:
      os.killpg(self.process.pid, signal.SIGTERM)
    except ProcessLookupError:
      pass
    try:
      self.process.wait(timeout=5)
    except subprocess.TimeoutExpired:
      os.killpg(self.process.pid, signal.SIGKILL)
    os.close(self.master)


def record(steps: List[Dict[str, Any]], cwd: Path, env: Dict[str, str], output: Path, setup: Optional[List[str]] = None,
           cols: int = 100, rows: int = 28, ps1: str = '$ ', prompt: str = '$', title: str = '', max_idle: float = 1.2) -> Dict[str, Any]:
  """Runs a scene's steps and saves its asciicast (one JSON file, events included)."""
  session = Session(cwd, env, cols=cols, rows=rows, ps1=ps1, prompt=prompt)
  try:
    for command in setup or []:
      session.send(command + '\r')
      session.expect_prompt(120)
    session.start_recording()
    for step in steps:
      session.run_step(step)
    time.sleep(0.8)
  finally:
    session.close()
  cast = session.cast(max_idle=max_idle, title=title)
  output.parent.mkdir(parents=True, exist_ok=True)
  output.write_text(json.dumps(cast))
  return cast
