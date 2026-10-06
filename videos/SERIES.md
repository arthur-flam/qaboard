# The QA-Board video series

One engineer, Noa, one project (the image denoiser of `examples/denoiser`), one pain per chapter, solved on screen
with the real tools. Each chapter is 3-5 minutes, starts from a feeling viewers know, and ends on what they can do next.

| # | Chapter | The pain | The "wow" | Needs |
|---|---------|----------|-----------|-------|
| 1 | **From `results_final_v3` to a dashboard** ([storyboard](01-getting-started/storyboard.yaml)) | Results in folders named by mood, metrics in a text file, "is it better?" | `qa wizard` + AI assistant wires the project in 2 minutes; the next change is better on average, but QA-Board shows the regression on textures | done |
| 2 | **Tuning without touching the code** | Tweaking `strength` means editing code, re-running, comparing by hand | Start a parameter sweep from the web app ("Run Tests / Tuning"), see the best values per scene, then `qa optimize` | a runner the server can start (local or LSF), `docs/tuning-from-the-webapp` |
| 3 | **Seeing is believing: visualizations** | Metrics say +0.3 dB, but what does it look like? | Synced image viewers with zoom and diff, plotly plots, text diffs, a flame graph of the runtime | richer outputs in the example (plots, a profile) |
| 4 | **Every commit, automatically: CI and milestones** | "It was better last month", nobody knows which commit | `qa batch` in GitLab CI on each commit, the history view, milestones to compare with a release | a CI runner in the stack, or a scripted CI log |
| 5 | **Bit-accuracy: "did I change anything?"** | Refactors that silently change outputs | `qa check-bit-accuracy` against the reference branch, in CI | outputs that change bit-wise in one commit |
| 6 | **More inputs, faster: batches, matrices and runners** | 7 test images is a toy; real datasets have 10,000 | Batch matrices, LSF/dask runners, filtering and sorting thousands of outputs | a bigger generated dataset, the dask runner |
| 7 | **For admins: running QA-Board** | "Who maintains this server?" | Zero-downtime deploys with `deploy.py`, the Helm chart, backups | the stack, `website/docs/backend-admin` |

## Recurring elements
- The same window chrome, captions and chapter label (`lib/stage`), the same terminal prompt (`noa@laptop ~/denoiser $`).
- The scripted AI assistant (`lib/fakellm.py`) whenever the wizard appears; a real model can replace it.
- Narration: short lines, "you" rather than "Noa", one idea per line. Ready for a voice (see README).
- End card: the command to try, and the next chapter.
