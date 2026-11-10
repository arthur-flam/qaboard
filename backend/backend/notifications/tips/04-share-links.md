---
key: share-links
subject: "Send a link, not a screenshot"
preheader: "A QA-Board link keeps the reference, the filters, the view and how the images are compared."
cta:
  label: "Share what you see"
  url: "/docs/user-guide/sharing#links-are-shareable"
---
Most of what you pick on a commit page is saved in its link: the reference, the batches, the filters, the names of
both sides, the view and how images are compared. Whoever opens it sees the same comparison, and still does next
month, after `main` moved on: the reference commit is written in the link.

Narrow the page down before you share it. The filter box of each bar takes:

- `low-light -gradient`: the low-light runs, except the gradient's
- `facade|foliage`: the runs of either input
- `status:failed`: only the runs that failed
- `denoise_strength:1.6`: one value of a tuning parameter

Then click the link button next to the reload button, and paste. In Teams, Outlook or Confluence, the link reads like
the page's title, e.g. *ISP v2 vs baseline · isp: Faster denoising · 40 runs: 37 passed, 3 failed*.

The zoom isn't in the link. To point at a spot, paste the image too: the copy button above the images copies the
new image, the reference or their difference.
