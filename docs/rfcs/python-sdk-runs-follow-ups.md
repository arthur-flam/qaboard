# Follow-ups to "creating runs from Python": what is left for training teams

Status: notes for discussion, November 2026. Nothing here is implemented or decided.
Follows `docs/rfcs/python-sdk-runs.md`, whose first version shipped as experimental (`qaboard.start_run()`, the series
viewer on run cards, the comparison of a batch's runs in the Summary view).
Three parts: (A) a run-centric view of a project, (B) the screenshots the new docs miss, (C) the gaps against a
hosted experiment tracker, with what to do about each.

## A. A run-centric view of a project

### The problem
QA-Board's pages are organized as project → commit → batch → runs, because its first question is "what did this
commit change?". Someone who trains models asks other questions first:

- "Where are my runs from this week?", whatever the commit: they change a parameter, not the code, or the code is
  dirty, or they run from a notebook with no commit at all.
- "Which of these 30 runs is the best, and what did it use?", across batches and commits.
- "How does today's run compare with Tuesday's?", two runs that are not new and reference of anything.

Today they can do it, with friction: the home page and the project page list *batches* ("Yours"), each opening one
commit's page; the Summary's comparison plots the runs of one batch; History plots a metric over *commits*. A sweep
done over three days on three commits is three pages. Runs outside the CI also land in a batch per user
(`@user| <batch>`), which helps "mine" and hides "the team's experiment".

### What is already there to build on
- Runs are rows with their commit, batch, input, configurations, reported parameters (`data.params`), metrics, who
  ran them and where from (`data.origin`, `submitted_by`), when (`created_date`, `started_at`, `finished_at`), and
  `data.sdk` for runs started from code.
- `GET /api/v1/batches` already lists batches across commits, by scope (mine, others) and origin, with aggregated
  metrics (`docs/home-and-project-pages-proposal.md`): the pattern for a list that is not commit-first.
- The series viewer's comparison (`webapp/src/viewers/SeriesComparison.jsx`) takes a list of runs, not a batch: it
  names them by what differs, colors them by a parameter, and only needs each run's folder.
- **Tuning Analysis** already has a parameters-and-metrics table, parallel coordinates and parameter importance,
  for the runs of one tuning experiment.
- Filters (`qaboard/filters.py`, shared with the web app) already match runs by input, configuration, parameters,
  status and id.

### Proposal: a **Runs** page per project
A fourth way into a project, next to **Commits**, **History** and **Milestones**: `/<project>/runs`.

1. **A table of runs, the latest first, across commits and batches.** One row per run: status, name (input and
   what differs in its configuration), who, when, duration, commit and batch (links to the commit page, where the
   run's files and its reference are), then one column per parameter that varies and per chosen metric. Scope tabs
   like the commits list: **Yours**, **Everyone's**, with the CI's runs off by default (a training team's project
   can have thousands of evaluation runs per day).
2. **Group and filter by what people think in.** A filter box with the existing syntax, plus quick filters: batch
   (the experiment), branch, user, origin (code, `qa`, CI, web app), status, a time range. "Group by" a parameter,
   the batch or the user turns the table into sections with the count and the best/median of each metric.
   *Experiment* is the natural word for this audience: show the batch's name without the `@user|` prefix, with the
   user as its own column, so that a team's experiment is one group whoever ran each run.
3. **Select runs, compare them.** Checkboxes, then:
   - their logged series on the same plots (the existing comparison component, fed with the selection);
   - their parameters and metrics side by side, differences highlighted (the Metrics Table, by run instead of by
     input);
   - **two** runs: open them as new and reference on the commit page, with every viewer the product has. That is
     QA-Board's strength and trackers' weak spot; the link is `/<project>/commit/<a>?reference=<b>&filter=id:…`,
     which works today.
   The selection lives in the URL, so a comparison can be shared.
4. **Pin a selection.** A milestone today is a commit and a batch (and a filter). Let a milestone hold a set of runs
   ("the candidates for 1.5"), so that the selection has a name, notes and a link, and can be a reference.
5. **Live.** Running runs first, with their last logged step and how long they have run; the table refreshes
   while any is running.

### What it needs
- **Server**: `GET /api/v1/runs?project=&scope=&origin=&batch=&branch=&filter=&since=&limit=&offset=`, returning
  runs with their commit and batch. The filter is applied in Python today (the web app's rules, on a batch's runs):
  across a project it needs SQL for the selective parts (user, origin, dates, status, batch) before the text filter,
  and indexes on `outputs (created_date desc)` joined to batches' commit and project. Parameters and metrics are
  JSONB: sorting by a metric across a large project needs care (a limit on the window, or an expression index per
  project's main metrics). Measure on a production copy before choosing.
- **Web app**: the page, the table (virtualized, like the benchmark viewer's), the selection in the URL, and the
  comparison component accepting runs from several commits (it already only needs `output_dir_url`). The sidebar
  gets **Runs** (`SIDEBAR.md`).
- **Python**: `qaboard.runs(project=..., since=..., scope="mine")` without a commit, on the same endpoint, so that
  notebooks can do their own analysis.
- **Docs**: a user guide page, and the deep learning page rewritten around it.

### Open questions
1. **Is it a new page, or a mode of the commits list?** The commits list already has **Yours** / **Everyone's** and
   a batches feed. A third unit (runs) in the same page keeps one place to look, and may be one toggle too many.
   A separate page is clearer for training teams, and invisible to the others if the sidebar only shows it for
   projects with runs started from code. Lean: separate page, shown when the project has such runs.
2. **Should training runs have a commit at all in the UI?** For a run from a dirty tree or a notebook, the commit is
   a detail, sometimes a placeholder. The Runs page can show it as a column and never require it; the commit page
   stays the place for "this run against its reference".
3. **Which reference does a training run have?** The reference bar compares with a commit's batch. For a training,
   the useful references are "the same configuration at the release", "my previous attempt" (kept in
   `data.previous_attempts`, without its files) and "the best run so far". The last two need product decisions:
   keep an earlier attempt's series (small) when a run is done again, and let a milestone be a run.
4. **Per-user batches.** Keep `@user| <batch>` for outside the CI (it protects the CI batch and powers "Yours"),
   and treat the raw label as the experiment's name in the Runs page? Or let the SDK share a batch by name
   (`start_run(batch=..., personal=False)`)? The first changes nothing on disk.
5. **Scale.** Samsung's server has more than 40 million runs: the page must never scan a project's runs. Start with
   a time window (the last 30 days) and explicit paging.

### A possible order
1. The endpoint, with scope, origin, batch, dates and paging, and `qaboard.runs(project=, since=)`. Useful alone,
   for scripts and agents.
2. The Runs page: table, quick filters, links to commit pages. No selection yet.
3. Selection and comparison (series, parameters and metrics, open two as new and reference).
4. Group by, then milestones that hold runs.

## B. Screenshots the docs miss

The new pages have no pictures: `website/docs/logging-from-code.mdx`, **Logged series** and **Compare all the runs
on the same plots** in `website/docs/user-guide/other-viewers.mdx`, and the `series` section of
`website/docs/viewers.mdx`. Screenshots come from the demo projects (`docs-media/README.md`): a shot needs a story
that produces its data, an entry in `docs-media/shots.yaml`, and a spec.

To do, in order:

1. **A training in a demo project.** `examples/detection-eval` evaluates models; none of the examples trains.
   Add a small, deterministic trainer to it (seconds on a CPU, fixed seeds: e.g. the calibration or distillation of
   the detector's head on its mini set) that calls `qaboard.start_run()`, logs `train/loss`, `val/loss`, `val/mAP`
   and `lr`, and saves the weights the existing evaluation reads. Story states (`examples/detection-eval/story.yaml`,
   `examples/STORY.md`):
   - a learning-rate sweep, 5 to 6 runs in one batch, one of them diverging (a NaN gap) and one clearly best;
   - the same configuration on the reference commit, so cards show new against reference;
   - one run left "running" when the snapshot is taken, for the Live state (or a spec that stubs `is_running`).
   `docs-media/seed.py` replays stories with real `qa` commands: check it can run a plain `python train.py` step.
2. **Shots** (`docs-media/shots.yaml`, status `planned`, `experimental: true`, a new feature key `logged-series`):

   | Id | Shows | Pages |
   |---|---|---|
   | `commit/logged-series` | A training's card: `train/loss` and `val/loss` against the reference run's, dashed; the toolbar | user-guide/other-viewers, log-from-code, viewers |
   | `commit/logged-series-smoothing` (clip) | Dragging **Smoothing**: the noisy curve becomes readable, the values stay behind | user-guide/other-viewers |
   | `commit/series-comparison` | The Summary's **Logged series**: the sweep's runs in colors, the best and the diverging one obvious | user-guide/other-viewers, log-from-code, deep-learning, use-cases (see C) |
   | `commit/series-comparison-color-by` | The same, colored by learning rate, one group shown alone | user-guide/other-viewers |
   | `commit/series-live` | A running run: the **Live** tag, curves that stop mid-way | log-from-code |
   | `cli/start-run` (tape) | `python train.py` printing the run's folder and address, then "finished" | log-from-code |

3. **Use them** with `<Shot>` in the pages above, with captions that say what to notice (`website/STYLE.md`), and
   add the feature to the user guide's overview if it lists viewers.
4. Until then the pages are text only; they don't use stand-ins.

## C. Gaps against a hosted experiment tracker, and what to do

For a team whose daily work is training, compared with Weights & Biases as of late 2026 (the public product: runs,
workspaces, sweeps, artifacts, reports). Sizes are rough: S days, M a couple of weeks, L more. "Decide" means a
product decision before any work. Their side of the table is from general knowledge of the product, not checked
against their current documentation: check it before publishing any of it in `website/docs/alternatives.mdx`.

| What they have | QA-Board today | Gap | Next step |
|---|---|---|---|
| A runs table across a project: sort, filter, group, columns per config and summary | Runs per commit and batch; batches across commits | **The main gap**: no run-centric view | Part A. L |
| A workspace of panels: any metrics, any runs, saved per user and shared | One plot per series, on a card or in the Summary; options in the URL | No custom panels (two series on one plot, a chosen x axis such as epoch, pinned plots) | Let people pick the x axis from a logged series, and put several series on one plot, in the comparison. M |
| Hundreds of runs on one plot, grouped with mean and spread over seeds | 40 runs of one batch, colored by a parameter | No averaging over a group; no runs from several batches | Mean and min/max band per color group (the files already have what it needs). S. Several batches: with part A |
| Two lines to start, anywhere, hosted | `start_run()` in a project with a server and shared storage | Setup: a server to run, a storage the server reads, a *qaboard.yaml* | Decide: uploads over HTTP (`docs/design/http-upload.md`) would remove the shared storage for small files; a hosted service is out of scope |
| Rich media per step: images, tables, audio, with a step slider | Files in the run's folder; a step slider for files named per step (dynamic visualizations, needs *qaboard.yaml*) | No `log_image()`, nothing shown without configuration | `run.log_image(name, image, step)` writing `media/<name>/<step>.png`, and the card showing them with the existing slider, like the series: no configuration. M |
| System metrics: GPU, memory, CPU, logged by default | None | Missing | A sampler thread in the SDK logging `system/…` series (`pynvml` and `psutil` optional), `summary="none"`. S |
| Sweeps with early stopping (Hyperband), launched from the UI | `qa optimize` (Optuna) for `qa run`-shaped code | No sweeps for code that calls `start_run()`; no pruning | Document the loop; then an Optuna pruner fed by `run.log()`. Decide if it is worth it. M |
| Artifacts: versioned datasets and models, lineage, a registry with aliases | Output files; badges linking to a registry | No versions, no lineage | Decide. A "model" file per run with a badge is S; a registry is a product of its own, likely no |
| Resume, offline mode with a later sync | `resume=True`; logging survives a server that is away | A run the server never heard of is not sent later | `qa sync <run folder>` reading *run.json*, *metrics.json* and the series. S |
| Alerts (a metric crossed a line, a run crashed), Slack and email | Emails when a `qa batch` ends; the quality gate in the CI | Nothing for runs started from code | A notification when such a run ends or fails, through the existing channel. S. Rules on logged values: later |
| Reports: text with live panels, shared by link | Milestones with notes; every page's address restores its state | No report editor | Decide. Probably no: link from your own documents |
| Integrations: Lightning, Hugging Face, Keras callbacks | None | Each framework needs a few lines by hand | A `qaboard.integrations.lightning` logger and a Transformers callback: thin, S each, once the API has settled. The `wandb`-shaped shim is in the RFC's left-outs |
| Notebook experience: a run's panel inline | Works from notebooks, untested; prints a link | No inline view | Test in Jupyter first (TODO_WARTS.md). An `_repr_html_` with the link and last metrics. S |
| Runs that crashed are marked as such | A killed process stays "running" | Stale runs | A heartbeat with the progress report, and the server marking runs silent for N minutes. S to M |
| Compare a run with any other in two clicks | New against reference, by commit and batch | See part A, point 3 | Part A |

Where QA-Board is ahead for the same team, to keep in the story (`website/docs/alternatives.mdx`): every commit
against the release by default, heavy outputs in synced viewers (images, video, point clouds, flame graphs),
per-input results on a test set, the quality gate in the CI, evaluations started from the browser on your own
cluster, self-hosted with your data in place.

### A use case page, when part A and the shots exist
`website/docs/use-cases/` has one page per kind of regression, each with a demo project and screenshots; there is
none for training. Add **"The loss looked fine, the model got worse"** (working title): a learning-rate sweep where
the run with the best validation loss is not the one with the best mAP on the hard slice. It walks from the runs'
curves (Summary) to the evaluation of the two best checkpoints on the test set (the existing detector evaluation),
to the gate. It needs B.1's trainer and shots, and reads better with the Runs page. Until then, the deep learning
page (`website/docs/deep-learning.mdx`) carries the feature, and the comparison table in `alternatives.mdx` says
what is experimental.

### Suggested order across the three parts
1. Verify what shipped against a real server, in Jupyter and on Windows; fix what that finds (`TODO_WARTS.md`).
2. Small gaps that make daily use safe: stale runs' heartbeat, `qa sync` for a run folder, the end-of-run
   notification, mean and band per group.
3. The demo trainer and the screenshots (B).
4. The runs endpoint and the Runs page (A.1 to A.3).
5. `log_image()` and system metrics.
6. The use case page; then decide on sweeps, artifacts and reports with users' feedback.
