"""
Toy "denoiser" wrapped with QA-Board, for demos: pure python, no dependencies.

For each input (scenes/*.json), it renders a synthetic image, adds noise, denoises it,
and saves clean.png / noisy.png / output.png with PSNR metrics.
"""
import json
import math
import random
import struct
import time
import zlib

# `stack.sh seed` changes this in a second commit, to have something to compare
FILTER = "box"
RADIUS = 1

WIDTH, HEIGHT = 480, 320


def run(context):
  scene = json.loads(context.input_path.read_text())
  params = {"filter": FILTER, "radius": RADIUS, **context.params}
  clean = render(scene["kind"], scene.get("seed", 0))
  noisy = add_noise(clean, scene.get("noise", 0.2), scene.get("seed", 0))
  start = time.time()
  denoised = denoise(noisy, params["filter"], int(params["radius"]))
  runtime = time.time() - start
  for name, img in [("clean", clean), ("noisy", noisy), ("output", denoised)]:
    write_png(context.output_dir / f"{name}.png", img)
  psnr_noisy, psnr_out = psnr(noisy, clean), psnr(denoised, clean)
  print(f"{scene['kind']}: {params['filter']} r={params['radius']} PSNR {psnr_noisy:.2f} -> {psnr_out:.2f} dB")
  return {
    "is_failed": False,
    "psnr": round(psnr_out, 3),
    "psnr_gain": round(psnr_out - psnr_noisy, 3),
    "runtime": round(runtime, 3),
  }


# Images are lists of rows of (r, g, b) floats in [0, 1]
def render(kind, seed):
  rng = random.Random(seed)
  blobs = [(rng.uniform(0, WIDTH), rng.uniform(0, HEIGHT), rng.uniform(30, 90), rng.random(), rng.random(), rng.random()) for _ in range(7)]
  img = []
  for y in range(HEIGHT):
    row = []
    for x in range(WIDTH):
      u, v = x / WIDTH, y / HEIGHT
      if kind == "rings":
        d = math.hypot(x - WIDTH / 2, y - HEIGHT / 2)
        s = 0.5 + 0.5 * math.cos(d / 9)
        px = (0.15 + 0.7 * s * u, 0.2 + 0.5 * s, 0.6 + 0.4 * s * (1 - u))
      elif kind == "stripes":
        s = 1.0 if math.sin((x + 0.6 * y) / 14) > 0 else 0.0
        px = (0.9 * s + 0.1 * v, 0.3 + 0.4 * u, 0.8 - 0.6 * s * v)
      elif kind == "checker":
        s = ((x // 40) + (y // 40)) % 2
        px = (0.1 + 0.8 * s, 0.15 + 0.6 * s * u, 0.25 + 0.5 * (1 - s) * v)
      else:  # blobs
        r, g, b = 0.05 + 0.2 * v, 0.08, 0.15 + 0.2 * u
        for bx, by, br, cr, cg, cb in blobs:
          w = math.exp(-((x - bx) ** 2 + (y - by) ** 2) / (2 * br * br))
          r, g, b = r + w * cr, g + w * cg, b + w * cb
        px = (r, g, b)
      row.append(tuple(min(1.0, max(0.0, c)) for c in px))
    img.append(row)
  return img


def add_noise(img, sigma, seed):
  rng = random.Random(1000 + seed)
  return [[tuple(min(1.0, max(0.0, c + rng.gauss(0, sigma))) for c in px) for px in row] for row in img]


def denoise(img, filter, radius):
  if filter == "median":
    return median(img, radius)
  return box_blur(img, radius)


def box_blur(img, radius):
  def blur_rows(rows):
    out = []
    for row in rows:
      n = len(row)
      new_row = []
      for x in range(n):
        lo, hi = max(0, x - radius), min(n, x + radius + 1)
        k = hi - lo
        new_row.append(tuple(sum(row[i][c] for i in range(lo, hi)) / k for c in range(3)))
      out.append(new_row)
    return out
  transposed = [list(col) for col in zip(*blur_rows(img))]
  return [list(col) for col in zip(*blur_rows(transposed))]


def median(img, radius):
  h, w = len(img), len(img[0])
  out = []
  for y in range(h):
    ys = range(max(0, y - radius), min(h, y + radius + 1))
    row = []
    for x in range(w):
      xs = range(max(0, x - radius), min(w, x + radius + 1))
      window = [img[j][i] for j in ys for i in xs]
      mid = len(window) // 2
      row.append(tuple(sorted(p[c] for p in window)[mid] for c in range(3)))
    out.append(row)
  return out


def psnr(a, b):
  se = sum((pa[c] - pb[c]) ** 2 for ra, rb in zip(a, b) for pa, pb in zip(ra, rb) for c in range(3))
  mse = se / (len(a) * len(a[0]) * 3)
  return 10 * math.log10(1 / mse) if mse else 100.0


def write_png(path, img):
  raw = b"".join(b"\x00" + bytes(int(c * 255 + 0.5) for px in row for c in px) for row in img)
  def chunk(tag, data):
    return struct.pack(">I", len(data)) + tag + data + struct.pack(">I", zlib.crc32(tag + data) & 0xffffffff)
  header = struct.pack(">IIBBBBB", len(img[0]), len(img), 8, 2, 0, 0, 0)
  path.write_bytes(b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", header) + chunk(b"IDAT", zlib.compress(raw, 6)) + chunk(b"IEND", b""))
