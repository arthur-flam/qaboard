# Sending results without `qa run`: `start_run()`, `qa upload`, HTTP upload, and where the bytes live

Status: proposal v2 (2026-11-14), for review. v1 (2026-10-26) proposed a new run client and an SDK on top of it;
since then `qaboard.start_run()` was written (branch `ccr-e581e9cd-w19rhx`, about to merge;
`docs/rfcs/python-sdk-runs.md`), and the benchmark and flame graph viewers shipped with their Python readers. v2
builds on them instead: it drops v1's client and SDK milestones, and reviews how the pieces fit (§2).
Revises the phasing of `docs/design/http-upload.md`.

## TL;DR
- **`start_run()` is the one way to create a run from outside `qa run`.** `qa upload` is its command line: about
  a hundred lines that call `start_run()`, `log_artifact()` and `log_metrics()`. No second client, no second
  vocabulary. v1's `qaboard/ingest.py` is dropped.
- **Milestone 1, `qa upload` on shared storage, got smaller (S)** and more useful: the branch already handles
  projects without a qaboard.yaml, attempts, permissions and metric definitions; `qaboard.benchmarks` already
  turns six harnesses' files into metrics. What's left: the command, a JUnit reader in the package, and run cards
  that show recognized files (test reports, benchmarks, flame graphs) without anything in qaboard.yaml.
- **What it gives:** the three steps that the test, benchmark and profile docs each ask for today (write a
  `run()` that calls a subprocess, save the file, list it in `outputs.visualizations`) become one CI line:
  `qa upload junit.xml pytest.benchmarks.json cpu.folded`.
- **Milestone 2, HTTP upload of small files, is one seam under `start_run()` and `qa run`:** a folder syncer.
  A run's folder becomes a local staging folder, and a background thread sends what changed. It follows the
  SDK's rule, "the run sends its state, not its events", so nothing new has to arrive in order.
- **The `start_run()` branch can merge as it is.** Nothing in its API assumes shared storage in a way upload
  would break. Five things to settle soon after, before more surfaces copy them: one word for batch/experiment,
  the project's name in CI without a qaboard.yaml, runs nested in runs, recognized files on cards, and one
  mechanism for runs that stopped reporting (§2.3).
- **Storage** (server-managed, or a bucket the project pays for): unchanged from v1, in §6.

---

## 1. Where things stand (November 2026)

| Piece | State | Where |
|---|---|---|
| Runs created by `qa run` / `qa batch` | Shipped | `qaboard/qa.py` |
| Add metrics and files to finished runs: `qaboard.runs(url)`, `run.log_metrics()`, `run.log_artifact()`, `qa runs` | Shipped | `qaboard/results.py` |
| Create runs from Python: `qaboard.start_run()`, `run.log()` series, `PATCH /output/<id>/progress`, the series viewer | Branch, experimental | `qaboard/active_run.py`, `qaboard/series.py`, `qaboard/payloads.py` |
| Benchmark files as metrics and a comparison table (pytest-benchmark, Google Benchmark, hyperfine, Criterion, JMH, `go test`) | Shipped | `qaboard/benchmarks.py`, `webapp/src/viewers/benchmarks/` |
| Differential flame graphs (folded stacks, speedscope, `.cpuprofile`; `perf script` and pprof through a helper) | Shipped | `qaboard/flamegraphs.py`, `webapp/src/viewers/flame-graph.jsx` |
| Test reports (JUnit XML) compared with the reference's | Viewer shipped; the reader of metrics is only in an example | `webapp/src/viewers/junit/`, `examples/software-quality/qa/junit.py` |
| Metric definitions saved with the run (`Metric(...)`) | Shipped | `qaboard/metrics.py` |
| Reruns as attempts (`<run>_attempt2`, `latest_attempt.txt`) | Shipped | `qaboard/attempts.py` |
| Scoped tokens, `ingest.auth`, service accounts, CI jobs exchanging their OIDC token | Shipped | `backend/backend/tokens.py`, `backend/backend/api/auth.py` |
| Kubernetes Job runner | Shipped | `docs/design/kubernetes-runner.md` |
| `qa upload` | This proposal, milestone 1 | |
| HTTP upload | Designed (`http-upload.md`), not built | |

What v1 planned and no longer needs building: the run client and its config refactor for projects without a
qaboard.yaml (the branch's `_Setup`), the SDK (milestone 3), benchmark readers (milestone 4), `metrics.jsonl`
(the branch's `series.jsonl` / `series.json`), OIDC tokens for CI.

---

## 2. Coherence review of the `start_run()` branch

### 2.1 What fits, and should be kept as the base
- **One identity** `(project, commit, batch, input, configs, platform)` for `qa run`, `start_run()` and so for
  `qa upload`: a test report uploaded from CI compares with the reference like any run.
- **One payload definition** (`qaboard/payloads.py`) and one place for folders (`config.commit_outputs_dir()`,
  `conventions.run_dir_in_commit()`, `attempts`): `qa upload` writes what `qa run` writes without knowing how.
- **`run.output_dir` is a local path people write into.** That is exactly what an upload transport needs: with
  HTTP upload it becomes a staging folder, and user code doesn't change.
- **State, not events.** The background thread says what the run looks like now. File sync can follow the same
  rule (§4.1): no queue, no replay, a final `finish()` that makes it exact.
- **Without a qaboard.yaml, no configuration is sent**, so a one-line upload from another repository can't
  replace a project's visualizations and metrics.
- **Readers return `Metric`s** (`benchmark_metrics`): direction and unit travel with the run, so uploaded
  results are colored and gated correctly with no metrics file.

### 2.2 What `qa upload` and HTTP upload need from it (no API change)
| Today on the branch | Needed | When |
|---|---|---|
| `share` needs a storage path (`can_share = outputs_commit is not None`, else `ValueError`) | Also true when the server accepts uploads (`GET /api/v1/config`) | Milestone 2 |
| Files are written in the storage by the process | Behind a seam: the run folder is local, a syncer sends it (§4.1) | Milestone 2 |
| `series.json` rewritten every 2 s, `series.jsonl` and `log.txt` appended | In upload mode: `series.json` sent with the 10 s sync; the two append-only files through one append endpoint | Milestone 2 |
| A new attempt reserves `<run>_attemptN` on the filesystem | In upload mode the server reserves it when the run is created | Milestone 2 |
| `capture_output` saves this process's output | `qa upload -- <command>` tees a child's output to the same `log.txt` | Milestone 1b |

### 2.3 To settle soon after the merge
None blocks the merge: the feature is marked experimental. Each gets more expensive once `qa upload` and the
docs repeat it.

1. **One word for the batch.** The CLI says `--label` (and `QA_BATCH_LABEL`), `start_run()` says `batch=`, the
   web app says batch, and the follow-ups propose `experiment=` as the documented name. `qa upload` would be a
   fourth surface. Proposal: `batch` is the word in every API (`qa --batch` as an alias of `--label`);
   "experiment" stays a translation in the training docs' table, not a second argument. If `experiment=` is
   wanted anyway, add `--experiment` to `qa` the same day.
2. **The project's name without a qaboard.yaml.** `start_run()` requires `project=`. In CI the name is known:
   `GITHUB_REPOSITORY`, `CI_PROJECT_PATH`, else the `origin` remote's path. Resolve it in `_Setup` so the SDK
   and `qa upload` (`--project`) behave the same. Without it, the one-liner needs an argument that every CI
   already knows.
3. **Runs inside runs.** `start_run()` called from code that `qa run` started, or from tests that
   `qa upload -- pytest` wraps, creates a second run. Proposal: whoever starts a run exports
   `QABOARD_RUN_DIR` (and the output id once known); `start_run()` under it, with no explicit identity,
   attaches: it logs to that run and `finish()` leaves the ending to the owner. An explicit `input=` still
   starts a separate run. This is what makes "a `qa run` that also logs series" and "tests that log metrics" work.
4. **Recognized files on cards without configuration.** The branch does it for series, for runs started from
   code (`with_logged_series`, `webapp/src/viewers/runView.js`). `qa upload junit.xml` in a project that lists no
   visualization shows nothing on the card: the first experience fails. Generalize the rule: for runs started
   from code or uploaded, files whose names a viewer recognizes (test reports, benchmarks, flame graphs, series)
   are shown, unless the project's `outputs.visualizations` already covers them. One function, the same
   precedence the branch chose.
5. **Runs that stopped reporting, and runs that never reached the server.** The follow-ups list a heartbeat for
   stale runs and `qa sync <run folder>`; v1 of this document had a "lost runs" sweep and `qa upload --pending`.
   They are the same two mechanisms: build them once, under the follow-ups' names. `PATCH /progress` is the
   heartbeat; `qa sync <run folder>` sends a finished folder later, files included in upload mode.
6. **A JUnit reader in the package.** `benchmark_metrics(path)` is in `qaboard/`; the JUnit reader is in
   `examples/software-quality/qa/junit.py`, to copy. Move it to `qaboard/junit.py` as `junit_metrics(path)`
   (`tests`, `passed`, `failed`, `errors`, `skipped`, `flaky`, `time`), tested on fixtures shared with the web
   app's parser, as benchmarks are.

---

## 3. Milestone 1: `qa upload` on shared storage

### 3.1 The command
```bash
# CI, after the tests and benchmarks ran: one run, named after the CI job
pytest --junitxml=junit.xml --benchmark-json=pytest.benchmarks.json
qa upload junit.xml pytest.benchmarks.json

# one run per suite: the input is each file's name without its extensions
qa --label unit-tests upload --each 'build/test-results/*.xml'

# a profile and a number, in a configuration
qa -c O3 upload --input codec cpu.folded -m throughput_mbps=812.5

# milestone 1b: wrap the command, with its output as the run's log
qa upload --input codec junit.xml -- ctest --output-junit junit.xml
```
| Option | Meaning |
|---|---|
| `PATHS…` | Files, folders (recursive) or globs, stored relative to their common parent, or to `--root` |
| `--input/-i NAME` | The run's name, as in `qa run -i`. Default: the single path's stem, else the CI job's name |
| `--each` | One run per path |
| `--as NAME` | Store a single file under this name, so that a viewer recognizes it (`--as junit.xml`) |
| `-m key=value`, `--metrics FILE` | Metrics. They win over those read from files |
| `--no-read` | Don't read metrics from recognized files |
| `--failed` | The run couldn't produce its results (`is_failed`) |
| `--append` | Add to the latest run of that identity instead of starting a new attempt |
| `--project`, `--commit` | As `start_run()`'s; needed outside a project and outside CI |
| `-- COMMAND` (1b) | Run it first; its output is the run's `log.txt`, its exit code is `qa upload`'s |
| Global options | `qa --label`, `-c`, `--platform` keep their meaning. `qa upload` always shares |

### 3.2 What it does
```python
with qaboard.start_run(input=name, configs=configs, batch=label, project=project, commit=commit,
                       capture_output=False) as run:
    for source, relative in files:
        run.log_artifact(relative, source)
    run.log_metrics({**read_metrics(files), **given_metrics})
```
- **Metrics from recognized files:** `junit_metrics` and `benchmark_metrics` (with a prefix per file when there
  are several). A failing test doesn't fail the run: it returns `failed: 3`, as the software-quality docs say.
- **Replace or add:** by default a new attempt, like running again. `--append` is
  `qaboard.runs(...)` + `log_artifact()` / `log_metrics()` on the finished run, which already exists
  (`qaboard/results.py`): coverage from a later CI job joins the tests' run.
- **Conversions at upload,** only where the web app can't read the file: `perf script` text and pprof `.pb.gz`
  go through `save_flame_graph`, a Criterion directory through `write_benchmarks`. Later, when asked for.
- **Machines without the shared storage** get a clear refusal until milestone 2; the command doesn't change then.

### 3.3 What it opens up
- **Unit tests of existing projects**, with no entry point: the test viewer against the reference, counts in
  History, and the gate catching "12 tests disappeared".
- **Microbenchmarks and profiles from a dedicated runner**: the benchmark table and flame graph diffs. The
  viewer already warns when the two files come from different CPUs.
- **Anything a CI job already writes**: coverage reports, binary sizes (`-m size_bytes=…`), lint counts.
- **Other languages and build systems**: a C++, Rust, Go or Java repository uploads its harness's files without
  writing Python.
- `qa run` stays the tool when QA-Board should start the code on each input: batches, tuning, redo from the web
  app. `qa upload` runs can't be started again from the web app, and the docs should say so.

### 3.4 Size: S, after the branch merges
The command and its tests (a run whose folder matches `start_run()`'s), `qaboard/junit.py`, recognized files on
cards (§2.3.4), the project's name in CI (§2.3.2). Docs: a page in Project integration, and a "from your CI,
without code" variant at the top of the benchmark, flame graph and test report pages; a release note.
Milestone 1b (`-- COMMAND`, nesting §2.3.3): S.

---

## 4. Milestone 2: HTTP upload of small files

### 4.1 One seam: a folder syncer
`qaboard/upload.py`: given a run's id and its local folder, a thread that every 10 s lists the folder and sends
what differs from what it sent last (path, size, mtime, then sha256), and at the end sends the rest and the
manifest. `ActiveRun` starts it next to its progress thread; `qa run` starts it around `run()`. Like
`payloads.py`, one definition for both, and `qa run` doesn't have to become an `ActiveRun`.

- **Which files go early** (the others at the end):

  | File | When |
  |---|---|
  | `log.txt`, `series.jsonl` | appended every few seconds, by offset |
  | `series.json`, `run.json`, `metrics.json` | each sync, when changed |
  | Files matching `outputs.live` globs, files saved with `log_artifact()` | once stable for two polls; again if they change |
  | Everything else | at `finish()` |

  `outputs.live` defaults to nothing: logs, series and metrics are live, other files are opt-in.
- **Staging:** the run's folder is under `$QA_STAGING` (`http-upload.md` §3.2). Shared storage needs none of
  this: files are visible as written.
- **A run that dies** keeps what was sent and its log; the heartbeat (§2.3.5) marks it failed.

### 4.2 Endpoints
All need a token with the `ingest` scope for the project (`ingest_access`).
| Request | Server |
|---|---|
| `POST /api/v1/output/` with `"storage": "upload"` and the relative run path | Builds the path under `QABOARD_UPLOAD_ROOT`, reserves the attempt's folder, returns the id and the limits |
| `PUT /api/v1/output/<id>/files/<rel path>` with `Repr-Digest: sha-256=…` | Validates the path (`http-upload.md` §6.4), writes to a temporary file, checks size and hash, renames. Same path and hash again: nothing written |
| `POST /api/v1/output/<id>/files/<rel path>?offset=n` | Appends, for `log.txt` and `series.jsonl` (`http-upload.md` §4.3's log endpoint, for two files) |
| `POST /api/v1/output/<id>/finish` with metrics, status and the manifest | Checks every file and hash, deletes files not in the manifest, writes `manifest.outputs.json`, ends the run |
| `GET /api/v1/config` | Adds `uploads: {enabled, max_file, encodings}` |

Limits: 256 MiB per file, 10 GiB and 100k files per run, `429` per token. Larger files get `413` naming shared
storage as the way, until milestone 4. Idempotency comes from paths and hashes: no session table.

### 4.3 Before uploads face the internet
Shipped since `http-upload.md`: scoped tokens, `ingest.auth`, CI token exchange (GitHub-hosted runners need no
stored secret), the sandboxed HTML viewer, a `Content-Security-Policy` on served files. Still open in this tree:
`/iiif/` has no `auth_request` (`services/nginx/conf.d/qaboard.conf`), `/s/` sends no
`X-Content-Type-Options: nosniff`; and to check again: the image APIs' path checks and the manifest `?refresh`
rewrite (`http-upload.md` §1.3). Part of this milestone.

### 4.4 Size: M-L
The syncer, the endpoints and their tests, `storage: upload|auto` in `config.py` and `_Setup`, the nginx
location, a compose overlay, the CI end-to-end test with a `qa` container that doesn't mount the volume, docs
(the GitHub-hosted runner tutorial; `start_run()` "two lines, no shared storage"). It also removes the RWX volume
from Kubernetes runner pods, as a later option.

---

## 5. Milestones
S ≤ 2 days, M ≈ 1 week, L 2-3 weeks, for one engineer or agent workstream with review.

| # | Milestone | Content | Size | Unlocks |
|---|---|---|---|---|
| 0 | **`start_run()` merges** | The branch, as it is | — | Runs from Python, series |
| 1 | **`qa upload` on shared storage** | §3: the command, `qaboard/junit.py`, recognized files on cards, the project's name in CI | S | Test reports, benchmarks, profiles from any CI job that sees the storage |
| 1b | **Wrap a command, nest runs** | `qa upload -- cmd`, `QABOARD_RUN_DIR` (§2.3.3), one word for batches (§2.3.1) | S | `qa run` without an entry point; SDK calls inside `qa run` |
| 2 | **HTTP upload, small files** | §4, with the heartbeat and `qa sync <run folder>` (§2.3.5) | M-L | GitHub-hosted runners, laptops, notebooks, pods without the volume |
| 3 | **Large outputs over HTTP** | `http-upload.md`'s parts, dedupe, content store, bit-accuracy references through the API | L | Image and video projects without shared storage |
| 4 | **Object storage, project-paid buckets** | §6 | XL | Kubernetes without RWX volumes; projects paying for their bulk |

1 → 1b → 2 is the path. 3 and 4 wait for a project that needs them. The SDK's own follow-ups (the Runs page,
`log_image()`, system metrics) are in `docs/rfcs/python-sdk-runs-follow-ups.md` and independent of these, except
where §2.3 joins them.

---

## 6. Where the bytes live, and who pays
Unchanged from v1.

### 6.1 The principle: the server owns the index
Whatever the storage, the server keeps: the database (metrics, statuses), every run's manifest with sha256,
`log.txt`, the series, files under `QABOARD_INLINE_MAX` (default 1 MiB: test reports, benchmark files, folded
stacks, small JSON and text), and derived data (thumbnails, Cantaloupe's cache). Only bulk files go to the
project's storage. So history, the gate, the test and benchmark viewers' comparisons and bit-accuracy checks
(manifest against manifest) work without the project's bucket, and after its retention deleted the bulk; the
operator pays for a small, predictable index, projects for their bulk.

### 6.2 Options for project-paid storage
| Option | How | Against | Verdict |
|---|---|---|---|
| **A. Bring-your-own bucket** (S3-compatible, GCS, Azure Blob) | A project admin registers a storage profile. Uploads get presigned `PUT` URLs; reads go through `/s/` (§6.3). Credentials by role assumption (AWS), workload identity federation (GCP) or federated credentials (Azure); static keys, encrypted, only for on-prem S3 | Per-cloud credential code; egress is the project's when files are read | **Recommended** |
| **B. CI artifact stores** | A pointer per run, fetched on read | GitHub serves whole zips; needs a CI token per project; files vanish with CI retention | Not as storage. Maybe an import later |
| **C. OCI registry** (ORAS: GHCR, Harbor, Artifactory) | A run's files as an OCI artifact, blobs by sha256 | Range reads and browsing vary by registry | A spike where Artifactory is the only sanctioned store |
| **D. Server-managed** (filesystem, or the operator's bucket) | Today | The operator pays for everything | The default; A is opt-in per project |

S3's "Requester Pays" is about who pays for downloads, not storage: it doesn't help here.

### 6.3 Serving files from anywhere: one resolver
- A run records where its bulk lives: `Output.data.storage = {profile, prefix}`; absent means the filesystem.
- `/s/<path>` keeps its URLs. nginx's `auth_request` asks the backend, which answers with `X-Accel-Redirect` to
  the local alias, or to an internal location proxying a presigned URL of the right bucket (same origin, Range
  works, no CORS). Large videos can get a `302` to the presigned URL.
- Cantaloupe: `S3Source` with a delegate script mapping the identifier to the profile's bucket and key.

### 6.4 Open source to build on
| Need | Pick | Notes |
|---|---|---|
| One API over many storages (put, ranged get, delete, list, presign) | **Apache OpenDAL** (Python bindings; S3-compatible, GCS, Azure, local, WebDAV, SFTP, HDFS…) | **obstore** if the cloud object stores are enough; **fsspec** is slower and presigns per backend. Check presign support per service first. Keep our `BlobStore` to five calls so the library stays swappable |
| Self-hosted S3 for the quickstart, Kubernetes, on-prem | **Garage** or **SeaweedFS**; **Ceph RGW** where Ceph exists | **MinIO**'s community edition stopped shipping binaries in Oct 2025 and is no longer maintained (2026): not the default (`http-upload.md` question 11; the public roadmap still names it) |
| S3 API over an existing NFS | **Versity S3 Gateway** | One code path for shared-storage sites, if ever wanted |
| Exotic storages without code (SharePoint, SMB, SFTP) | **rclone** `serve s3` / `serve webdav`, run by the site | |
| Resumable uploads | Our own parts (milestone 3) | tus: rejected in `http-upload.md` §4.2, still true |

---

## 7. Open questions
1. **Batch, label or experiment** (§2.3.1): one word in the APIs, and which?
2. **Nesting** (§2.3.3): attach when `QABOARD_RUN_DIR` is set and no identity is given. Is "an explicit
   `input=` starts a separate run" the right rule, or should attaching be explicit (`qb.current_run()` only)?
3. **Recognized files on cards** (§2.3.4): for uploaded and SDK runs only (proposed), or for every run whose
   project lists no visualization for the file?
4. **`qa upload` default:** a new attempt (proposed), `--append` to add.
5. **Default input with several paths:** the CI job's name (proposed), or require `--input`?
6. **Per-file limit for milestone 2:** 256 MiB, or 1 GiB?
7. **`outputs.live` default:** nothing (proposed), or small text files?
8. **First project-paid provider:** S3-compatible first; is GCS or Azure needed for an actual user?
