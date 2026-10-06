# denoiser

Small image denoising experiments: gaussian and bilateral filters, PSNR/SSIM evaluation.

```bash
pip install -r requirements.txt
python denoise.py data/noisy/day/street.png --output out.png
python denoise.py data/noisy/night/parking.png --output out.png --method gaussian --strength 1.5
```

Test images are in `data/noisy/`, with their ground truth at the same place in `data/clean/`.
When a ground truth exists, `denoise.py` prints the PSNR and SSIM.
