# /// script
# requires-python = ">=3.10"
# dependencies = ["playwright", "pyyaml", "requests"]
# ///
"""
Makes a QA-Board video from its storyboard.

    uv run videos/make.py videos/01-getting-started            # record the terminal scenes, then render
    uv run videos/make.py videos/01-getting-started record     # only (re-)record the terminal scenes
    uv run videos/make.py videos/01-getting-started render     # only render, from the recordings
    uv run videos/make.py videos/01-getting-started render --scenes outro,batch   # a preview of some scenes
    uv run videos/make.py videos/01-getting-started script     # the narration, in narration.md

Requirements: ffmpeg, Chromium (the one Playwright uses), `npm install` in videos/, and for scenes that show the
web app, a running QA-Board (videos/stack). See videos/README.md.
"""
import argparse
import json
import os
import re
import shutil
import subprocess
import sys
from pathlib import Path
from typing import Any, Dict, List

import yaml

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE / 'lib'))

from voice import plan, provider  # noqa: E402


class Chapter:
  def __init__(self, folder: Path):
    self.folder = folder.resolve()
    self.board = yaml.safe_load((self.folder / 'storyboard.yaml').read_text())
    self.cache = HERE / '.cache' / self.folder.name
    self.casts = self.cache / 'casts'
    self.state_file = self.cache / 'state.json'
    self.state: Dict[str, str] = json.loads(self.state_file.read_text()) if self.state_file.exists() else {}

  # --- Variables: {name} in the storyboard -------------------------------------------
  def variables(self) -> Dict[str, str]:
    base = {
      'repo': str(Path(__file__).resolve().parent.parent),
      'videos': str(HERE),
      'chapter': str(self.folder),
      'cache': str(self.cache),
      'PATH': os.environ.get('PATH', ''),
      'HOME': os.environ.get('HOME', ''),
    }
    for key, value in (self.board.get('vars') or {}).items():
      base[key] = self.expand(str(value), base)
    return {**base, **self.state}

  def expand(self, value: Any, variables: Dict[str, str] = None) -> Any:
    variables = variables if variables is not None else self.variables()
    if isinstance(value, str):
      return re.sub(r'\{(\w+)\}', lambda m: variables.get(m.group(1), m.group(0)), value)
    if isinstance(value, list):
      return [self.expand(v, variables) for v in value]
    if isinstance(value, dict):
      return {k: self.expand(v, variables) for k, v in value.items()}
    return value

  def shell(self, commands: List[str], cwd: Path, env: Dict[str, str]):
    """Runs setup commands (not recorded). Lines printed as KEY=value are saved for later scenes."""
    for command in commands:
      out = subprocess.run(['bash', '-c', command], cwd=cwd, env=env, capture_output=True, text=True)
      if out.returncode:
        raise RuntimeError(f"Failed: {command}\n{out.stdout}\n{out.stderr}")
      for line in out.stdout.splitlines():
        match = re.match(r'^@(\w+)=(.*)$', line)
        if match:
          self.state[match.group(1)] = match.group(2)
    self.cache.mkdir(parents=True, exist_ok=True)
    self.state_file.write_text(json.dumps(self.state, indent=2))

  def environment(self) -> Dict[str, str]:
    env = {k: v for k, v in os.environ.items() if not k.lower().endswith('_proxy')}
    env.update({k: str(v) for k, v in self.expand(self.board.get('env') or {}).items()})
    return env

  # --- Recording ------------------------------------------------------------------
  def record(self, only: List[str]):
    from terminal import record
    from fakellm import FakeLLM, load_conversation, self_signed_cert
    from forward import Forward
    terminal = self.board.get('terminal') or {}
    if not only:
      self.state = {}
    services = []
    try:
      # The scripted AI assistant, and the forwarded ports: some settings depend on them
      if self.board.get('llm'):
        config = self.expand(self.board['llm'])
        cert = key = None
        if config.get('tls_hostname'):
          cert, key = self_signed_cert(config['tls_hostname'], self.cache / 'certs')
          self.state['llm_cert'] = str(cert)
        llm = FakeLLM(load_conversation(self.folder / config['conversation']), host=config.get('listen', '127.0.0.1'),
                      port=int(config.get('port', 0)), certfile=cert, keyfile=key)
        self.state['llm_port'] = str(llm.port)
        services.append(llm)
      for forward in self.expand(self.board.get('forward') or []):
        services.append(Forward(int(forward['from']), int(forward['to'])))

      env = self.environment()
      work = Path(self.expand(self.board.get('workdir', '{cache}/work')))
      work.mkdir(parents=True, exist_ok=True)
      if not only:
        self.shell(self.expand(self.board.get('prepare') or []), work, env)
      for scene in self.board['scenes']:
        if scene['type'] != 'terminal' or (only and scene['id'] not in only):
          continue
        scene = self.expand(scene)
        print(f"● recording {scene['id']}", flush=True)
        cwd = Path(self.expand(scene.get('cwd', str(work))))
        scene_env = {**env, **{k: str(v) for k, v in self.expand(scene.get('env') or {}).items()}}
        record(
          scene['steps'], cwd, scene_env, self.casts / f"{scene['id']}.json",
          setup=scene.get('before'), cols=terminal.get('cols', 100), rows=terminal.get('rows', 26),
          ps1=terminal.get('ps1', '$ '), prompt=terminal.get('prompt', '$'), title=scene.get('title', ''),
          max_idle=float(scene.get('max_idle', terminal.get('max_idle', 1.2))),
        )
        if scene.get('after'):
          self.shell(scene['after'], cwd, scene_env)
    finally:
      for service in services:
        service.close()
    self.state_file.write_text(json.dumps(self.state, indent=2))

  # --- Rendering ------------------------------------------------------------------
  def render(self, only: List[str], force: bool = False):
    """
    Renders the scenes, and joins them. With `only`, renders and joins only those scenes, into a preview.
    Unchanged scenes are reused (`force` renders everything: browser scenes show live data that may have changed).
    """
    from render import Renderer, compose
    voice = provider(self.board.get('voice'))
    renderer = Renderer(self.cache / 'clips')
    clips = []
    try:
      for scene in self.board['scenes']:
        if only and scene['id'] not in only:
          continue
        scene = self.expand(scene)
        scene.setdefault('chapter', self.expand(self.board.get('chapter', '')))
        marks = {}
        if scene['type'] == 'terminal':
          cast_file = self.casts / f"{scene['id']}.json"
          if not cast_file.exists():
            raise SystemExit(f"No recording for {scene['id']}: run `make.py {self.folder} record` first")
          scene['cast'] = json.loads(cast_file.read_text())
          marks = scene['cast'].get('marks', {})
        timeline = plan(scene.get('narration', []), voice, marks=marks)
        local_storage = (self.board.get('browser') or {}).get('local_storage')
        # A clip is rendered again only when what it shows changed (the scene, its recording, narration, the stage)
        key = fingerprint([scene, timeline.to_json(), local_storage])
        clip = renderer.work / f"{scene['id']}.mp4"
        key_file = clip.with_suffix('.key')
        if not force and clip.exists() and key_file.exists() and key_file.read_text() == key:
          print(f"● {scene['id']}: unchanged", flush=True)
        else:
          print(f"● rendering {scene['id']}", flush=True)
          clip, _ = renderer.scene(scene['id'], scene, timeline, local_storage=local_storage)
          key_file.write_text(key)
        clips.append((clip, timeline))
    finally:
      renderer.close()
    output = self.folder / self.board.get('output', 'out/video.mp4')
    if only:
      output = output.with_name(output.stem + '-preview.mp4')
    total = compose(clips, output, fade=float(self.board.get('crossfade', 0.5)))
    print(f"✔ {output} ({total:.0f} s), captions in {output.with_suffix('.srt')}")


def fingerprint(data: Any) -> str:
  """Identifies what a clip shows: its data, and the stage and renderer code."""
  import hashlib
  digest = hashlib.sha256(json.dumps(data, sort_keys=True, default=str).encode())
  for path in sorted((HERE / 'lib' / 'stage').iterdir()) + [HERE / 'lib' / 'render.py']:
    digest.update(path.read_bytes())
  return digest.hexdigest()


def write_script(chapter: 'Chapter') -> Path:
  """The narration, scene by scene, to review it (or hand it to a voice actor) before rendering."""
  board = chapter.board
  lines = [f"# {board.get('title') or chapter.folder.name}", '']
  for scene in board['scenes']:
    lines.append(f"## {scene['id']} ({scene['type']})")
    what = scene.get('title') if scene['type'] == 'title' else None
    if what:
      lines.append(f"*On screen: {re.sub(r'<[^>]+>', '', str(what))}*")
    for item in scene.get('narration') or []:
      text = item if isinstance(item, str) else item['text']
      lines.append(f"- {re.sub(r'<[^>]+>', '', text)}")
    lines.append('')
  words = sum(len(re.sub(r'<[^>]+>', '', i if isinstance(i, str) else i['text']).split())
              for scene in board['scenes'] for i in scene.get('narration') or [])
  lines.append(f"*{words} words, about {words / 150:.1f} minutes of voice at 150 words per minute.*")
  output = chapter.folder / 'narration.md'
  output.write_text('\n'.join(lines) + '\n')
  return output


def main():
  parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
  parser.add_argument('chapter', type=Path)
  parser.add_argument('step', nargs='?', choices=('all', 'record', 'render', 'script'), default='all',
                      help="script: writes the narration to narration.md, to review it")
  parser.add_argument('--scenes', default='', help="Only these scenes (comma-separated ids)")
  parser.add_argument('--force', action='store_true', help="Render every scene again, even unchanged ones")
  args = parser.parse_args()
  if not (HERE / 'node_modules' / '@xterm').exists():
    subprocess.run(['npm', 'install', '--no-audit', '--no-fund', '--loglevel=error'], cwd=HERE, check=True)
  chapter = Chapter(args.chapter)
  if args.step == 'script':
    print(write_script(chapter))
    return
  only = [s for s in args.scenes.split(',') if s]
  if args.step in ('all', 'record'):
    chapter.record(only)
  if args.step in ('all', 'render'):
    chapter.render(only, force=args.force or args.step == 'all')


if __name__ == '__main__':
  main()
