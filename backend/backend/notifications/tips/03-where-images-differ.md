---
key: where-images-differ
subject: "Let QA-Board find where two images differ"
preheader: "Rank a batch's runs by how much their images changed, then zoom on the regions that differ most."
cta:
  label: "Find the differences"
  url: "/docs/user-guide/image-viewer#find-where-images-differ"
---
A PSNR that went up can hide halos on one image out of forty. Instead of scanning them all, ask the server where to
look:

1. In the regions menu above any image, pick **Where they differ, in all N runs…**. The runs are ranked by their
   largest difference; those that didn't change are counted as *identical*.
2. Click **Show** next to the first run: its card zooms on the region that differs most. `]` goes to the next one.
3. Press `3` for **Single**, then `t` to flip between new and reference in place. Press `4` for their
   **Difference**.

Pick how the server measures differences: **perceptual** for any visible change, **structure (SSIM)** for lost
texture or ringing, **ΔE 2000** for color. To judge on looks alone, **Blind test** (`h`) hides which image is which.

Videos work the same way: **Differences**, at the right of the timeline, finds the frames that differ most.

Sign in first: the server compares the full images, not what your screen shows.
