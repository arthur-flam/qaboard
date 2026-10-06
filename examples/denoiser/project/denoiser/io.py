from pathlib import Path
from typing import Optional

import numpy as np
from PIL import Image


def load_image(path) -> np.ndarray:
  """RGB image as float32 in [0, 1]."""
  with Image.open(path) as img:
    return np.asarray(img.convert('RGB'), dtype=np.float32) / 255


def save_image(path, img: np.ndarray):
  Path(path).parent.mkdir(parents=True, exist_ok=True)
  Image.fromarray(np.round(np.clip(img, 0, 1) * 255).astype(np.uint8)).save(path)


def find_ground_truth(path) -> Optional[Path]:
  """
  Our test images live in data/noisy/, and their clean versions at the same place in data/clean/.
  Returns None if there is no ground truth for this image.
  """
  parts = Path(path).resolve().parts
  if 'noisy' not in parts:
    return None
  i = len(parts) - 1 - parts[::-1].index('noisy')
  candidate = Path(*parts[:i], 'clean', *parts[i + 1:])
  return candidate if candidate.exists() else None
