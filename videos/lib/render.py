"""
Renders scenes to video clips with Playwright, on the stage page (lib/stage), then joins them with ffmpeg.

- title, chat and terminal scenes play by themselves (terminal scenes replay a recorded asciicast).
- browser scenes show the real QA-Board web app in a browser window, driven by a list of actions:

    actions:
      - goto: {url: "http://localhost:5151/demo/project", display: "qaboard.acme.internal/demo/project"}
      - wait: "table"                     # a CSS selector in the app
      - click: "text=night/parking"       # moves the cursor there, clicks for real
      - move: ".bp6-card"                 # just moves the cursor
      - scroll: {to: ".metrics"}          # or {by: 600}
      - focus: {on: "img", scale: 1.8}    # zooms the camera on a part of the window
      - unfocus: true
      - pause: 1.5
      - until: 12                         # waits until the scene's clock reaches 12 s (narration)
"""
import glob
import json
import shutil
import subprocess
import tempfile
import time
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from voice import Timeline

STAGE = Path(__file__).resolve().parent / 'stage' / 'stage.html'
WIDTH, HEIGHT, FPS = 1920, 1080, 30


def chromium_path() -> Optional[str]:
  """The Chromium preinstalled for Playwright, if any (no download needed)."""
  found = sorted(glob.glob('/opt/pw-browsers/chromium-*/chrome-linux*/chrome'))
  return found[-1] if found else None


class Renderer:
  def __init__(self, work: Path):
    from playwright.sync_api import sync_playwright
    self.work = work
    self.work.mkdir(parents=True, exist_ok=True)
    self.playwright = sync_playwright().start()
    self.browser = self.playwright.chromium.launch(
      executable_path=chromium_path(),
      # the stage is a local file that embeds the app (localhost) in an iframe
      args=['--allow-file-access-from-files', '--disable-web-security', '--autoplay-policy=no-user-gesture-required',
            '--font-render-hinting=none', '--force-color-profile=srgb'],
    )

  def close(self):
    self.browser.close()
    self.playwright.stop()

  def scene(self, name: str, scene: Dict[str, Any], timeline: Timeline) -> Tuple[Path, float]:
    """Records one scene, returns its clip and duration."""
    videos = Path(tempfile.mkdtemp(dir=self.work))
    context = self.browser.new_context(
      viewport={'width': WIDTH, 'height': HEIGHT}, device_scale_factor=1,
      record_video_dir=str(videos), record_video_size={'width': WIDTH, 'height': HEIGHT},
    )
    page = context.new_page()
    page.set_default_timeout(60_000)
    video_start = time.monotonic()
    page.goto(STAGE.as_uri())
    page.evaluate("([scene, timeline]) => stage.load(scene, timeline)", [scene, timeline.to_json()])
    if scene['type'] == 'browser':
      frame = page.frame(name='app')
      if frame and scene.get('url'):
        frame.wait_for_load_state('networkidle')
    page.wait_for_timeout(400)
    start = time.monotonic() - video_start
    page.evaluate("stage.start()")
    if scene['type'] == 'browser':
      Driver(page).run(scene.get('actions', []))
      page.evaluate(f"stage.until({timeline.to_json()['duration']})")
      page.wait_for_timeout(int(scene.get('hold', 1.0) * 1000))
    else:
      page.evaluate("stage.run()")
    end = time.monotonic() - video_start
    video = page.video
    context.close()
    clip = self.work / f'{name}.mp4'
    encode(Path(video.path()), clip, start, end)
    shutil.rmtree(videos, ignore_errors=True)
    return clip, end - start


class Driver:
  """Plays a browser scene's actions on the app, in the stage's iframe."""

  def __init__(self, page):
    self.page = page
    self.frame = page.frame(name='app')

  def locator(self, selector: str):
    return self.frame.locator(selector).first

  def center(self, selector: str) -> Tuple[float, float]:
    locator = self.locator(selector)
    locator.wait_for(state='visible')
    box = locator.bounding_box()
    return box['x'] + box['width'] / 2, box['y'] + box['height'] / 2

  def run(self, actions: List[Dict[str, Any]]):
    for action in actions:
      (kind, value), = [(k, v) for k, v in action.items() if k not in ('ms', 'scale', 'display')]
      getattr(self, f'do_{kind}')(value, action)

  def do_goto(self, value, action):
    url = value['url'] if isinstance(value, dict) else value
    display = value.get('display') if isinstance(value, dict) else None
    self.page.evaluate("url => stage.setUrl(url)", display or url)
    self.frame.goto(url)
    self.frame.wait_for_load_state('networkidle')

  def do_wait(self, selector, action):
    self.locator(selector).wait_for(state='visible')

  def do_move(self, selector, action):
    x, y = self.center(selector)
    self.page.evaluate("([x, y, ms]) => stage.cursorTo(x, y, ms)", [x, y, action.get('ms', 900)])

  def do_click(self, selector, action):
    x, y = self.center(selector)
    self.page.evaluate("([x, y, ms]) => stage.cursorTo(x, y, ms)", [x, y, action.get('ms', 900)])
    self.page.evaluate("([x, y]) => stage.ripple(x, y)", [x, y])
    self.locator(selector).click()
    self.frame.wait_for_load_state('networkidle')

  def do_hover(self, selector, action):
    self.do_move(selector, action)
    self.locator(selector).hover()

  def do_scroll(self, value, action):
    if 'to' in value:
      self.locator(value['to']).evaluate("node => node.scrollIntoView({behavior: 'smooth', block: 'center'})")
    else:
      self.frame.evaluate("by => window.scrollBy({top: by, behavior: 'smooth'})", value['by'])
    self.page.wait_for_timeout(int(value.get('ms', 1200)))

  def do_focus(self, value, action):
    if isinstance(value, dict) and 'x' in value:
      x, y = value['x'], value['y']
    else:
      x, y = self.center(value['on'] if isinstance(value, dict) else value)
    scale = (value.get('scale') if isinstance(value, dict) else None) or action.get('scale', 1.6)
    self.page.evaluate("([x, y, scale, ms]) => stage.focus(x, y, scale, ms)", [x, y, scale, action.get('ms', 1100)])

  def do_unfocus(self, value, action):
    self.page.evaluate("ms => stage.unfocus(ms)", action.get('ms', 900))

  def do_hide_cursor(self, value, action):
    self.page.evaluate("stage.hideCursor()")

  def do_type(self, value, action):
    self.locator(value['into']).type(value['text'], delay=70)

  def do_key(self, value, action):
    self.page.keyboard.press(value)

  def do_pause(self, value, action):
    self.page.wait_for_timeout(int(float(value) * 1000))

  def do_until(self, value, action):
    self.page.evaluate(f"stage.until({float(value)})")


def encode(webm: Path, mp4: Path, start: float, end: float):
  subprocess.run([
    'ffmpeg', '-y', '-v', 'error', '-ss', f'{start:.3f}', '-to', f'{end:.3f}', '-i', str(webm),
    '-r', str(FPS), '-c:v', 'libx264', '-preset', 'medium', '-crf', '16', '-pix_fmt', 'yuv420p', str(mp4),
  ], check=True)


def duration(path: Path) -> float:
  out = subprocess.run(['ffprobe', '-v', 'error', '-show_entries', 'format=duration', '-of', 'csv=p=0', str(path)],
                       capture_output=True, text=True, check=True)
  return float(out.stdout.strip())


def compose(clips: List[Tuple[Path, Timeline]], output: Path, fade: float = 0.5, music: Optional[Path] = None):
  """
  Joins the clips with crossfades, mixes the narration (when there is a voice) and writes captions next to the video
  (.srt). Scene i starts when scene i-1 ends, minus the crossfade.
  """
  durations = [duration(path) for path, _ in clips]
  starts, t = [], 0.0
  for d in durations:
    starts.append(t)
    t += d - fade
  total = t + fade

  inputs: List[str] = []
  for path, _ in clips:
    inputs += ['-i', str(path)]
  filters, last = [], '[0:v]'
  for i in range(1, len(clips)):
    label = f'[v{i}]'
    filters.append(f"{last}[{i}:v]xfade=transition=fade:duration={fade}:offset={starts[i]:.3f}{label}")
    last = label
  video_label = last

  # Narration: each line's audio at its time in the whole video
  lines = [(starts[i] + line.start, starts[i] + line.end, line) for i, (_, timeline) in enumerate(clips) for line in timeline.lines]
  audio_inputs = [(start, line.audio) for start, _, line in lines if line.audio]
  if music:
    audio_inputs.append((0.0, music))
  audio_label = None
  if audio_inputs:
    labels = []
    for n, (start, path) in enumerate(audio_inputs):
      index = len(clips) + n
      inputs += ['-i', str(path)]
      delay = int(start * 1000)
      volume = 0.12 if music and path == music else 1.0
      filters.append(f"[{index}:a]adelay={delay}|{delay},volume={volume}[a{n}]")
      labels.append(f'[a{n}]')
    filters.append(f"{''.join(labels)}amix=inputs={len(labels)}:normalize=0:duration=longest[aout]")
    audio_label = '[aout]'
  else:
    inputs += ['-f', 'lavfi', '-t', f'{total:.3f}', '-i', 'anullsrc=channel_layout=stereo:sample_rate=48000']
    audio_label = f'{len(clips)}:a'

  output.parent.mkdir(parents=True, exist_ok=True)
  command = ['ffmpeg', '-y', '-v', 'error', *inputs]
  if filters:
    command += ['-filter_complex', ';'.join(filters)]
  command += ['-map', video_label if filters else '0:v', '-map', audio_label,
              '-t', f'{total:.3f}', '-c:v', 'libx264', '-preset', 'slow', '-crf', '18', '-pix_fmt', 'yuv420p',
              '-c:a', 'aac', '-b:a', '192k', '-movflags', '+faststart', str(output)]
  subprocess.run(command, check=True)
  write_srt([(s, e, line.text) for s, e, line in lines], output.with_suffix('.srt'))
  return total


def write_srt(entries: List[Tuple[float, float, str]], path: Path):
  import re

  def stamp(t: float) -> str:
    ms = int(round(t * 1000))
    return f"{ms // 3_600_000:02d}:{ms // 60_000 % 60:02d}:{ms // 1000 % 60:02d},{ms % 1000:03d}"
  blocks = [f"{i}\n{stamp(s)} --> {stamp(e)}\n{re.sub(r'<[^>]+>', '', text)}\n" for i, (s, e, text) in enumerate(entries, start=1)]
  path.write_text('\n'.join(blocks))
