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


def bilateral(img: np.ndarray, sigma_s: float, sigma_r: float) -> np.ndarray:
  """
  Bilateral filter: averages neighbors that are close in space *and* in color,
  so that edges stay sharp. Vectorized over the image, loops over the window.
  """
  radius = max(1, math.ceil(2 * sigma_s))
  h, w = img.shape[:2]
  padded = np.pad(img, ((radius, radius), (radius, radius), (0, 0)), mode='reflect')
  acc = np.zeros_like(img)
  weights = np.zeros((h, w), dtype=np.float32)
  for dy in range(-radius, radius + 1):
    for dx in range(-radius, radius + 1):
      if dx * dx + dy * dy > radius * radius:  # round window
        continue
      spatial = (dx * dx + dy * dy) / (2 * sigma_s**2)
      neighbor = padded[radius + dy:radius + dy + h, radius + dx:radius + dx + w]
      color_dist = np.sum((neighbor - img)**2, axis=-1)
      weight = np.exp(-spatial - color_dist / (2 * sigma_r**2))
      acc += weight[..., None] * neighbor
      weights += weight
  return acc / weights[..., None]


def estimate_noise(img: np.ndarray) -> float:
  """Standard deviation of the noise, from the median absolute deviation of the luma's laplacian."""
  y = luma(img)
  laplacian = 4 * y[1:-1, 1:-1] - y[:-2, 1:-1] - y[2:, 1:-1] - y[1:-1, :-2] - y[1:-1, 2:]
  # for white noise, the laplacian's std is sqrt(4**2 + 4) = sqrt(20) times bigger
  return float(np.median(np.abs(laplacian)) / 0.6745 / np.sqrt(20))


def bilateral_denoise(img: np.ndarray, strength: float = 1.0) -> np.ndarray:
  # Night shots are much noisier: smooth more when we measure more noise
  noise = estimate_noise(img)
  sigma_r = max(0.1, 3 * noise) * strength
  sigma_s = 1.5 * strength * (2.0 if noise > 0.05 else 1.0)
  return bilateral(img, sigma_s=sigma_s, sigma_r=sigma_r)


METHODS = {
  'gaussian': gaussian_denoise,
  'bilateral': bilateral_denoise,
}
DEFAULT_METHOD = 'bilateral'


def denoise(img: np.ndarray, method: str = DEFAULT_METHOD, strength: float = 1.0) -> np.ndarray:
  if method not in METHODS:
    raise ValueError(f"Unknown method {method!r}, choose from: {', '.join(METHODS)}")
  out = METHODS[method](img.astype(np.float32), strength)
  return np.clip(out, 0, 1)
