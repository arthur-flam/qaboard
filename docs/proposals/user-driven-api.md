# Proposal: a user-driven API for QA-Board (`qaboard.start()`)

**Status:** draft for discussion
**Scope:** `qaboard/` (CLI package), small additive changes in `backend/`, later `webapp/`

## TL;DR

Today QA-Board assumes it is the entrypoint: users run `qa run` / `qa batch`, and the CLI
calls their `run(context)` function. This inversion of control is great for CI and batch
regression testing, but it is a poor fit for many workflows where the *user's code* is the
entrypoint: notebooks, training loops, pytest suites, Airflow/DAG pipelines, tools that
already have their own CLI.

We propose a library-mode API in the spirit of wandb / Neptune / MLflow:

```python
import qaboard

with qaboard.start(input="videos/night-drive.mp4", configurations=["base"]) as run:
    for step, frame in enumerate(frames):
        psnr = process(frame, run.params)
        run.log({"psnr": psnr}, step=step)         # time-series
    run.log_summary({"psnr_median": 31.2})          # what the tables/regressions use
    run.save(fig, "psnr-over-time.png")             # shows up in the visualizations
# run is marked finished (or failed/crashed) automatically
```

…while keeping what makes QA-Board different from wandb: **commit-centric organization,
inputs as the comparison key, and files-on-shared-storage instead of uploads**.

The existing `qa run` / `qa batch` / `run(context)` flow does not go away: it gets
*reimplemented on top of the new API*, so both styles share one code path and can be mixed
(a legacy entrypoint can call `qaboard.current_run().log(...)` today, a library-mode script
can be scheduled by `qa batch` tomorrow).

---

## 1. Why

### What the entrypoint model can't do well

- **Notebooks / REPL**: you can't `qa run` a notebook cell. Exploratory work is where
  experiment tracking starts, and today QA-Board is absent there.
- **Training loops**: `run(context)` returns one `dict` of metrics at the end. There is no
  way to stream loss curves, per-epoch metrics, or intermediate previews while the run is
  alive.
- **Existing entrypoints**: teams with their own CLI/pipeline must refactor into a
  `run(context)` function and a `qaboard.yaml` entrypoint before they see any value.
  Adoption cost is front-loaded exactly where it shouldn't be.
- **Gradual adoption**: with wandb you add two lines to an existing script and get value.
  QA-Board asks you to restructure first.
- **Debugging**: running user code under `qa` means debuggers, profilers and `python -m`
  workflows need workarounds.

### What we refuse to lose (QA-Board's edge over wandb/Neptune)

1. **Commit-centric**: runs attach to git commits and branches, not to a flat run list.
   This is what makes per-commit CI dashboards, regression tracking and "compare this
   branch to `master`" first-class. wandb only *records* the git SHA; QA-Board *organizes*
   around it.
2. **Inputs & configurations as the comparison key**: an output is
   `(commit, batch, input, configurations, platform)`. That key is what lets the webapp and
   `matching_output()` do apples-to-apples comparisons across commits, and what makes
   "same test, new code" a well-defined question. wandb has nothing like it.
3. **Files-first, no upload**: outputs are written to shared storage under predictable
   paths; viewers (IIIF images, videos, plots, 3D, flame graphs, text diffs) read the files
   directly. 100 GB of debug dumps per run is normal here and would be absurd to upload to
   a tracking server.
4. **Batch orchestration**: `qa batch` + runners (local / LSF / Celery), tuning searches,
   `qa optimize`, bit-accuracy checks. wandb sweeps are the only comparable feature and
   they are weaker for HPC.

The proposal is therefore **not** "become wandb", but: *expose QA-Board's data model
through a user-driven SDK, and adopt wandb/Neptune's best ergonomics on top of it.*

---

## 2. The new API

Everything lives in a new module, `qaboard/sdk/` (exported at top level), usable with zero
configuration files and zero server if needed.

### 2.1 Starting a run

```python
import qaboard

run = qaboard.start(
    # ── all optional ──────────────────────────────────────────────
    project="dvs/hdr",                  # default: qaboard.yaml → git remote → cwd name
    input="sequences/night-drive.raw",  # default: a "named run", see below
    database="/mnt/datasets",           # default: qaboard.yaml inputs.database
    configurations=["base", {"gain": 2}],
    params={"lr": 1e-4},                # ≈ wandb config / today's extra_parameters
    batch="nightly-hdr",                # ≈ wandb group; today's --batch/--label
    platform=None,                      # default: linux/windows
    output_dir=None,                    # default: QA-Board's storage conventions
    mode="auto",                        # "auto" | "online" | "offline" | "disabled"
    share=False,                        # same meaning as `qa --share`
    name=None,                          # display name for named runs
    run_id=None, resume="never",        # see §2.6
)
```

Semantics:

- **Auto-detection, not requirements.** `qaboard.start()` walks up for `qaboard.yaml` and
  `.git` like the CLI does and fills in project/commit/branch/database defaults, but *none
  of it is mandatory*. Outside a git repo it attaches runs to the existing
  `<local:{user}>` pseudo-commit (this fallback already exists in `config.py`). No config
  file → sane defaults + `~/.config/qaboard` for the server URL, like `QABOARD_HOST` today.
- **Context manager + atexit.** `with qaboard.start() as run:` marks the run
  finished/failed on exit; a bare `qaboard.start()` registers an `atexit`/exception hook so
  crashed scripts show up as *crashed*, not eternally *pending* (wandb behavior; today a
  killed `qa run` leaves a pending row until someone notices).
- **`mode="disabled"`** returns a no-op run so library users can keep instrumentation in
  production code (wandb's killer adoption feature).
- **Named runs.** `input=` stays the recommended way to get cross-commit comparison, but is
  no longer required. `qaboard.start(name="train-resnet")` creates an output whose input is
  the synthetic `runs/train-resnet`. It participates in batches, metrics, files and
  history like any output — it just compares by name instead of by test input. This is the
  wandb-style "just track my script" mode, expressed in the existing data model (one
  auto-created `TestInput` per name), so **no schema change**.

### 2.2 Logging metrics

```python
run.log({"loss": 0.31, "psnr": 29.8}, step=epoch)   # appends to the series
run.log({"gpu_mem": 11.2})                           # step auto-increments

run.log_summary({"psnr_median": 31.2, "is_failed": False})
run.summary["psnr_median"]                           # read back
```

- **Summary vs series.** `log_summary()` is exactly today's `metrics.json` — the scalars
  shown in tables, aggregated per batch, compared against `qaboard.yaml` metric targets,
  used by regression checks. `log()` appends to a new **series file**
  `output_dir/history.jsonl` (one JSON object per line: `{step, _timestamp, ...values}`).
  By default, the last logged value of each series key is folded into the summary, like
  wandb.
- **Files-first, again.** The series is a *file in the output directory*, not rows in
  Postgres. The webapp gets a "history" panel that fetches `history.jsonl` over the
  existing `/s/` file serving — consistent with how every other viewer works, zero backend
  load, and 10⁶-step histories cost the server nothing. (If aggregation across runs is
  wanted later, a Parquet mirror is a backend-side optimization, not an API change.)
- **Declared metrics stay.** `qaboard.yaml`'s metric declarations (label, unit,
  `smaller_is_better`, targets) keep working and now also apply to series keys. Undeclared
  keys are allowed, as today.

### 2.3 Files, images, rich media

QA-Board's answer to wandb artifacts is one it already has: **the output directory**.
The SDK makes writing into it ergonomic:

```python
run.dir                          # Path to the output directory
run.save("debug/*.png")          # copy files/globs into run.dir
run.save(fig, "loss-curve.png")  # matplotlib/PIL/numpy → file, via light dispatch
run.log_plotly(fig, "tradeoff.json")
run.open("report.html", "w")     # file handle inside run.dir
```

Anything written there is picked up by the existing manifest + viewers — the
`.png`/`.mp4`/`.plotly.json`/flame-graph conventions that already exist. No new "artifact"
concept, no upload, no versioning service: the manifests (`manifest.outputs.json`, hashes)
already provide integrity checking, and inputs already have `manifest.inputs.json`.

### 2.4 Finishing

```python
run.finish()                 # ok
run.finish(failed=True)      # explicit failure
# exceptions inside `with` → failed, traceback saved to log.txt
```

Log capture (`log.txt` via `redirect_std_streams`) is opt-in in library mode
(`qaboard.start(capture_logs=True)`), default-on when running under `qa`.

### 2.5 One current run, module-level sugar

```python
qaboard.current_run()   # the active Run or None
qaboard.log(...)        # proxies to current_run(), like wandb.log
```

This is what lets *framework callbacks* exist (pytest plugin, PyTorch-Lightning /
Keras callback ≈ 30 lines each) and lets legacy entrypoints log series today (§4).

### 2.6 The attach protocol (the glue between both worlds)

When a scheduler (today: `qa run` / `qa batch`; tomorrow: anything) has already created the
run row, it exports:

```
QABOARD_RUN_ID=1234
QABOARD_OUTPUT_DIR=/mnt/qaboard/.../output
QABOARD_ATTACH=1
```

and `qaboard.start()` **attaches** to that run instead of creating a new one (all
explicitly-passed args must be consistent or warn). This is the same trick wandb uses for
sweeps (`WANDB_RUN_ID`), and it is what makes the following work with zero user changes:

- `qa batch` keeps its role as the **launcher** (LSF/local/Celery, tuning matrices,
  `action_on_existing`, …) but the thing it launches can be *any command* that calls
  `qaboard.start()` — the current `run(context)` entrypoint becomes just one such command.
- A library-mode script behaves identically standalone (creates its run) and under
  `qa batch` (attaches to the pre-created pending run).

### 2.7 Offline mode and sync

`mode="offline"` writes everything (`run.json`, `metrics.json`, `history.jsonl`, files)
into the output directory and never touches the network. `qa sync` (which already exists
for metrics) is extended to walk an output tree and reconcile with the server. This covers
air-gapped rigs and flaky lab networks — a real Neptune strength worth copying.

### 2.8 Resume

```python
run = qaboard.start(run_id="1234", resume="must")    # continue: append to history
run = qaboard.start(..., resume="allow")             # attach if exists, else create
```

Maps to existing behavior: `keep_previous`/`QABOARD_RUN_KEEP` semantics for the output
directory, `PUT /api/v1/output/<id>` for state. Enables preemptible/checkpointed jobs.

### 2.9 Reading back (the query side)

Neptune/wandb's `Api()` is heavily used for reports; QA-Board has the pieces
(`batch_info`, `get_output`, `matching_output`) but they're internal. Promote them:

```python
from qaboard.api import Project

proj = Project("dvs/hdr")
batch = proj.commit("125abc").batch("nightly-hdr")     # or .branch("master").latest()
for output in batch.outputs:
    output.summary, output.params, output.input, output.dir
df = batch.to_dataframe()                              # pandas, flattened summary+params
run.history()                                          # DataFrame of history.jsonl
```

This is mostly a thin, documented wrapper over existing endpoints — cheap to build, huge
for notebook users, and it makes "write your own report/regression script" a supported
pattern rather than screen-scraping.

---

## 3. Concept mapping

| wandb            | Neptune            | QA-Board today                        | This proposal              |
|------------------|--------------------|---------------------------------------|----------------------------|
| `wandb.init()`   | `init_run()`       | `qa run` wraps `run(context)`          | `qaboard.start()`          |
| project          | project            | project (git repo + subproject)        | same, auto-detected        |
| run              | run                | output                                 | `Run`                      |
| group            | —                  | batch                                  | `batch=`                   |
| job_type         | —                  | `platform`, `type`                     | unchanged                  |
| config           | parameters         | configurations + extra_parameters      | `configurations=`, `params=` |
| `wandb.log(step=)` | series fields    | — (single `metrics.json`)              | `run.log()` → `history.jsonl` |
| summary          | fields             | `metrics.json`                         | `run.log_summary()`        |
| artifacts        | file sets          | output dir + manifests (shared storage)| `run.dir`, `run.save()`    |
| — (weak)         | —                  | **input / test recording**             | `input=` (optional)        |
| — (SHA recorded) | — (SHA recorded)   | **commit/branch as the organizing axis** | unchanged — our moat     |
| sweeps           | —                  | `qa batch` tuning, `qa optimize`       | unchanged + attach protocol |
| offline + sync   | offline + sync     | `--offline`, `qa sync` (partial)       | `mode="offline"` + `qa sync` |
| `mode="disabled"`| stub mode          | —                                      | yes                        |
| crashed-run detection | heartbeats    | stuck "pending" rows                   | atexit + heartbeat (§5)    |

---

## 4. Backward compatibility & unification

**No flag day.** The CLI is rebuilt *on top of* the SDK:

- `qa run` becomes: create/attach `Run` → set env vars (§2.6) → call the entrypoint's
  `run(context)` inside the run's lifecycle → `postprocess` → `finish()`. `RunContext`
  keeps its interface and gains `context.run` (the SDK `Run`); `notify_qa_database`
  becomes an internal of `Run` (the `ctx.obj` grab-bag finally gets an owner).
- Inside a legacy entrypoint, `qaboard.current_run()` is live — so existing projects can
  start streaming series (`qaboard.log({"iter_loss": l}, step=i)`) **without changing
  anything else**. This is the immediate carrot for current users.
- `qa batch` unchanged for users; internally it pre-creates pending runs and launches
  commands, which is what it already does — the attach protocol just formalizes it.

**One import-time cleanup is a prerequisite:** `qaboard/__init__.py` currently triggers
`check_for_updates()` and imports `config.py`, which eagerly reads `qaboard.yaml`, resolves
git state and prints warnings at import. A library must be silent and lazy on `import
qaboard` (works with no repo, no config, no network). Config resolution moves into a lazy
`qaboard.sdk.context` used by both the SDK and the CLI. This is the main refactor risk and
should land first.

---

## 5. Server-side changes (small, additive)

Phase 1 needs **no schema change**: `POST /api/v1/output/` already handles
create-with-pending, `PUT /output/<id>` updates metrics/state, commits are auto-created
from client-sent git metadata, `<local:user>` pseudo-commits already work, and `QA_TOKEN`
bearer auth exists.

Worth adding soon after:

1. **Heartbeat**: `Run` pings `PUT /output/<id>` every N min; a sweeper marks silent
   pending runs *crashed*. Kills the eternal-pending problem for everyone, CLI included.
2. **Named-run inputs**: auto-create `runs/<name>` test inputs server-side (a convention,
   not a migration).
3. **Tags** on outputs (JSONB `data` field can host them initially; index later if needed).
4. **Webapp history panel**: plot `history.jsonl` from the output dir (client-side fetch,
   like other viewers); overlay the same series across selected runs/commits.
5. Later, if demand: optional file *upload* endpoint for laptop users with no shared
   storage — explicitly out of scope for v1 to protect the files-first model.

---

## 6. What we deliberately do NOT take from wandb/Neptune

- **Upload-everything storage.** Shared storage + predictable paths is a feature, not a
  gap. (Optional upload can come later for laptop onboarding.)
- **A parallel "artifact" abstraction with its own versioning.** Manifests + the input
  database already cover integrity and identity; adding a second system would blur
  QA-Board's clearest concept.
- **Flat run lists as the primary UI.** Commits/branches stay the spine; named runs hang
  off pseudo-commits rather than introducing a second organizational model.

---

## 7. Phasing

1. **SDK core** — lazy config/context; `Run` (start/attach/log_summary/save/finish,
   offline, disabled); `POST`/`PUT` against existing endpoints; atexit/crash handling.
   *Usable from notebooks at the end of this phase.*
2. **Unify** — `qa run`/`qa batch` re-based on `Run` + attach protocol;
   `qaboard.current_run()` inside legacy entrypoints; `qa sync` for whole trees.
3. **Series** — `run.log(step=)` → `history.jsonl`; webapp history panel; last-value →
   summary folding.
4. **Query API** — `qaboard.api.Project/Batch/Output` wrappers, `to_dataframe()`.
5. **Sugar** — heartbeats + crashed sweeper; pytest plugin; Lightning/Keras callbacks;
   system metrics (CPU/GPU sampler → a reserved series); tags.

Each phase ships value on its own; nothing breaks existing projects at any point.

---

## 8. Open questions

- **Naming**: `qaboard.start()` (MLflow-ish) vs `qaboard.init()` (wandb-ish; clashes with
  `qa init`) vs `qaboard.run()` (clashes with the entrypoint convention). This draft says
  `start`.
- Should `params=` and tuning `extra_parameters` merge into one concept in the SDK
  (`run.params`), keeping the split only at the storage layer for webapp compatibility?
  (This draft assumes yes.)
- Named runs: attach to the *current commit* when in a repo (proposed), or always to a
  per-user pseudo-commit? Current-commit keeps the CI story coherent but makes
  "notebook noodling" pollute commit views — maybe `share=False` (default) already
  answers this since local runs are hidden by default.
- Series retention: cap `history.jsonl` size? downsample in the viewer or on write?
- Do we want `qaboard.start()` to work in-process concurrently (several active runs,
  Neptune-style handles) or is one-current-run-per-process (wandb-style) enough for v1?
  V1 says: `Run` objects are independent; only the module-level sugar assumes "current".
