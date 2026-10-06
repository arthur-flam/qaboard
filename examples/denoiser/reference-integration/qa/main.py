"""
QA-Board integration of the denoiser.
`qa run --input day/street.png` denoises data/noisy/day/street.png, saves images in the output directory,
and returns PSNR/SSIM against data/clean/day/street.png.
"""
import shutil
import sys
import time
from pathlib import Path

import numpy as np
import typer
from PIL import Image, ImageDraw, ImageFont

# True in GitLab CI or Jenkins, if you need to behave differently there.
from qaboard.config import is_ci  # noqa: F401

# The project's code lives at the root of the repository
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from denoiser import DEFAULT_METHOD, denoise  # noqa: E402
from denoiser.io import find_ground_truth, load_image, save_image  # noqa: E402
from denoiser.metrics import psnr, ssim  # noqa: E402


def run(context):
  """
  Denoises context.input_path, saves images under context.output_dir, and returns metrics.

  Parameters come from context.params: configs in qa/batches.yaml, or `qa --tuning '{"strength": 1.5}' run ...`
    method:   gaussian | bilateral (default: the project's default)
    strength: more is smoother (default: 1.0)
  """
  method = context.params.get('method', DEFAULT_METHOD)
  strength = float(context.params.get('strength', 1.0))
  typer.secho(f"Denoising {context.rel_input_path} with {method} (strength {strength:g})", fg='blue')
  if context.dryrun:
    typer.secho(f"  would save output.png, error.png and zoom.png in {context.output_dir}", fg='blue')
    return {"is_failed": False}

  try:
    noisy = load_image(context.input_path)
  except OSError as e:
    typer.secho(f"Could not read {context.input_path}: {e}", fg='red', err=True)
    return {"is_failed": True}
  start = time.perf_counter()
  try:
    output = denoise(noisy, method=method, strength=strength)
  except ValueError as e:  # e.g. an unknown method
    typer.secho(str(e), fg='red', err=True)
    return {"is_failed": True}
  runtime = time.perf_counter() - start

  # Visualizations (qaboard.yaml: outputs.visualizations) compare these files between runs
  save_image(context.output_dir / 'output.png', output)
  shutil.copyfile(context.input_path, context.output_dir / 'input.png')
  metrics = {"is_failed": False, "runtime_ms": 1000 * runtime}

  gt_path = find_ground_truth(context.input_path)
  if not gt_path:
    typer.secho("No ground truth in data/clean/ for this input: no PSNR/SSIM", fg='yellow', err=True)
    return metrics
  gt = load_image(gt_path)
  shutil.copyfile(gt_path, context.output_dir / 'ground_truth.png')
  save_image(context.output_dir / 'error.png', error_heatmap(output, gt))
  zoom(noisy, output, gt).save(context.output_dir / 'zoom.png')

  metrics.update({
    "psnr": psnr(output, gt),
    "ssim": ssim(output, gt),
    "psnr_gain": psnr(output, gt) - psnr(noisy, gt),
  })
  print(f"PSNR {metrics['psnr']:.2f} dB ({metrics['psnr_gain']:+.2f} dB)  SSIM {metrics['ssim']:.3f}  {metrics['runtime_ms']:.0f} ms")
  return metrics


# A perceptually ordered colormap (black, purple, red, orange, yellow), like matplotlib's "inferno"
INFERNO = np.array([(0, 0, 4), (40, 11, 84), (101, 21, 110), (159, 42, 99), (212, 72, 66), (245, 125, 21), (250, 193, 39), (252, 255, 164)], dtype=np.float32) / 255


def error_heatmap(output: np.ndarray, gt: np.ndarray, max_error: float = 0.1) -> np.ndarray:
  """Absolute error vs the ground truth. The scale is fixed, so that heatmaps compare between runs."""
  error = np.abs(output - gt).mean(axis=-1)
  t = np.clip(error / max_error, 0, 1) * (len(INFERNO) - 1)
  low = np.floor(t).astype(int).clip(max=len(INFERNO) - 2)
  frac = (t - low)[..., None]
  return INFERNO[low] * (1 - frac) + INFERNO[low + 1] * frac


def zoom(noisy: np.ndarray, output: np.ndarray, gt: np.ndarray, size=(80, 60), scale=4) -> Image.Image:
  """Noisy | output | ground truth, magnified on the most detailed part of the image."""
  w, h = size
  detail = np.abs(np.diff(gt.mean(axis=-1), axis=0))[:, :-1] + np.abs(np.diff(gt.mean(axis=-1), axis=1))[:-1, :]
  # most detailed crop on a coarse grid, so that it is the same for every run
  best, x0, y0 = -1.0, 0, 0
  for y in range(0, detail.shape[0] - h, h // 2):
    for x in range(0, detail.shape[1] - w, w // 2):
      score = float(detail[y:y + h, x:x + w].sum())
      if score > best:
        best, x0, y0 = score, x, y
  gap, label_height = 6, 22
  sheet = Image.new('RGB', (3 * w * scale + 2 * gap, h * scale + label_height), (24, 24, 28))
  draw = ImageDraw.Draw(sheet)
  try:
    font = ImageFont.load_default(size=15)
  except TypeError:  # older Pillow
    font = ImageFont.load_default()
  for i, (label, img) in enumerate([("noisy", noisy), ("output", output), ("ground truth", gt)]):
    crop = Image.fromarray(np.round(img[y0:y0 + h, x0:x0 + w] * 255).astype(np.uint8))
    sheet.paste(crop.resize((w * scale, h * scale), Image.NEAREST), (i * (w * scale + gap), label_height))
    draw.text((i * (w * scale + gap) + 6, 4), label, fill=(235, 235, 235), font=font)
  return sheet
