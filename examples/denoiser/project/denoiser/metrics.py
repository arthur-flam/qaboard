"""
Image quality metrics, for float images in [0, 1].
"""
import numpy as np

from .filters import gaussian_blur, luma


def psnr(img: np.ndarray, reference: np.ndarray) -> float:
  """Peak signal-to-noise ratio, in dB."""
  mse = float(np.mean((img.astype(np.float64) - reference.astype(np.float64))**2))
  return float('inf') if mse == 0 else 10 * np.log10(1.0 / mse)


def ssim(img: np.ndarray, reference: np.ndarray) -> float:
  """Structural similarity on the luma, with the usual 11x11 gaussian window (sigma=1.5)."""
  x, y = luma(img), luma(reference)
  c1, c2 = 0.01**2, 0.03**2
  mu_x, mu_y = gaussian_blur(x, 1.5), gaussian_blur(y, 1.5)
  var_x = gaussian_blur(x * x, 1.5) - mu_x**2
  var_y = gaussian_blur(y * y, 1.5) - mu_y**2
  cov = gaussian_blur(x * y, 1.5) - mu_x * mu_y
  ssim_map = ((2 * mu_x * mu_y + c1) * (2 * cov + c2)) / ((mu_x**2 + mu_y**2 + c1) * (var_x + var_y + c2))
  return float(ssim_map.mean())
