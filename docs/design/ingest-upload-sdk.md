# Sending results without `qa run`: `qa upload`, HTTP upload, an SDK, and where the bytes live

Status: proposal v1 (2026-10-26), for review. Revises the phasing of `docs/design/http-upload.md` (v1, 2026-10-12)
and places it in a larger picture: three ways to create runs (`qa run`, `qa upload`, an SDK), one client, and
storage that can live on the server, on shared storage, or in the user's own bucket.

## TL;DR
- **One run client, three front-ends.** A small Python client (`qaboard.ingest.Run`: start, add files, log,
  finish) is the only code that talks to the server and to storage. `qa upload` (new), the SDK (later) and
  eventually `qa run` all use it. The transport behind it (copy to shared storage, HTTP upload, a bucket) is
  a detail of the client, so every front-end gets every transport.
- **Milestone 1 is `qa upload`, on today's shared storage.** No entry point, no `run()`:
  `qa --label unit-tests upload --each 'build/test-results/*.xml'` makes one run per test suite, with metrics
  read from the JUnit reports. It writes the same folders, manifests and database rows as `qa run`, so every
  viewer, the history, the gate and bit-accuracy work unchanged. No server change.
- **Milestone 2 is HTTP upload, cut down to option A of the current design:** one `PUT` per file (≤ 256 MiB),
  sha256-verified, token-required, written by the server at the paths `qa run` would use, plus the live log
  endpoint. That covers test reports, benchmarks, logs, metrics and most images. Parts, packs, the content
  store with hardlinks, sessions and async commits wait for a project that actually has huge outputs and no
  shared storage (milestone 5).
- **Live results are "upload early", not a new mechanism.** `log.txt` streams; files matching `outputs.live`
  globs (and files the SDK saves) upload as soon as they're stable; everything else at the end. A run killed
  mid-way keeps what was uploaded, and its logs: that's what people need to debug it.
- **The server owns the index, storage owns the bytes.** Metrics, manifests (with sha256), logs, small files
  and derived data (thumbnails, test summaries) always live on the server. Bulk files can live in a bucket the
  project pays for. Analysis keeps working even when the bucket's retention deleted the bulk.
- **Bring-your-own bucket** (milestone 6): per-project storage profiles (S3-compatible, GCS, Azure), credentials
  by role assumption or workload identity federation rather than stored keys, presigned uploads, reads through
  `/s/` with `X-Accel-Redirect`. A storage library (Apache OpenDAL or obstore) gives the backends.

---

## 1. What changes from the current HTTP upload proposal
The current proposal (`http-upload.md`) is sound on paths, security and readers, and most of it stays. What changes:

| Topic | `http-upload.md` v1 | This proposal |
|---|---|---|
| Who creates runs | `qa run` / `qa batch` only | `qa run`, `qa upload`, an SDK, through one client (§2) |
| First deliverable | Security baseline (M), then upload core (L) | `qa upload` on shared storage (S-M), no server change. Most of the security baseline has shipped since (§6) |
| Protocol, first version | Sessions, content-addressed blobs, parts, packs, commit | One `PUT` per file, a `finish` call, the log endpoint (§4). Same paths, same tokens |
| Dedupe, hardlink content store, async commit, ingest pool | Phase 2b | Milestone 5, when a project needs it |
| Live files | Non-goal: only `log.txt` | `log.txt` plus opt-in globs and SDK saves (§5) |
| Object storage | Server-managed bucket (phase 3) | Server-managed or the project's own bucket, behind one resolver (§7) |

Why cut the protocol: the projects asking for upload first send JUnit reports, benchmark JSON, logs, metrics
and a few images, from a few KB to tens of MB per run. Parts, packs and dedupe matter for 600 MB TIFFs and 8 GB
videos from bit-accurate ISP runs, and those projects have shared storage today. Option A's endpoint is a strict
subset of the full protocol: we can add parts and "which hashes do you have" later without breaking clients.

---

## 2. One model, one client

### 2.1 What a run is, whoever creates it
- **Identity:** project, commit, batch label, input (the test's name), configurations, platform. Same as today:
  `POST /api/v1/output/` already gets-or-creates the `Output` from these (`backend/backend/api/outputs.py:129-249`).
- **Lifecycle:** running, then finished or failed. A re-run with the same identity replaces the previous one.
- **Content:** metrics (a dict, final), files (a folder with `manifest.outputs.json`), `log.txt`.

Nothing here is specific to `qa run`: `qa run` is one way to fill it in.

### 2.2 The client
A new module, `qaboard/ingest.py`, internal in milestone 1 and public with the SDK:
```python
from qaboard.ingest import Run

run = Run.start(batch="unit-tests", input="codec", configurations=["debug"])  # project, commit, platform: resolved
run.add_file("build/test-results/codec.xml", as_="junit.xml")
run.add_dir("build/bench/", prefix="bench")
run.log_metrics({"tests": 249, "failed": 3})
run.finish()          # writes the manifest, sends the final metrics and status
```
- **Resolution of the identity**, in order: arguments, environment (`QA_BATCH_LABEL`, …), qaboard.yaml, CI
  variables (`GITHUB_SHA`, `CI_COMMIT_SHA`, … as `qaboard/config.py` already does), git.
- **The transport** is chosen once, by the existing and planned storage settings: `direct` (copy into the shared
  storage, milestone 1), `upload` (HTTP, milestone 2), later a bucket (milestone 6). Front-ends never see it.
- **Nesting:** `Run.start` exports `QABOARD_RUN_ID` (the output id) and `QABOARD_RUN_DIR`. A `Run.start()` in a
  child process with `QABOARD_RUN_ID` set attaches to that run instead of creating one, like `WANDB_RUN_ID`. So
  `qa upload -- pytest` with the SDK inside the tests, or the SDK inside a `qa run` entry point, make one run.
- **`qa run` moves onto it later** (milestone 2, when it needs the upload transport anyway), not in milestone 1:
  `qa run` has years of behaviours (tuning, redo, `--keep-previous`, Windows paths) we shouldn't risk for a new command.

---

## 3. Milestone 1: `qa upload` on shared storage

### 3.1 Commands
```bash
# one run: files and folders keep their relative paths
qa --label unit-tests upload --input core build/test-results/core.xml build/coverage/

# one run per suite: the input is the file name without its extension (or the folder's name)
qa --label unit-tests upload --each 'build/test-results/*.xml'

# micro-benchmarks, with metrics
qa --label bench -c O3 upload --input varint bench.json -m throughput_mbps=812.5 --metrics extra.json

# rename a file, mark the run failed
qa upload --input smoke out.xml --as junit.xml --failed
```
| Option | Meaning |
|---|---|
| `PATHS…` | Files, folders (recursive) or globs. Stored relative to the common parent, or to `--root` |
| `--input/-i NAME` | The run's name, as in `qa run -i`. Default: the single path's stem |
| `--each` | One run per path; the input is each path's stem. With `{dir}` / `{stem}` patterns later if needed |
| `--as NAME` | Store a single file under this name, e.g. `junit.xml` so the viewer and its docs apply as-is |
| `-m key=value`, `--metrics FILE` | Metrics; JSON numbers, booleans and strings |
| `--failed` | The run failed (couldn't produce results), as `is_failed` |
| `--append` | Add files to an existing run instead of replacing it (e.g. coverage from a later job) |
| Global options | `qa --label`, `-c/--configurations`, `--platform` keep their meaning. `qa upload` always shares, like CI |

### 3.2 Built-in readers, no configuration
When an uploaded file is a JUnit XML report (by name, as the viewer recognizes them, or by sniffing the root
element), `qa upload` reads it and adds metrics: `tests`, `passed`, `failed`, `errors`, `skipped`, `flaky`,
`time_s`. Explicit `-m` wins. That's the whole value for the first use case: history, dashboards and the gate on
test counts with zero code. Reader code is ~50 lines over `xml.etree`, shared with `examples/software-quality/qa/junit.py`.

Benchmark formats (Google Benchmark JSON, pytest-benchmark, hyperfine, criterion) come later, one small reader
each, when someone uploads them: a reader is a function from a file to metrics, registered by name. Not in
milestone 1 beyond the registry.

### 3.3 What it writes
Exactly what `qa run` writes, at the same place:
`<storage>/<project>/<sha[:2]>/<sha[2:16]>/<subproject>/output/<batch>/<config hash>/<input>/`, with the
files, `metrics.json`, `manifest.outputs.json` (`qaboard/utils.py` `save_outputs_manifest`), a `log.txt` that
records the command line, and `POST /api/v1/output/` with `is_running: false`. So:
- the file browser, viewers, Cantaloupe, comparisons with the reference run, the history, the gate, `qa check-bit-accuracy` work;
- `qa batch` and `qa upload` runs mix in one batch.

### 3.4 Project setup
- **With a qaboard.yaml** (SIRC projects): nothing to add; the site package gives the storage.
- **Without one**: `qa upload --project group/repo` or `QA_PROJECT`, else the git remote's path
  (`origin` → `group/repo`) or `GITHUB_REPOSITORY` / `CI_PROJECT_PATH`; storage from the site package or `QA_STORAGE`.
  This needs `qaboard/config.py` to tolerate a missing qaboard.yaml for this command (it exits at import today,
  `config.py:63-75`): that refactor is the riskiest part of milestone 1, and the reason it's S-M rather than S.

### 3.5 What milestone 1 doesn't do
- Machines without the shared storage: `qa upload` fails with "this machine doesn't see the storage at X:
  needs HTTP upload (QA-Board ≥ …)". Milestone 2 removes the limit with no change to the command.
- Wrapping a command (`qa upload -- ctest …`): milestone 2, with the live log (§5), because that's where it pays off.

### 3.6 Size: S-M
`qaboard/ingest.py` (direct transport only), `qa upload` in `qaboard/qa.py`, the JUnit reader, the config
refactor for projects without qaboard.yaml, tests in `tests/` (green) including a run whose output is
byte-identical to a `qa run`'s, a docs page ("Upload results without an entry point" in Project integration,
linked from the test viewer and software-quality pages), a release note.

---

## 4. Milestone 2: HTTP upload, small files first

### 4.1 Endpoints
All need a token with the `ingest` scope for the project (exists: `backend/backend/api/auth.py:344` `ingest_access`).
| Request | Body | Server |
|---|---|---|
| `POST /api/v1/output/` | today's payload, `"storage": "upload"`, the **relative** run path | Builds the absolute path under `QABOARD_UPLOAD_ROOT`; moves the previous run's folder to trash; returns `id` and upload limits |
| `PUT /api/v1/output/<id>/files/<rel path>` | the file, `Repr-Digest: sha-256=…`, optional `Content-Encoding: gzip` | Validates the path (rules of `http-upload.md` §6.4), streams to a temp file in the run folder, checks size and sha256, renames into place. Same path and hash again: `200`, nothing written |
| `POST /api/v1/output/<id>/log?offset=n` | log bytes | Appends at `offset` (`http-upload.md` §4.3) |
| `POST /api/v1/output/<id>/finish` | metrics, status, the manifest (`path → size, sha256, md5…`) | Checks every manifest file is present with its hash, deletes files not in it, writes `manifest.outputs.json`, sets the status |
| `GET /api/v1/config` | | Adds `uploads: {enabled, max_file, encodings}` |

- **Limits:** 256 MiB per file (nginx `client_max_body_size` on that location only), 10 GiB and 100k files per run,
  per-token `429`. Larger files get `413` with "needs shared storage or QA-Board ≥ M5". Projects hitting it are
  the signal to build milestone 5.
- **Visibility:** files appear as they arrive, in a run marked running. There's no folder swap: readers already
  handle running runs, and seeing files early is the live feature (§5). A run is *finished* only at `finish`, after
  verification.
- **Idempotency** comes from the path and the hash: a retried `PUT` or `finish` is harmless. No session table.
- **Lost runs:** a running upload-mode run with no request for 30 min (log appends are the heartbeat) is marked
  failed with `data.error = "lost: the client stopped sending"`, by the existing cleanup schedule.

### 4.2 CLI
- `storage: upload` / `upload: auto|always|never` and `QA_UPLOAD`, as in `http-upload.md` §3.1, including the
  marker file for `auto`. The client's `upload` transport implements them; `qa upload` gets HTTP for free.
- `qa run` moves onto the client here: staging folder (`http-upload.md` §3.2), log streamer, finish.
- `qa upload -- <command>`: starts the run, tees the command's output to `log.txt` (streamed), uploads the paths
  given (globs evaluated after the command), finishes; exit code = the command's, `--failed` when it crashed
  without writing the paths. That's `qa run` without an entry point, for test runners and benchmarks.
- 4 upload threads, retries with backoff, `QA_UPLOAD_ERRORS=fail|warn` (fail in CI).

### 4.3 Entry criteria: the rest of the security baseline
Since `http-upload.md`, scoped and project-limited tokens, `ingest.auth = off|warn|token` and the sandboxed
HTML viewer have shipped. Before uploads face the internet, check what's still open from its §1.3 and §6.4;
in this tree: `/iiif/` has no `auth_request`, `/s/` sends no `X-Content-Type-Options: nosniff` nor a sandbox CSP
for HTML/SVG, plus the image APIs' path checks and the manifest `?refresh` rewrite. These are M-sized together
and part of milestone 2.

### 4.4 Size: M-L
Endpoints and tests (path validation, digests, limits, idempotency, lost runs), the upload transport, `qa run` on
the client, `qa upload -- cmd`, nginx location, compose overlay, the CI e2e from `http-upload.md` §11 (a `qa`
container without the volume), docs: the GitHub-hosted-runner tutorial step.

---

## 5. Live results
What people want while a run runs: its log, its progress metrics, and sometimes a plot or a first image. What
they need when it dies: whatever it produced up to then. One rule covers both: **upload early what's useful
early; the end-of-run sync makes it exact.**

| What | When | How |
|---|---|---|
| `log.txt` | every 2 s or 1 MiB | the log endpoint, as today's web app polls it with Range (`webapp/src/components/logs/useLogTail.js`) |
| Running metrics | on `run.log_metrics()` / every 10 s | `POST /api/v1/output/` with `is_running: true` and the metrics, as today |
| Step metrics (SDK `run.log({...}, step=)`) | buffered, every 10 s | appended to `metrics.jsonl` like the log; a plot viewer reads it. No schema change |
| Files matching `outputs.live` globs (qaboard.yaml) or saved with `run.save(path)` | once stable: same size and mtime for two 2-s polls | `PUT`, re-sent if the hash changes |
| Everything else | at `finish` | `PUT` of what's missing, then `finish` deletes what the final manifest doesn't list |

- **Default `outputs.live`: none.** Logs and metrics are live by default, files are opt-in: re-uploading a file
  the code rewrites every minute costs bandwidth nobody asked for. Projects add `["*.json", "plots/*.png"]`.
- **Shared storage needs none of this:** files are visible as written, as today. The rules only matter in upload mode.
- **Crash or kill:** the run keeps its live files and log; `qa`'s exit handler sends `is_failed` when it can, and
  the lost-run sweep does otherwise. `qa upload --pending` (milestone 2) resumes runs whose final sync failed
  from the local staging folder.

---

## 6. Milestone 3: the SDK
```python
import qaboard

run = qaboard.init(batch="training", input="resnet50-imagenet", config={"lr": 3e-4})  # attaches to QABOARD_RUN_ID if set
for step in range(n):
    run.log({"loss": loss, "acc": acc}, step=step)
run.log_image("confusion.png", fig)           # saved into the run folder, uploaded when stable
run.save("checkpoints/best.onnx")             # any file
run.finish()                                  # also atexit, failed on an uncaught exception
```
- A thin public layer over `qaboard.ingest.Run`; nothing new on the server beyond milestone 2.
- `config=` maps to QA-Board's configurations (`extra_parameters` for dicts), so tuning and comparisons keep working.
- `qaboard.init()` without `QABOARD_RUN_ID` and without CI behaves like `qa run` locally: writes `./output/`
  and doesn't share, unless `share=True` or `QA_SHARE=1`. Same rule as today, no surprise uploads from laptops.
- Later, separately: a `pytest` plugin (`--qaboard`) that uploads the JUnit report and per-test durations, and a
  `wandb.log` shim. Not part of milestone 3.
- **Size: M.** The hard design work (identity, nesting, transport) is done in milestones 1-2.

---

## 7. Where the bytes live, and who pays

### 7.1 The principle: the server owns the index
Whatever the storage, the server keeps: the database (metrics, statuses), every run's manifest with sha256,
`log.txt`, files under `QABOARD_INLINE_MAX` (default 1 MiB: JUnit reports, metrics, small JSON and text), and
derived data (thumbnails, Cantaloupe's cache, parsed test summaries). Only bulk files go to the project's
storage. Consequences:
- history, dashboards, the gate, the test viewer's comparisons, and bit-accuracy checks (manifest against manifest,
  `http-upload.md` §7) work without touching the project's bucket, and keep working after its retention deletes the bulk;
- the web app shows "file expired from the project's storage" rather than broken runs;
- the server's own disk stays small and predictable: the operator pays for the index, projects for their bulk.

### 7.2 Options for project-paid storage
| Option | How | For | Against | Verdict |
|---|---|---|---|---|
| **A. Bring-your-own bucket** (S3-compatible, GCS, Azure Blob) | A project admin registers a storage profile: provider, bucket, prefix, and credentials. Uploads get presigned `PUT` URLs; reads go through `/s/` (§7.3) | Standard, fast, Range reads, lifecycle rules for retention, Cantaloupe has an `S3Source` | Credentials management; egress costs land on the project when files are read | **Recommended** (milestone 6) |
| A's credentials | **Role assumption** (AWS `AssumeRole` with an external id), **workload identity federation** (GCP), **federated credentials** (Azure): the project grants QA-Board's identity access; no secret stored. Static keys, encrypted at rest, only for on-prem S3 | No long-lived secrets in our database | Per-cloud code paths | Federation first, keys as fallback |
| **B. CI artifact stores** | Store a pointer per run (job id, path); fetch on read, cache | Zero setup, retention is the CI's | GitLab can serve one file of a job's artifacts (`GET /projects/:id/jobs/:job_id/artifacts/*path`); GitHub Actions artifacts download as a whole zip. Needs a CI token per project; slow first reads; files vanish with CI retention | Not as storage. Possibly as an import ("link this job's report") later |
| **C. OCI registry** (ORAS: GHCR, Harbor, Artifactory) | Each run's files pushed as an OCI artifact; blobs by sha256 | Content-addressed and deduped; enterprises already run and pay for a registry; their auth and retention | Range reads and browsing vary by registry; every read needs our manifest; images go through a fetch-and-cache | Worth a spike where Artifactory is the only sanctioned store. Not first |
| **D. Server-managed storage** (filesystem, or a bucket the operator owns) | Today, and milestone 5's object store | Simplest for users | The operator pays for everything | The default; A is opt-in per project |

S3's "Requester Pays" is about who pays for downloads, not storage: it doesn't help here.

### 7.3 Serving files from anywhere: one resolver
- Every run records where its bulk lives: `Output.data.storage = {profile, prefix}`; absent means today's filesystem.
- `/s/<path>` keeps its URLs. nginx's existing `auth_request` asks the backend, which answers with
  `X-Accel-Redirect` to: the local alias (today), or an internal location that proxies to a presigned URL of the
  right bucket (same origin, Range works, no CORS). This is `http-upload.md` §10.2, generalized to many buckets.
- Large videos can be answered with a `302` to the presigned URL instead, so the project's bucket serves the bandwidth.
- Cantaloupe: `S3Source` with a delegate script mapping the identifier to the profile's bucket and key, for
  S3-compatible profiles; other providers go through a fetch into its source cache. The backend's pixel and ROI
  APIs read through the same cache.

### 7.4 Open source to build on
| Need | Pick | Alternatives and notes |
|---|---|---|
| One API over many storages in the backend (put, ranged get, delete, list, presign) | **Apache OpenDAL** (Python bindings; S3-compatible, GCS, Azure, local, WebDAV, SFTP, HDFS and more) | **obstore** (Python over Rust's `object_store`: S3, GCS, Azure, local, HTTP; fast, signing built in) if the cloud object stores are enough; **fsspec** (s3fs, gcsfs, adlfs) is the Python ecosystem's standard but slower and presigning is per-backend. Check presign support per service before committing to OpenDAL. Keep our `BlobStore` interface to five calls so the library stays swappable |
| Self-hosted S3 for the quickstart, Kubernetes and on-prem | **Garage** (small, simple) or **SeaweedFS**; **Ceph RGW** where Ceph exists | **MinIO** community edition stopped shipping binaries and images in Oct 2025 and its repository is no longer maintained (2026): don't make it the default (`http-upload.md` question 11) |
| S3 API over an existing NFS (one code path for SIRC) | **Versity S3 Gateway** (S3 over a POSIX filesystem) | Lets the bucket transport serve shared-storage sites too, if we ever want to drop the direct transport. Not needed before milestone 6 |
| Exotic storages without code (SharePoint, SMB, Google Drive, SFTP) | **rclone** `serve s3` / `serve webdav` in front of them, operated by the site | Zero code for us; the site runs one more service |
| Resumable uploads | Our own parts (milestone 5) | **tus/tusd**: rejected in `http-upload.md` §4.2 (no dedupe, a new service); still true |
| Versioned data lakes (lakeFS, DVC, git-annex) | None | They version datasets; we need an index of run outputs, which we already have |

---

## 8. Milestones
S ≤ 2 days, M ≈ 1 week, L 2-3 weeks, for one engineer or agent workstream with review.

| # | Milestone | Content | Size | Unlocks |
|---|---|---|---|---|
| 1 | **`qa upload` on shared storage** | `qaboard/ingest.py` (direct transport), `qa upload` with `--each`, `--as`, metrics, `--append`; JUnit reader; projects without qaboard.yaml | S-M | Test reports, benchmarks, any files from existing projects, from any machine that sees the storage |
| 2 | **HTTP upload, small files** | §4: per-file `PUT`, `finish`, log endpoint, lost runs, `storage: upload\|auto`, `qa run` on the client, `qa upload -- cmd`, live log and live globs (§5), remaining security baseline | M-L | GitHub-hosted runners, laptops, pods without a shared volume |
| 3 | **SDK** | §6: `qaboard.init/log/log_image/save/finish`, `QABOARD_RUN_ID` nesting, `metrics.jsonl` plot | M | Training loops, notebooks, tests calling QA-Board from inside |
| 4 | **More readers** (as asked) | Google Benchmark, pytest-benchmark, hyperfine, criterion; a pytest plugin | S each | Benchmarks and tests with zero code |
| 5 | **Large outputs over HTTP** | `http-upload.md` parts, "which hashes do you have" dedupe, content store, bit-accuracy references via the API, ingest uwsgi pool | L | Bit-accurate image/video projects without shared storage |
| 6 | **Object storage and BYO buckets** | `BlobStore` on OpenDAL/obstore, server-managed bucket first, then per-project profiles with federated credentials, the `/s/` resolver, Cantaloupe `S3Source`, inline small files on the server | XL | Kubernetes without RWX volumes; projects paying for their own bulk storage |

Milestones 1 → 2 → 3 is the path; 4 runs alongside whenever a format is asked for; 5 and 6 wait for a real
project that needs them. Milestone 1 can ship this month and is useful on its own.

---

## 9. Open questions
1. **`qa upload` replaces or merges by default?** Proposed: replace (like a re-run), `--append` to merge.
   Merging by default would hide stale files from a previous upload.
2. **Run name for `--each`:** file stem, or the parent folder for `TEST-*.xml` (Surefire, Gradle) where stems
   are class names? Proposed: stem, with `--input '{parent}'`-style patterns if people ask.
3. **Per-file limit for milestone 2:** 256 MiB enough for your non-ISP projects, or 1 GiB?
4. **`outputs.live` default:** none (proposed), or small text files under 1 MiB?
5. **Inline threshold** (§7.1): which files always stay on the server once buckets exist? 1 MiB, plus every
   file a viewer needs for comparisons (test reports, manifests)?
6. **First BYO provider:** S3-compatible (covers AWS, R2, Ceph, Garage, SeaweedFS) is the obvious first; is
   GCS or Azure needed for an actual user?
7. **OCI registries (§7.2 C):** is Artifactory the only store some teams may use? If so, a spike before milestone 6.
