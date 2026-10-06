"""
Narration: when each line is said, and its audio once there's a voice.

Each scene of a storyboard has narration lines. They're laid out one after the other (or at a given time),
and the captions follow them. Today the voice is "none": lines last as long as reading them takes, and the
video has no voice. With a TTS provider (ElevenLabs is ready), each line gets its audio file, lines last as
long as their audio, and the renderer mixes them in: captions and voice stay in sync by construction.

    voice:
      provider: elevenlabs          # or none
      voice_id: <an ElevenLabs voice id>
      model_id: eleven_multilingual_v2
    # and the API key in $ELEVENLABS_API_KEY
"""
import hashlib
import json
import os
import re
import subprocess
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional

CACHE = Path(__file__).resolve().parent.parent / '.cache' / 'voice'


@dataclass
class Line:
  text: str                      # as shown in captions (may contain <em>)
  start: float = 0.0             # seconds from the start of the scene
  end: float = 0.0
  audio: Optional[Path] = None

  @property
  def spoken(self) -> str:
    """What the voice says: no markup, and a few words read better spelled out."""
    text = re.sub(r'<[^>]+>', '', self.text)
    for written, said in (('qaboard.yaml', 'Q A Board yaml'), ('QA-Board', 'Q A Board'), ('qa ', 'Q A '), ('PSNR', 'P S N R'), ('SSIM', 'S S I M')):
      text = text.replace(written, said)
    return text

  def to_json(self) -> Dict[str, Any]:
    return {'text': self.text, 'start': round(self.start, 3), 'end': round(self.end, 3)}


@dataclass
class Timeline:
  lines: List[Line] = field(default_factory=list)

  @property
  def duration(self) -> float:
    return max((line.end for line in self.lines), default=0.0)

  def to_json(self) -> Dict[str, Any]:
    return {'lines': [line.to_json() for line in self.lines], 'duration': round(self.duration + (0.6 if self.lines else 0), 3)}


class Silent:
  """No voice: lines last as long as reading them takes."""
  words_per_second = 2.7

  def audio(self, line: Line) -> Optional[Path]:
    return None

  def duration(self, line: Line) -> float:
    words = len(line.spoken.split())
    return max(1.8, words / self.words_per_second + 0.6)


class ElevenLabs:
  """https://elevenlabs.io/docs/api-reference/text-to-speech. Audio files are cached by text and voice."""

  def __init__(self, voice_id: str, model_id: str = 'eleven_multilingual_v2', settings: Optional[Dict[str, Any]] = None):
    self.voice_id, self.model_id, self.settings = voice_id, model_id, settings or {}
    self.api_key = os.environ.get('ELEVENLABS_API_KEY')
    if not self.api_key:
      raise RuntimeError("Set ELEVENLABS_API_KEY to use ElevenLabs voices")

  def audio(self, line: Line) -> Path:
    import requests
    key = hashlib.sha1(json.dumps([line.spoken, self.voice_id, self.model_id, self.settings]).encode()).hexdigest()[:16]
    path = CACHE / f'{key}.mp3'
    if path.exists():
      return path
    response = requests.post(
      f'https://api.elevenlabs.io/v1/text-to-speech/{self.voice_id}',
      params={'output_format': 'mp3_44100_128'},
      headers={'xi-api-key': self.api_key, 'Content-Type': 'application/json'},
      json={'text': line.spoken, 'model_id': self.model_id, **({'voice_settings': self.settings} if self.settings else {})},
      timeout=120,
    )
    response.raise_for_status()
    CACHE.mkdir(parents=True, exist_ok=True)
    path.write_bytes(response.content)
    return path

  def duration(self, line: Line) -> float:
    return audio_duration(self.audio(line)) + 0.25


def audio_duration(path: Path) -> float:
  out = subprocess.run(['ffprobe', '-v', 'error', '-show_entries', 'format=duration', '-of', 'csv=p=0', str(path)],
                       capture_output=True, text=True, check=True)
  return float(out.stdout.strip())


def provider(config: Optional[Dict[str, Any]]):
  config = config or {}
  name = (os.environ.get('QA_VIDEO_VOICE') or config.get('provider') or 'none').lower()
  if name == 'none':
    return Silent()
  if name == 'elevenlabs':
    return ElevenLabs(config.get('voice_id') or os.environ['ELEVENLABS_VOICE_ID'], config.get('model_id', 'eleven_multilingual_v2'), config.get('settings'))
  raise ValueError(f"Unknown voice provider: {name}")


def plan(narration: List[Any], voice, start: float = 0.8, gap: float = 0.35, marks: Optional[Dict[str, float]] = None) -> Timeline:
  """
  Lays out a scene's narration: one line after the other, or not before a given time:
  `at: 12.5` (seconds), or `at: {mark: wizard-ai, offset: 0.5}` (a moment of the terminal recording).
  """
  timeline = Timeline()
  t = start
  for item in narration or []:
    item = {'text': item} if isinstance(item, str) else dict(item)
    line = Line(item['text'])
    at = item.get('at', 0)
    if isinstance(at, dict):
      at = (marks or {}).get(at['mark'], 0.0) + float(at.get('offset', 0))
    line.start = max(t, float(at))
    line.audio = voice.audio(line)
    line.end = line.start + float(item.get('duration') or voice.duration(line))
    timeline.lines.append(line)
    t = line.end + gap
  return timeline
