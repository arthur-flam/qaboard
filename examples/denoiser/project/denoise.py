#!/usr/bin/env python3
"""
Denoise an image:
  python denoise.py data/noisy/day/street.png --output out.png --method bilateral --strength 1.0

If a ground truth exists (data/clean/... for data/noisy/...), prints PSNR and SSIM.
"""
import argparse
import time

from denoiser import DEFAULT_METHOD, METHODS, denoise
from denoiser.io import find_ground_truth, load_image, save_image
from denoiser.metrics import psnr, ssim


def main():
  parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
  parser.add_argument('input', help="noisy image")
  parser.add_argument('--output', '-o', required=True, help="where to save the denoised image")
  parser.add_argument('--method', default=DEFAULT_METHOD, choices=sorted(METHODS))
  parser.add_argument('--strength', type=float, default=1.0, help="more is smoother")
  args = parser.parse_args()

  noisy = load_image(args.input)
  start = time.perf_counter()
  out = denoise(noisy, method=args.method, strength=args.strength)
  elapsed = time.perf_counter() - start
  save_image(args.output, out)
  print(f"{args.input}: {args.method} (strength {args.strength:g}) in {elapsed * 1000:.0f} ms -> {args.output}")

  gt_path = find_ground_truth(args.input)
  if gt_path:
    gt = load_image(gt_path)
    print(f"  PSNR {psnr(out, gt):.2f} dB (noisy: {psnr(noisy, gt):.2f} dB)   SSIM {ssim(out, gt):.3f} (noisy: {ssim(noisy, gt):.3f})")


if __name__ == '__main__':
  main()
