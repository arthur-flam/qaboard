"""
Denoising filters. Images are float32 arrays, HxWx3, with values in [0, 1].
"""
import math

import numpy as np


def luma(img: np.ndarray) -> np.ndarray:
  return img @ np.array([0.299, 0.587, 0.114], dtype=np.float32)


def gaussian_kernel(sigma: float) -> np.ndarray:
  radius = max(1, math.ceil(3 * sigma))
  x = np.arange(-radius, radius + 1, dtype=np.float32)
  kernel = np.exp(-x**2 / (2 * sigma**2))
  return kernel / kernel.sum()


def gaussian_blur(img: np.ndarray, sigma: float) -> np.ndarray:
  """Separable gaussian blur, works on HxW or HxWxC arrays."""
  kernel = gaussian_kernel(sigma)
  radius = len(kernel) // 2
  out = img.astype(np.float32)
  for axis in (0, 1):
    pad = [(0, 0)] * out.ndim
    pad[axis] = (radius, radius)
    padded = np.pad(out, pad, mode='reflect')
    size = out.shape[axis]
    out = sum(w * np.take(padded, range(i, i + size), axis=axis) for i, w in enumerate(kernel))
  return out


def gaussian_denoise(img: np.ndarray, strength: float = 1.0) -> np.ndarray:
  return gaussian_blur(img, sigma=1.0 * strength)


METHODS = {
  'gaussian': gaussian_denoise,
}
DEFAULT_METHOD = 'gaussian'


def denoise(img: np.ndarray, method: str = DEFAULT_METHOD, strength: float = 1.0) -> np.ndarray:
  if method not in METHODS:
    raise ValueError(f"Unknown method {method!r}, choose from: {', '.join(METHODS)}")
  out = METHODS[method](img.astype(np.float32), strength)
  return np.clip(out, 0, 1)
