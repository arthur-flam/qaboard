#!/usr/bin/env python3
"""
Writes a small, deterministic test set of synthetic scenes for the denoiser example:
  OUT/clean/<category>/<scene>.png   ground truth
  OUT/noisy/<category>/<scene>.png   the same scene with sensor-like noise (stronger at night)

  python make_dataset.py data

Only needs numpy and Pillow. Everything is drawn here: no external or copyrighted images.
"""
import argparse
from pathlib import Path

import numpy as np
from PIL import Image, ImageDraw, ImageFilter, ImageFont

W, H = 400, 300
SS = 4  # supersampling: scenes are drawn at 4x then downsampled, for smooth edges


# Helpers --------------------------------------------------------------------
def rgb(*c):
  return np.array(c, dtype=np.float32) / 255


def vertical_gradient(stops, h=H * SS, w=W * SS):
  """stops: [(position in [0, 1], (r, g, b)), ...]"""
  y = np.linspace(0, 1, h, dtype=np.float32)
  positions = [p for p, _ in stops]
  channels = [np.interp(y, positions, [c[i] for _, c in stops]) for i in range(3)]
  column = np.stack(channels, axis=-1) / 255
  return np.repeat(column[:, None, :], w, axis=1)


def to_pil(arr):
  return Image.fromarray(np.round(np.clip(arr, 0, 1) * 255).astype(np.uint8))


def to_arr(img):
  return np.asarray(img.convert('RGB'), dtype=np.float32) / 255


def glow(canvas, x, y, radius, color, strength=1.0):
  """Adds a soft light (lamps, sun, neon...). Coordinates in final pixels."""
  hh, ww = canvas.shape[:2]
  yy, xx = np.mgrid[0:hh, 0:ww].astype(np.float32)
  d2 = ((xx - x * SS)**2 + (yy - y * SS)**2) / (radius * SS)**2
  canvas += strength * np.exp(-d2)[..., None] * np.array(color, dtype=np.float32) / 255
  return canvas


def font(size):
  try:
    return ImageFont.load_default(size=size * SS)
  except TypeError:  # Pillow < 10.1 or without FreeType: a tiny bitmap font
    return ImageFont.load_default()


def finish(img):
  """Downsample the 4x canvas to the final size."""
  if isinstance(img, np.ndarray):
    img = to_pil(img)
  return to_arr(img.resize((W, H), Image.BOX))


def s(*values):
  """Final pixel coordinates to canvas coordinates."""
  return [int(round(v * SS)) for v in values]


# Scenes ---------------------------------------------------------------------
def street():
  sky = vertical_gradient([(0, (28, 104, 214)), (0.55, (168, 214, 250)), (1, (168, 214, 250))])
  sky = glow(sky, 330, 45, 40, (255, 240, 190), 0.9)
  img = to_pil(sky)
  d = ImageDraw.Draw(img)
  d.ellipse(s(315, 30, 345, 60), fill=(255, 250, 225))
  # buildings
  buildings = [(0, 70, 70, (214, 108, 72)), (70, 95, 140, (46, 160, 154)), (140, 55, 205, (238, 188, 64)),
               (205, 105, 262, (232, 92, 108)), (262, 80, 330, (120, 110, 200)), (330, 120, 400, (240, 150, 60))]
  for x0, top, x1, color in buildings:
    d.rectangle(s(x0, top, x1, 175), fill=color)
    shade = tuple(int(c * 0.75) for c in color)
    d.rectangle(s(x0, top, x0 + 4, 175), fill=shade)
    for wy in range(top + 10, 165, 18):
      for wx in range(x0 + 10, x1 - 10, 16):
        lit = (wx * 7 + wy * 13) % 5 == 0
        d.rectangle(s(wx, wy, wx + 8, wy + 10), fill=(255, 226, 140) if lit else (40, 60, 92))
  # road and sidewalk
  d.rectangle(s(0, 175, 400, 300), fill=(150, 150, 146))
  d.polygon(s(150, 175, 250, 175, 400, 300, 0, 300), fill=(62, 64, 70))
  for i in range(6):
    t0, t1 = i / 6, (i + 0.5) / 6
    y0, y1 = 175 + 125 * t0**1.6, 175 + 125 * t1**1.6
    w0, w1 = 1 + 5 * t0, 1 + 5 * t1
    d.polygon(s(200 - w0, y0, 200 + w0, y0, 200 + w1, y1, 200 - w1, y1), fill=(250, 250, 240))
  # tree
  d.rectangle(s(355, 140, 363, 190), fill=(110, 70, 40))
  for cx, cy, r in [(350, 130, 22), (370, 120, 20), (362, 102, 18)]:
    d.ellipse(s(cx - r, cy - r, cx + r, cy + r), fill=(52, 150, 64))
  # red car
  d.rounded_rectangle(s(40, 228, 160, 268), radius=10 * SS, fill=(212, 30, 40))
  d.polygon(s(66, 230, 82, 206, 130, 206, 146, 230), fill=(212, 30, 40))
  d.polygon(s(74, 229, 86, 211, 104, 211, 104, 229), fill=(160, 210, 240))
  d.polygon(s(109, 229, 109, 211, 127, 211, 138, 229), fill=(160, 210, 240))
  for cx in (70, 132):
    d.ellipse(s(cx - 13, 255, cx + 13, 281), fill=(25, 25, 28))
    d.ellipse(s(cx - 6, 262, cx + 6, 274), fill=(180, 180, 185))
  d.rectangle(s(150, 238, 160, 246), fill=(255, 220, 120))
  return finish(img)


def portrait():
  bg = vertical_gradient([(0, (36, 170, 180)), (1, (120, 60, 170))])
  img = to_pil(bg)
  bokeh = Image.new('RGB', img.size, (0, 0, 0))
  d = ImageDraw.Draw(bokeh)
  rng = np.random.default_rng(7)
  for _ in range(18):
    x, y, r = rng.uniform(0, W), rng.uniform(0, H * 0.8), rng.uniform(8, 26)
    color = tuple(int(c) for c in rng.choice([(255, 200, 90), (255, 120, 170), (140, 230, 255)]))
    d.ellipse(s(x - r, y - r, x + r, y + r), fill=color)
  bokeh = bokeh.filter(ImageFilter.GaussianBlur(3 * SS))
  img = to_pil(to_arr(img) * 0.75 + to_arr(bokeh) * 0.45)
  d = ImageDraw.Draw(img)
  # shoulders, neck, hair behind the head
  d.ellipse(s(110, 230, 290, 380), fill=(238, 120, 40))
  d.polygon(s(180, 232, 220, 232, 200, 262), fill=(250, 236, 220))
  d.ellipse(s(140, 40, 260, 190), fill=(70, 40, 30))
  d.rectangle(s(184, 180, 216, 240), fill=(214, 160, 126))
  # face with a soft shading
  face = Image.new('L', img.size, 0)
  ImageDraw.Draw(face).ellipse(s(152, 62, 248, 196), fill=255)
  yy, xx = np.mgrid[0:H * SS, 0:W * SS].astype(np.float32) / SS
  light = np.clip(1.05 - 0.004 * np.hypot(xx - 182, yy - 100), 0.72, 1.05)
  skin = rgb(236, 184, 150)[None, None, :] * light[..., None]
  arr = to_arr(img)
  mask = (np.asarray(face, dtype=np.float32) / 255)[..., None]
  img = to_pil(arr * (1 - mask) + skin * mask)
  d = ImageDraw.Draw(img)
  d.chord(s(146, 50, 254, 130), 180, 360, fill=(70, 40, 30))  # fringe
  for ex in (180, 220):
    d.ellipse(s(ex - 10, 118, ex + 10, 130), fill=(250, 250, 250))
    d.ellipse(s(ex - 5, 119, ex + 5, 129), fill=(60, 110, 70))
    d.ellipse(s(ex - 2, 122, ex + 2, 126), fill=(10, 10, 10))
    d.line(s(ex - 12, 109, ex + 10, 107), fill=(60, 35, 25), width=3 * SS)
  d.line(s(200, 128, 195, 152, 203, 154), fill=(200, 140, 110), width=2 * SS)
  d.arc(s(182, 150, 218, 176), 20, 160, fill=(190, 60, 70), width=3 * SS)
  for cx in (172, 228):
    d.ellipse(s(cx - 10, 148, cx + 10, 160), fill=(240, 160, 150))
  return finish(img)


def parking():
  sky = vertical_gradient([(0, (6, 8, 26)), (0.45, (20, 24, 52)), (0.46, (14, 14, 18)), (1, (24, 24, 28))])
  img = to_pil(sky)
  d = ImageDraw.Draw(img)
  rng = np.random.default_rng(3)
  for _ in range(40):
    x, y = rng.uniform(0, W), rng.uniform(0, 90)
    d.ellipse(s(x, y, x + 1.2, y + 1.2), fill=(200, 200, 230))
  # buildings in the back
  for x0, top, x1 in [(0, 70, 90), (90, 100, 150), (230, 60, 320), (320, 90, 400)]:
    d.rectangle(s(x0, top, x1, 138), fill=(16, 16, 24))
    for wy in range(top + 8, 130, 12):
      for wx in range(x0 + 6, x1 - 6, 11):
        if (wx * 31 + wy * 17) % 7 == 0:
          d.rectangle(s(wx, wy, wx + 5, wy + 6), fill=(230, 180, 90))
  # parking lines
  for i in range(-4, 10):
    x_far = 40 + i * 40
    x_near = 200 + (x_far - 200) * 2.4
    d.line(s(x_far, 160, x_near, 300), fill=(150, 150, 140), width=2 * SS)
  d.line(s(0, 160, 400, 160), fill=(150, 150, 140), width=2 * SS)
  # cars, with red tail lights
  for x0, color in [(60, (40, 50, 70)), (250, (70, 30, 34))]:
    d.rounded_rectangle(s(x0, 180, x0 + 95, 225), radius=8 * SS, fill=color)
    d.polygon(s(x0 + 15, 182, x0 + 28, 160, x0 + 70, 160, x0 + 82, 182), fill=color)
    d.rectangle(s(x0 + 2, 192, x0 + 14, 199), fill=(255, 40, 30))
    d.rectangle(s(x0 + 81, 192, x0 + 93, 199), fill=(255, 40, 30))
    d.rectangle(s(x0 + 32, 205, x0 + 63, 213), fill=(220, 220, 200))
  arr = to_arr(img)
  for x0 in (60, 250):
    glow(arr, x0 + 8, 196, 10, (255, 40, 30), 0.5)
    glow(arr, x0 + 87, 196, 10, (255, 40, 30), 0.5)
  # street lamps: sodium orange
  img = to_pil(arr)
  d = ImageDraw.Draw(img)
  for x in (30, 200, 370):
    d.rectangle(s(x - 2, 70, x + 2, 170), fill=(40, 40, 44))
    d.rectangle(s(x - 10, 66, x + 10, 72), fill=(50, 50, 54))
  arr = to_arr(img)
  for x in (30, 200, 370):
    glow(arr, x, 74, 14, (255, 190, 90), 0.9)
    glow(arr, x, 150, 70, (255, 150, 60), 0.18)
    glow(arr, x, 74, 4, (255, 255, 230), 1.0)
  return finish(arr)


def neon():
  # a dark brick wall
  wall = np.zeros((H * SS, W * SS, 3), dtype=np.float32) + rgb(34, 18, 20)
  img = to_pil(wall)
  d = ImageDraw.Draw(img)
  for row, y in enumerate(range(0, H, 16)):
    for x in range(-(row % 2) * 16, W, 32):
      tone = 40 + ((x * 7 + y * 3) % 5) * 5
      d.rectangle(s(x + 1, y + 1, x + 31, y + 15), fill=(tone + 18, tone // 2 + 6, tone // 2 + 4))
  arr = to_arr(img) * 0.55
  # neon tubes: glow layer + bright core
  layer = Image.new('RGB', img.size, (0, 0, 0))
  d = ImageDraw.Draw(layer)
  d.text(s(200, 105), "OPEN", font=font(70), anchor='mm', fill=(255, 40, 160))
  d.text(s(200, 190), "NOODLES  24/7", font=font(30), anchor='mm', fill=(40, 220, 255))
  d.rounded_rectangle(s(70, 50, 330, 230), radius=18 * SS, outline=(255, 170, 40), width=3 * SS)
  core = to_arr(layer)
  halo = to_arr(layer.filter(ImageFilter.GaussianBlur(6 * SS)))
  arr = arr + 1.6 * halo + 0.9 * core + 0.35 * (core > 0.3)
  return finish(arr)


def fabric():
  """Woven tartan: horizontal and vertical stripes blended, fine 1-2 px lines."""
  def stripes(n, offset):
    # sett: (color, width in px), repeated
    sett = [((176, 22, 40), 6), ((250, 246, 230), 1), ((176, 22, 40), 2), ((20, 30, 80), 2), ((176, 22, 40), 1),
            ((250, 246, 230), 1), ((176, 22, 40), 2), ((20, 30, 80), 5), ((30, 110, 60), 4), ((240, 200, 40), 1),
            ((30, 110, 60), 1), ((20, 30, 80), 2), ((240, 200, 40), 1), ((20, 30, 80), 2)]
    colors = np.concatenate([np.tile(rgb(*c), (wd, 1)) for c, wd in sett])
    idx = (np.arange(n) + offset) % len(colors)
    return colors[idx]
  rows = stripes(H, 5)[:, None, :]
  cols = stripes(W, 11)[None, :, :]
  cloth = (rows + cols) / 2
  # soft folds: low-frequency shading
  yy, xx = np.mgrid[0:H, 0:W].astype(np.float32)
  folds = 0.82 + 0.18 * np.sin((xx * 0.6 + yy) / 38) * np.sin(xx / 90 + 0.5)
  return np.clip(cloth * folds[..., None] * 1.1, 0, 1)


def sign():
  sky = vertical_gradient([(0, (70, 140, 230)), (1, (190, 225, 250))])
  img = to_pil(sky)
  d = ImageDraw.Draw(img)
  for cx, cy, r in [(30, 250, 60), (110, 270, 50), (300, 265, 60), (380, 240, 55)]:
    d.ellipse(s(cx - r, cy - r, cx + r, cy + r), fill=(40, 120, 56))
  d.rectangle(s(90, 200, 98, 300), fill=(130, 130, 136))
  d.rectangle(s(302, 200, 310, 300), fill=(130, 130, 136))
  d.rounded_rectangle(s(40, 30, 360, 215), radius=14 * SS, fill=(0, 110, 70))
  d.rounded_rectangle(s(48, 38, 352, 207), radius=10 * SS, outline=(250, 250, 250), width=3 * SS)
  d.rectangle(s(268, 46, 344, 74), fill=(250, 200, 30))
  d.text(s(306, 60), "EXIT 42", font=font(15), anchor='mm', fill=(20, 20, 20))
  d.text(s(64, 90), "Riverside", font=font(30), anchor='lm', fill=(255, 255, 255))
  d.text(s(336, 90), "12 km", font=font(26), anchor='rm', fill=(255, 255, 255))
  d.text(s(64, 132), "Old Town", font=font(30), anchor='lm', fill=(255, 255, 255))
  d.text(s(336, 132), "5 km", font=font(26), anchor='rm', fill=(255, 255, 255))
  d.text(s(64, 176), "Harbor  Airport  Stadium", font=font(18), anchor='lm', fill=(255, 255, 255))
  d.polygon(s(318, 168, 338, 182, 318, 196), fill=(255, 255, 255))
  d.rectangle(s(300, 177, 318, 187), fill=(255, 255, 255))
  return finish(img)


def sunset():
  sky = vertical_gradient([(0, (40, 20, 90)), (0.3, (150, 40, 120)), (0.55, (250, 110, 70)), (0.7, (255, 200, 90)), (1, (255, 200, 90))])
  sky = glow(sky, 200, 205, 60, (255, 210, 120), 0.6)
  img = to_pil(sky)
  d = ImageDraw.Draw(img)
  d.ellipse(s(160, 165, 240, 245), fill=(255, 236, 170))
  # hills, further ones are lighter
  xs = np.linspace(0, W, 80)
  for base, amp, freq, phase, color in [(200, 18, 0.018, 0.0, (170, 70, 120)), (225, 22, 0.012, 2.0, (110, 40, 100)),
                                        (255, 20, 0.022, 4.0, (60, 22, 64))]:
    ys = base + amp * np.sin(xs * freq + phase) + amp * 0.4 * np.sin(xs * freq * 3.1 + phase)
    d.polygon(s(*[v for p in zip(xs, ys) for v in p], W, H, 0, H), fill=color)
  # a few birds
  for bx, by in [(90, 70), (110, 62), (128, 74)]:
    d.line(s(bx - 6, by - 3, bx, by, bx + 6, by - 3), fill=(40, 20, 50), width=SS)
  return finish(img)


# Noise ----------------------------------------------------------------------
def add_noise(clean, rng, read, photons):
  """Sensor-like noise: shot noise (variance proportional to the signal) + read noise, per channel."""
  shot = rng.standard_normal(clean.shape).astype(np.float32) * np.sqrt(clean / photons)
  read_noise = rng.standard_normal(clean.shape).astype(np.float32) * read
  return np.clip(clean + shot + read_noise, 0, 1)


# name: (painter, read noise, photons at full scale)
SCENES = {
  'day/street': (street, 0.03, 400),
  'day/portrait': (portrait, 0.03, 400),
  'night/parking': (parking, 0.05, 12),
  'night/neon': (neon, 0.05, 12),
  'texture/fabric': (fabric, 0.035, 300),
  'text/sign': (sign, 0.035, 300),
  'sky/sunset': (sunset, 0.03, 400),
}


def save(path: Path, arr):
  path.parent.mkdir(parents=True, exist_ok=True)
  to_pil(arr).save(path, optimize=False, compress_level=6)


def make_dataset(out: Path, verbose=True):
  for i, (name, (painter, read, photons)) in enumerate(SCENES.items()):
    clean = np.round(painter() * 255) / 255  # what we'll save, so the noise is added to the exact ground truth
    noisy = add_noise(clean, np.random.default_rng(1000 + i), read, photons)
    save(out / 'clean' / f'{name}.png', clean)
    save(out / 'noisy' / f'{name}.png', noisy)
    if verbose:
      print(f"{out / 'noisy' / name}.png")


if __name__ == '__main__':
  parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
  parser.add_argument('out', type=Path, nargs='?', default=Path('data'), help="output folder (default: data)")
  make_dataset(parser.parse_args().out)
