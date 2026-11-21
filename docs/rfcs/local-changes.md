# RFC: runs record their local changes

Status: accepted, November 2026. Implemented with this RFC: see "What ships" at the end.
Related: `docs/rfcs/python-sdk-runs.md` ("Commit, dirty trees, notebooks"), `docs/quality-ledger-plan.md` (item B,
provenance), `docs/design/ingest-upload-sdk.md` (uploads).

## Why
A run says which commit it ran. Local runs (`qa run`, `qa batch`, `qaboard.start_run()` from a laptop or a
workstation) often don't run that commit: they run it plus what the developer changed and didn't commit yet. Today:

- `start_run()` records `data.dirty` and a hash of `git diff HEAD`. The hash ignores new files, so two runs that add
  different files look identical. The web app shows neither.
- `qa run` and `qa batch` record nothing. Local batches get a generic warning: "It is possible they didn't use the
  code under version control".
- Nobody can answer "what code produced this result?", or "what did I change between these two runs?", the question
  people ask all day when they experiment.

Asking for a commit per run is fine in CI, not when people iterate. Some teams work around it by copying their
code and the whole configuration into each run: it is heavy (big repositories), incomplete (C++, other languages,
dependencies), and what a hosted QA-Board must not receive by default.

## What others do
| Tool | Records |
|---|---|
| W&B | commit, remote, `diff.patch` (`git diff HEAD`); `upstream_diff_<sha>.patch` when HEAD isn't pushed. `wandb restore` checks out the commit and applies the patch. Copying code is opt-in. |
| Comet | git metadata and a patch (on by default), the entry script. |
| ClearML | commit, "uncommitted changes", installed packages; its agent re-runs a task by applying the diff on the commit. |
| Neptune | git info, dirty flag, diff, entry-point files. |
| MLflow | commit and source name; code is what users log. |
| DVC experiments | each experiment is a git commit under hidden refs: reproducible, no code outside git. |

The consensus: commit + patch, captured by default, code copies opt-in. We do the same, with a policy admins control.

## Design

### What is recorded: levels
`off` < `summary` < `patch` (`snapshot` is reserved, see "Later").

- **`summary`**, nothing of the code itself: whether the tree is dirty (or `null` when we couldn't tell), the branch,
  whether HEAD is pushed, the changed files' paths and +/- counts (at most 500), the number of untracked files, and a
  hash of the patch. Safe for a hosted server: paths, counts and a hash.
- **`patch`**, the default: also the patch, as a file in the run's folder, `.qaboard/local-changes.patch`. When HEAD
  isn't pushed, also `.qaboard/unpushed.patch`: the commits others can't fetch, from the merge-base with the upstream
  (W&B's `upstream_diff`). With both, anyone with the repository can rebuild the code: `qa restore`.
- **`off`**: nothing.

Recorded in `data.local_changes`:
```json
{
  "level": "patch", "scope": "batch",
  "dirty": true, "head": "<sha>", "branch": "feature/x", "upstream": "origin/feature/x",
  "pushed": false, "unpushed_commits": 2, "base": "<merge-base sha>",
  "files": [{"path": "src/a.py", "status": "M", "added": 10, "deleted": 2}, {"path": "notes.md", "status": "?", "added": 3, "deleted": 0}],
  "files_total": 2, "added": 13, "deleted": 2, "untracked": 1,
  "sha256": "<sha256 of the patch>",
  "patch": {"path": ".qaboard/local-changes.patch", "bytes": 1234},
  "unpushed": {"path": ".qaboard/unpushed.patch", "bytes": 4567, "sha256": "..."},
  "excluded": ["secrets.env"],
  "ms": 85
}
```
When a patch isn't stored, `patch` says why: `{"omitted": "too_large" | "level" | "timeout" | "error"}`.
`data.dirty` stays, a boolean, for compatibility.

### What is in the patch
- Tracked files: `git diff HEAD`, with `--no-ext-diff --no-textconv --no-color` (no program from the repository's or
  the user's configuration runs) and `--submodule=short`. Binary files appear as "Binary files differ", not as content.
- **New files that aren't ignored are included** (as `git add -N` would show them), computed on a copy of the
  index (`GIT_INDEX_FILE`): the user's index is never written.
- Untracked files that look like secrets aren't: `.env*`, `*.pem`, `*.key`, `*.p12`, `*.pfx`, `id_rsa*`,
  `*credentials*`, `*secret*` (and the project's `exclude` globs). They are listed as `excluded`, by name. Tracked
  files are already in git and are included as they are.
- When there are too many untracked files (more than 200, a build folder that isn't ignored), their contents are left
  out and the count says so.
- Size: at most 2 MB per patch (`max_patch_kb`). The hash is of the whole patch even when it is too large, so equal
  states still compare equal.

### When it is captured
- **In CI, nothing** (`CI` or `GIT_COMMIT` set): checkouts are clean, and the overhead is for nothing.
  `QA_LOCAL_CHANGES=summary|patch` forces it.
- **`qa run` and `start_run()`**: per run, when the run starts. In experiment tracking each run needs its own:
  people edit between runs.
- **`qa batch`**: once, when the batch starts, passed to its runs (`QA_LOCAL_CHANGES_FILE`: a JSON file with the
  state, next to the patch, in a temporary folder). The runs copy it into their own folder. We draw the line at the
  start: people who edit while their batch runs get the state it started with. Runners whose runs can't read the
  file (Kubernetes, Dask on another machine) keep the summary, without the patch. Those runs use the commit's image or
  artifacts anyway, not the working tree.
- Every run keeps its own copy of the patch: a run moved, exported or deleted takes its code with it, and the files
  follow the storage's rules (retention, permissions, uploads). Patches are small; the database only holds the
  summary and the hash (the content address). The web app fetches one copy per hash.

### Cost on big repositories
The cost is `git status` walking the working tree, not the patch's size. One `git status --porcelain=v2 -z --branch
--untracked-files=all` gives the branch, upstream, ahead/behind counts and changed paths; `git diff --numstat` and
`git diff` read the same index copy, whose stat cache git keeps. Everything runs under one budget (10 s by default,
`timeout`): past it, what's known is kept and `dirty` is `null` if it isn't. A run never fails because of this.
`qa doctor` measures it and suggests `git config feature.manyFiles true` / `core.fsmonitor true` when it is slow.

### Who decides: admins, then projects
- **Admins** set the most a run may record: the `runs.local_changes` setting (Admin → Settings, or
  `QABOARD_LOCAL_CHANGES`): `patch` by default, `summary` or `off` for a hosted server or a site that doesn't want
  code in QA-Board.
- **Projects** choose within it in qaboard.yaml:
  ```yaml
  outputs:
    local_changes: patch        # or summary, off; or a mapping:
    # local_changes:
    #   level: patch
    #   max_patch_kb: 2048
    #   timeout: 10
    #   exclude: ["*.ckpt", "data/**"]
  ```
  `$QA_LOCAL_CHANGES` overrides the level for one command.
- **The server enforces it**: it drops what's above its level from `data.local_changes` and refuses `.qaboard/*.patch`
  uploads (reason `local_changes_not_allowed`). That is a real boundary for runs that upload their files, the only
  kind a hosted server gets. With a storage shared with the server, `qa` writes the files itself: it learns the
  server's level from the answer to the run's (or the batch's) first request (`local_changes_level`) and writes no
  patch above it, but nothing prevents a modified client from writing files in a storage it can write to.
- `qa` says what it recorded: `Local changes: 3 files, +40 -12 (saved in the run)`, and why when it couldn't.

### In the web app
- **A tag on runs with local changes**: "+40 −12 · 3 files". It opens the changes.
- **Batches whose runs share a state say it once**, above the runs, instead of the old warning:
  "Local runs on 3f2a1b9c with uncommitted changes: 3 files, +40 −12 — View", or "Local runs on 3f2a1b9c, without
  local changes", a fact instead of a doubt. When runs differ, the callout says how many states there are, and each
  run carries its tag.
- **The changes viewer**: the files with their counts, the patch (Monaco, `diff` language: virtualized, so large
  patches stay fast), Download, and how to reproduce (`qa restore <run>`, or the `git` commands). It shows unpushed
  commits when there are, and says when the patch was too large or not allowed.
- **Comparing two runs' changes**: when the run and its reference both have patches that differ, "Compare with
  the reference's changes" shows the patches side by side (Monaco's diff editor).
- **The sidebar's Source section** gets "Local changes": the batch's distinct states, with how many runs each.
  "Code" links say when the commit isn't pushed (the git host doesn't know it).
- **Filter**: `changes:local` and `changes:none` in the runs' filter.
- `.patch` and `.diff` files open with highlighting in the Files views.

### Reproducing: `qa restore`
`qa restore <run id or URL>` makes a branch `qa-restore/<run id>` at the run's commit (or at its `base`, then
applies `unpushed.patch`) and applies `local-changes.patch`. It refuses to run in a dirty tree, and says what to do
when the commit is unknown locally (`git fetch`). It is `wandb restore`.

## Security
- A patch is source code: it is visible to whoever sees the run. Projects that restrict their files restrict it too.
- `--no-ext-diff --no-textconv`: capturing never runs a program configured by the repository.
- Untracked files that look like secrets are excluded by name; projects add their own globs.
- Patches are files served like the run's other files (QA-Board's sandboxed origin rules apply), shown as text.
- Admins can lower the level server-wide; the server strips and refuses above it.

## Later
- **`snapshot`**: a commit of the working tree (`git commit-tree` on the index copy) pushed to
  `refs/qaboard/snapshots/<hash>` on the project's own remote. QA-Board would store only its sha: full
  reproducibility without code on the server, and runners on other machines could run uncommitted code. It needs
  push rights and a cleanup policy for the refs.
- Deduplicating patch files on the storage (they are small, so we haven't).
- The run's environment: a hash of the lockfile, `pip freeze` (W&B saves `requirements.txt`).

## What ships
Filled in when the work lands.
