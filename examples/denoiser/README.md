# Example: an image denoiser, before and after QA-Board

A small but realistic project for demos and the promo video ("chapter 1: from results_final_v3 folders to a dashboard").
An engineer, Noa (fictional), writes image denoisers and evaluates them by hand: `python denoise.py IMAGE --output X.png`,
folders like `results_final_v3_REAL/`, PSNRs copied into a spreadsheet. Then she runs `qa wizard`, and QA-Board
shows that her latest "night" commit, better at night, quietly ruined the fabric texture.

Everything is synthetic, deterministic, and only needs `git`, `numpy` and `Pillow`. Each image takes 0.1-0.5 s.

## What's here

```
make_dataset.py            draws 7 scenes (day, night, texture, text, sky), clean + noisy: data/{clean,noisy}/<category>/<scene>.png
make_history.py            creates the project's git repository, with its history and messy results folders
project/                   the project at its latest commit: denoise.py (CLI), denoiser/ (filters, metrics, io)
history/filters_{1,2,3}.py denoiser/filters.py in the earlier commits
reference-integration/     what a good AI assistant writes during `qa wizard`
  qa/main.py, qa/metrics.yaml, qa/batches.yaml, qaboard.yaml
  edits.json               the same, as edit_file/write_file tool calls on the wizard's template (for a scripted fake LLM)
  apply_edits.py           applies edits.json without an LLM
```

## The project's history

| # | Commit | What happens |
|---|--------|--------------|
| 1 | Gaussian denoiser + eval script | gaussian blur; adds `data/` |
| 2 | Bilateral filter: keep edges sharp | much better on day, text, fabric; worse at night |
| 3 | Stronger smoothing for night shots | estimates the noise, smooths more when it's high: better at night, but fine stripes look like noise, so **fabric regresses** (worse than the noisy input!) |
| 4 | Estimate noise on diagonal details: fabric isn't noise | the fix, not created by default: `--apply-next` |

PSNR (dB) with `qa batch all` at each commit:

| input | 1 gaussian | 2 bilateral | 3 night | 4 fix |
|---|---|---|---|---|
| day/portrait.png | 33.91 | 35.90 | 35.90 | 35.90 |
| day/street.png | 28.60 | 35.06 | 35.06 | 35.06 |
| night/neon.png | 25.34 | 20.70 | 24.66 | 24.55 |
| night/parking.png | 26.45 | 20.72 | 25.86 | 25.73 |
| sky/sunset.png | 36.04 | 36.45 | 36.45 | 36.45 |
| text/sign.png | 23.50 | 33.23 | 34.04 | 33.56 |
| texture/fabric.png | 18.94 | 30.66 | **24.12** | 30.66 |

## Regenerate

```bash
python make_history.py ~/denoiser               # commits 1-3, plus results*/ folders, notes.txt, results_table.csv
python make_history.py ~/denoiser --upto 2      # stop earlier, to make commit 3 on camera
python make_history.py ~/denoiser --apply-next  # add the next commit, dated now (--date to choose, --no-commit to commit yourself)
python make_history.py ~/denoiser --force       # start over
python make_dataset.py data                     # only the images
```

The repository has a fictional, unreachable `origin` remote (`git@gitlab.example.com:imaging/denoiser.git`), so that
`qa wizard` names the project `imaging/denoiser`. The messy folders and notes are git-ignored: `git status` is clean.

## Running the integration (what the video does)

```bash
cd ~/denoiser
ls; cat results_table.csv                       # the pain
qa wizard                                       # or: qa wizard --yes --no-ai && python .../reference-integration/apply_edits.py .
git add .gitignore qa qaboard.yaml && git commit -m "Track results with QA-Board"
qa run --input night/parking.png
qa batch all                                    # also: smoke, night, methods (gaussian vs bilateral)
python .../make_history.py . --apply-next       # the next commit, then `qa batch all` again
```

Use `qa --share batch all` (or CI) to see results per commit in the web app. Each run saves `output.png`, `zoom.png`
(noisy | output | ground truth, magnified), `error.png` (error heatmap, fixed scale), `input.png` and `ground_truth.png`,
and returns `psnr`, `ssim`, `psnr_gain` and `runtime_ms`. `method` and `strength` come from `context.params`:
`qa --tuning '{"strength": 1.2}' run -i day/street.png`.

## Gotchas

- `qa` imports qa/main.py, which imports numpy and Pillow: install them where `qa` runs (`pip install -r requirements.txt`).
- `qa batch` runs `qa run` in subprocesses: `qa` must be on the `PATH`.
- Inputs are relative to `inputs.database: data/noisy` (relative to where you run `qa`, the repository's root).
  Ground truths are found by `denoiser.io.find_ground_truth`, which swaps `noisy/` for `clean/` in the path.
- Local results go to `output/linux/<input>/`, overwritten by each run: compare commits with `--share` and the web app.
- In `edits.json`, the `inputs.database` and `inputs.globs` edits are `optional`: they're skipped when the person
  already told the wizard that inputs are in `data/noisy`.
