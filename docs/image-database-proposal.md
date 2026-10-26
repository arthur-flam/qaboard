# Proposal: a native image database in QA-Board

Status: proposal v1 (2026-10-26), for discussion. Nothing here is implemented.
Line references point to this branch's tree.

## TL;DR
- **We have tried this before, more than once, and the technology was never what killed it.** The projects
  failed on adoption (a desktop app nobody could install), on lock-in (data you can only reach through the
  tool, formats the tool can't preview), on data models that don't fit (a list of runs stored in each image
  document), and on conventions that die when the person pushing them leaves.
- **Recommendation: "index, don't ingest", built into QA-Board.** The files stay on the IT shared storage, in
  plain folders, and they remain the source of truth. Metadata lives in **sidecar files next to the images**.
  Postgres only holds an **index** that can be rebuilt from the files. If the application dies, the data is
  still usable as plain folders and files: that is the antidote to "a new project every two years".
- **Day 1 does not start empty.** QA-Board already knows every input that every run used (`test_inputs`),
  with metadata from `metadata()`/`iter_inputs()`. Indexing those first gives a useful catalogue on the first
  day, before anyone has uploaded or tagged anything.
- **Five surfaces over one model:**
  1. a FiftyOne-style web grid (facets, tags, bulk select, the image viewer we already have);
  2. a Python API (`qaboard.images`);
  3. `qaboard.yaml` batches that point at a **query or a pinned collection**, a native replacement for
     `only`/`exclude` over external databases;
  4. collections **materialized as folders**: symlink farms first, a mountable filesystem later, if people
     actually ask for it;
  5. **ingest recipes**: Python functions in a git repo whose typed parameters become a web form. They
     cover upload, sidecar generation (white balance and the like), metadata extraction and thumbnails for
     custom formats.
- **Don't try to out-FiftyOne FiftyOne.** Its strengths are labels, embeddings and model evaluation. Ours
  would be: files first, custom raw formats, sidecars, a multi-user server with authentication, the link to
  runs, and folder views. Offer `view.to_fiftyone()` for the people who want its ML tooling.
- **Phases:** 0) browse what QA-Board already knows (S); 1) storage roots crawler, sidecars, tags, Python
  API, YAML integration (L); 2) ingest recipes, materialized collections, sidecar history (M-L);
  3) optional extras: mounts, semantic search, FiftyOne export (M each).

---

## 1. Why the previous attempts failed (and what each one teaches)

| Attempt | What went wrong | Design rule we take from it |
|---|---|---|
| Desktop application | Nobody figured out how to install it, and the UX was poor | Web only. Zero install for people who browse. `pip`/`uv` only for scripting |
| Commercial DAM (web, tag search) | Thumbnails for our custom formats were hard to get. Once the data was in, it could not be exposed as plain folders. We were an awkward customer for the vendor | We own the decoders through plugins. Files stay plain files. No vendor relationship to maintain |
| Internal "idb" (hash-tracked, MongoDB) | No web UI to look at images. Each image document held an array of the runs it appeared in, so the data model doesn't support real queries | Relational joins (image ↔ run), not embedded arrays. The UI ships first, not last |
| Folder naming conventions | A new convention every two years, then it fades | Don't rely on paths carrying the meaning. Metadata belongs to the file (sidecar, hash), wherever it is |

There is a pattern we should be honest about: **every attempt had a champion and no forcing function.**
When the champion moved on, nobody kept tagging, and the database decayed. The only defence that has
worked elsewhere is to put the tool on a path people **already walk**. For us that path is QA-Board batches:
if the image database is how you declare what a batch runs on, it gets used and kept up to date as a side
effect of normal work. If it is a separate destination, it will repeat the history above.

## 2. What QA-Board already has (verified)

- **Inputs are already in the database**, but barely modelled. Table `test_inputs`
  (`backend/backend/models/TestInput.py`) has `(database, path)` unique, plus `data.metadata` (JSON). It has no
  hash, size, mtime, type or project. Metadata is overwritten by the last run that posted it
  (`backend/backend/api/outputs.py:179-194`). No API lists or queries inputs.
- **Metadata hooks exist on the CLI side.**
  - `metadata(abs_path, database, rel_path)` and `iter_inputs(...)` in the user's entrypoint
    (`qaboard/iterators.py:238`, `qaboard/utils.py:251`).
  - `only`/`exclude` with wildcards, comparisons and dicts (`iterators.py:126`).
  - There is even a comment asking whether to send this metadata "to /our/ database" (`iterators.py:252`).
- **Run inputs are hashed:** `manifest.inputs.json` stores the md5 of each input file (`qaboard/qa.py:337-360`).
- **Image serving is mostly there:**
  - Cantaloupe IIIF (`services/cantaloupe/`), with an image server chosen per file extension
    (`QABOARD_IMAGE_SERVERS`, `webapp/src/viewers/images/utils.js:2-22`).
  - Pixel values decoded server side, with the internal raw reader when it's installed (`backend/backend/images.py`).
  - A standalone OpenSeadragon viewer with comparison modes, histograms and ROIs (`webapp/src/image-viewer/`).
  - Missing: a thumbnail pipeline and a decoder for custom formats that Cantaloupe can use. No Cantaloupe
    delegate script exists.
- **The pieces an app needs are there:** authentication, users and permissions, Celery workers, the storage
  roots checks (`backend/backend/storage.py`), the file browser (`/s/`), and an existing Python client
  (`qaboard/api.py`).
- **The idb integration** (`qaboard/idb.py`) pushes output image md5s to the internal image database. It's a
  migration source and a lesson, not a base.
- **Roadmap:** "Dataset slices" (metrics per slice of inputs) is listed as exploring
  (`website/docs/roadmap.mdx:88`). It falls out of this design almost for free.

## 3. Requirements, reordered by what actually matters

**Must have**
1. Find images that already exist ("we're about to shoot this for the 10th time"). Search by tags, dates,
   sensor and capture settings, project, free text, and "used in which runs".
2. Look at them in a browser: thumbnails for our custom formats, plus a full viewer with pixel values.
3. Get a selection out as **a folder**, **a Python list**, or **a QA-Board batch**.
4. Add images easily, with metadata filled automatically, and with project-specific sidecars (white balance)
   produced by custom scripts that take parameters from the user.
5. Storage stays on the IT shared storage, as plain files you can edit.

**Should have**
- Saved, versioned collections, so a batch is reproducible even when its query would now match more images.
- History of sidecar edits, and "this run used white balance v2".

**Not now**
- Full revision control of image data. Nobody has reported it as a real pain point. Sidecar history (§5.6)
  covers the realistic need at almost no cost.
- Annotation and labelling tools. Use CVAT or Label Studio and import their outputs as sidecars.
- Object storage. The shared storage is cheaper for us, IT already deduplicates, and files stay editable.

## 4. Design directions

### Direction A — "Index, don't ingest" (catalogue over the existing storage) — **recommended**
The application never owns the images.
- Admins declare **storage roots** (`/datasets/...`, the per-project database folders already used in
  `inputs.database`). A crawler indexes them.
- Uploads are just a convenient way to **write files into a root, in a layout the app generates**.
- Metadata of record is in sidecars. The database holds a rebuildable index, plus the things that have no
  natural home in a file: tags and collections. Those are also exported to sidecars and manifests, so the
  "the app died" scenario loses nothing.
- (+) No migration: legacy folders become searchable as they are. Survives the app dying. Edit files with any
  tool. IT's deduplication and scaling are free.
- (−) Crawling NFS/SMB is slow (lots of metadata operations, no inotify over NFS), so scans must be incremental
  and scoped. Files get moved or renamed behind our back, so identity has to come from content hashes. And
  people can mess up sidecars by hand, so they need to be validated and the errors shown.

### Direction B — "Managed library" (the app owns a content-addressed store)
Upload everything into `<store>/<hash[:2]>/<hash>`. The database is the source of truth. Folders are only
ever views.
- (+) Clean: real deduplication, natural revisions, simple identity, ready for object storage.
- (−) This is the internal idb and the commercial DAM again. Everything has to be imported, and nobody knows
  where their files are without the app. It fights the "everything is a file, edit the sidecar" habit, which
  is the one thing users like about the current mess. **Not recommended.**

### Direction C — Adopt FiftyOne OSS and integrate
Run FiftyOne for the UI and Python API, and sync it with QA-Board.
- (+) A great UI and API on day one.
- (−) The open-source app is designed for one user: no multi-user authentication or permissions (those are
  in the paid product), and a MongoDB of its own. It has no knowledge of our raw formats without converting
  them to PNG/JPEG, and no folder views. It would also be a second system to deploy and keep in sync, and
  we would hit the same limitations that stopped us before.
- Worth keeping as an **export target** (`to_fiftyone()`), not as the foundation.

### Direction D — A standalone new application, outside QA-Board
Same model as A, as its own service.
- (+) Neutral branding, so teams that don't use QA-Board might adopt it more easily.
- (−) This is the failure pattern: a new app, its own authentication, deploy and on-call, and no forcing
  function. It also loses the image viewer, workers and file serving we already have.
- **Mitigation if neutrality matters:** keep the model and API separable inside QA-Board (its own blueprint,
  tables and Python package), with an "Images" entry in the sidebar that works without any project.

**Recommendation: A, inside QA-Board, with separable boundaries (the D mitigation) and C as an export.**

## 5. Proposed design (Direction A)

### 5.1 Core model: a *sample* is a group of files
An image is rarely one file. There is the raw file, its `.wb.json`, maybe a `.jpg` preview and capture
logs. As in FiftyOne's groups, the unit is a **sample**:

```
samples
  id, root_id, key (path of the primary file, relative to its root), kind (image | burst | video | folder)
  content_hash (of the primary file), size, mtime, width, height, format
  captured_at, created_at, indexed_at, missing_since (null while the file exists)
  meta JSONB (merged: extractors + sidecars + user fields), with a GIN index
  thumb_state, thumb_hash
sample_files      sample_id, role (primary | sidecar:<name> | preview | aux), relpath, hash, size, mtime
sample_locations  content_hash → other paths where the same bytes exist (copies, symlinks), for "this image is in 7 places"
tags              (sample_id, tag), who and when, also written to the sidecar
collections       id, name, owner, query (JSON) | null, frozen
collection_versions  collection_id, version, created_at, manifest: list of (sample_id, content_hash, sidecar hashes)
roots             id, path (linux/windows), project (optional), layout rules, extractors, scan schedule
sidecar_history   sample_id, role, hash, content (small text), changed_at, detected_by (scan | ui | api)
```

- Typed columns are only for what every image has. Everything else goes in `meta` JSONB: Postgres handles
  tens of millions of rows with GIN and expression indexes on hot keys.
- **Link to runs:** add `test_inputs.sample_id` (nullable) and `outputs.input_hashes`, taken from
  `manifest.inputs.json` when the run is posted. "Which runs used this image?" becomes a join, and so does
  "which runs used this image with the old white balance?". No arrays of runs in each image.
- **Identity:** `(root, key)` gives the location, `content_hash` gives the content. A moved file is detected
  as missing in one place and appearing elsewhere with the same hash. Its tags and history follow it.
- **Hashing large raw files over NFS is expensive.** Use size, mtime and a hash of the first and last MiB as
  a fast fingerprint, then compute a full BLAKE3 (or md5, to match the manifests) in the background with
  low priority.

### 5.2 Sidecars: the convention that has to outlive the app
One rule, enforced by tooling:

- `IMG_0042.raw` → `IMG_0042.raw.meta.yaml` holds the user metadata and tags, editable by hand.
- Other sidecars are `IMG_0042.raw.<name>.<ext>` (for example `.wb.json`). Each root declares the sidecar
  patterns it knows (`<stem>.wb.json` for legacy layouts).
- The app reads sidecars and also writes them when tags or fields change in the UI. A file edited by hand
  is picked up at the next scan of that folder, or immediately with `qa images rescan <path>`.
- **Why this matters:** grep, editors, scripts and any future tool can read the metadata without our
  database. The convention is the durable part. The application is replaceable.

### 5.3 Crawler and extractors
- **Scans are incremental**, per root. Walk the folders, then for each one compare its mtime and listing
  with the last scan. NFS doesn't support inotify, so use scheduled scans plus explicit `rescan` calls from
  uploads and the CLI. The existing `StorageScan` job is a model to reuse.
- **Extractors** are Python plugins, versioned in git like the rest of a project's QA code, and run on
  Celery or LSF workers. Each one declares the files it applies to and returns metadata fields, and can
  also produce a thumbnail or a decoded preview:

```python
# extractors/hm3_raw.py
from qaboard.images import extractor

@extractor(match="*.raw", version=3)
def hm3(sample):
    header = read_hm3_header(sample.primary)
    return {"sensor": header.sensor, "exposure_us": header.exposure, "iso": header.gain * 100,
            "captured_at": header.timestamp}

@extractor.decoder(match=["*.raw", "*.hex"])
def decode(path):            # numpy array, used for thumbnails, IIIF and pixel values
    return cde.image.read_image(path)
```

- Bump `version` to re-extract. The extractor version is stored per sample, so we know what is stale.
- **Thumbnails for custom formats** were a small issue with the vendor tool, and they would be a blocker
  for us. They are solved once, by the decoder. The decoder writes a JPEG/WebP thumbnail into a cache, and,
  for large images, a pyramidal TIFF that Cantaloupe already serves. The image viewer, pixel probes
  (`backend/backend/api/image.py`) and thumbnails then share one decoding path.

### 5.4 Ingest recipes (upload, sidecars, custom scripts with parameters)
This is the part that has been painful: people want to upload, run a project script with a few parameters
(white balance mode, illuminant, scene type), and get sidecars written next to their images.

```python
# recipes/lab_shoot.py
from typing import Literal
from qaboard.images import recipe, Path

@recipe(title="Lab shoot", root="lab", layout="{date:%Y/%m}/{scene}/{sensor}")
def lab_shoot(files: list[Path], scene: str, illuminant: Literal["D50", "D65", "A", "TL84"],
              wb_from_grey_card: bool = True):
    for f in files:
        wb = estimate_wb(f) if wb_from_grey_card else default_wb(illuminant)
        f.write_sidecar("wb.json", wb)
        f.meta.update(scene=scene, illuminant=illuminant)
```

- **The signature is the form.** Type hints become a web form (enums become dropdowns), and also CLI
  options for `qa images ingest lab_shoot --scene ...`. One definition gives a UI and a CLI.
- **The layout is generated, not a convention people have to remember.** The recipe decides where files
  land, so the next "new convention" is a one-line change, not a two-year project.
- **Large shoots don't go through the browser.** The main flow is: copy the files to a drop folder on the
  shared storage (people already do that), then "register" the folder from the UI or the CLI. Browser
  upload is for tens of files. The HTTP upload work (`docs/design/http-upload.md`) can be reused for the
  transfer later.
- **Recipes run on workers, as the user who asked**, with logs streamed like a run. A recipe is in fact a
  QA-Board run without a commit, so we can reuse that machinery instead of building a second job system.

### 5.5 Collections and how you get them out
**A query is live, a collection version is frozen.** Both are first class:

**Python API (FiftyOne-like):**
```python
from qaboard import images as qi
view = (qi.find(tags="outdoor", sensor="HM3", captured_at=">=2025-01")
          .exclude(tags="blurry")
          .sort_by("captured_at")
          .take(200, seed=0))
for s in view:
    s.path, s.meta["iso"], s.sidecar("wb.json")
c = view.save("night-hdr")        # creates night-hdr@1, a frozen manifest
qi.collection("night-hdr@1").materialize("/scratch/me/night-hdr")   # a folder of symlinks
view.to_fiftyone("night-hdr")     # optional export
```

**qaboard.yaml (native, replacing `only`/`exclude` for this use case):**
```yaml
inputs:
  database: images://          # the image database
night:
  inputs:
    collection: night-hdr@3    # pinned: reproducible, CI-safe
smoke:
  inputs:
    query: {tags: [smoke], sensor: HM3}   # live, fine for exploration
    limit: 20
```
- Implement it as a built-in `iter_inputs` (the hook at `qaboard/iterators.py:238` already exists). Paths
  resolve to the real files, so user `run()` code doesn't change at all, and the sample's `meta` becomes
  the run's `input_metadata`.
- **Warn when a batch uses a live query in CI**, since the reference run could then differ in its inputs.
  Pinned versions also fix the reproducibility question.

**Folders.** In order of cost:
1. **Symlink farm on the shared storage**: `collections/<name>@<v>/<original relative path>`. This is the
   cheapest option and works with every Linux tool and LSF job. Windows over SMB only follows symlinks if
   the NAS allows it ("wide links" or the equivalent): **ask IT, it's a setting on their side, not code on
   ours**. As a fallback, hardlinks work transparently over SMB when they're on the same volume. They do break
   when an editor saves through a temporary file and a rename, which is fine for read-only images and bad
   for sidecars.
2. **ZIP download** of small collections from the UI.
3. **A virtual filesystem, if 1 is not enough**: queries as paths (`/mnt/images/q/sensor=HM3/tags=night/`).
   On Linux, a FUSE daemon in Python (`pyfuse3`), read-only and backed by the API. If installing FUSE on
   clients is a problem, a userspace NFSv3 server (a small Go service on `go-nfs`) is mountable with no client
   install. I agree that WebDAV is the wrong choice for Windows: the built-in client is slow and has file
   size limits. Defer this until someone shows a workflow that a symlink folder doesn't cover; I expect
   most consumers are scripts on Linux.

### 5.6 Revisions, honestly scoped
- **Images:** no versioning. A changed hash means a new content version: the old one is recorded in the
  history, but not kept, so it's up to people to keep a copy if they need one.
- **Sidecars:** they are small, so the crawler stores **every version it sees** (`sidecar_history`). This
  gives "who changed the white balance, when, and what it was before" for almost nothing.
- **Runs:** keep the sidecar hashes along with the input hashes, then:
  - flag "inputs changed since the reference run" on the comparison page. That's a cheap explanation for
    bit-accuracy breaks that nobody can explain today;
  - offer "re-run the batches affected by this white balance change". This builds on the DAG proposal
    (`docs/dag-pipelines-proposal.md`) later.

### 5.7 The web UI
- **Grid**: virtualized thumbnails. The JUnit viewer already handles 50k rows, so the technique is proven
  in the codebase.
- **Facets** in a sidebar, with counts, generated from `meta` key statistics. Admins pick which keys
  show as facets per root.
- **Query bar** that shares its syntax with the Python API and YAML. Copy a view as Python or YAML.
- **Bulk select → tag, add to collection, run recipe, export**, plus "run a batch on these" (the forcing
  function, again).
- **Detail page**: the existing image viewer with pixel values, the sidecars (editable, schema-validated),
  the history, "used in N runs", with links to them, and copies elsewhere.
- **Selection in the URL** (`src/selection.js`), so views can be shared.
- **Dataset slices on the history pages** come almost for free: they are metrics grouped by any `meta`
  key or tag of the inputs.

## 6. Where we can stand out, and where we shouldn't compete

| | FiftyOne (open source) | This proposal |
|---|---|---|
| Multi-user, authentication, permissions | Paid product only | Yes (QA-Board) |
| Custom raw formats | Convert first | Native decoder plugins, pixel values |
| Data stays in plain folders + sidecars | No (its own DB is the source of truth) | Yes, the core principle |
| Collections as folders | Export | Symlink views, mounts later |
| Link to algorithm runs and regressions | No | Joins on hashes, slices, "inputs changed" |
| Labels, embeddings, model evaluation | Excellent | Out of scope, export to FiftyOne |

**Semantic search** ("night scenes with traffic lights") is the one ML feature worth considering early.
It directly answers "do we already have this?". It's cheap: a CLIP-style embedding per thumbnail,
`pgvector` in the Postgres we already run, computed on a GPU worker. Phase 3, but it might be the demo that
gets people on board.

## 7. Risks and open questions
1. **Ownership.** Who curates the roots, extractors and recipes per team? Without a named owner in each
   algorithm team, metadata decays, whatever the tool. This is a staffing question before a technical one.
2. **Scale.** How many files and bytes are in the roots we would index? Is it millions or hundreds of
   millions? This decides whether Postgres alone is enough for search (probably) and how long the first
   full hashing pass takes (could be weeks on a slow NAS, hence the fast fingerprints).
3. **Permissions.** Should the catalogue follow filesystem permissions (complex: UNIX groups, ACLs) or
   project access in QA-Board (simple, but it can show the existence of files that a user can't open)?
   I'd start with project or root-level access and serve file contents through the existing
   `/api/v1/files/authorize` check.
4. **The current internal image database.** Import its hash ↔ run data, and position this as its
   successor rather than as a competitor. That's a conversation to have with its team, not a technical
   issue.
5. **Settings on the NAS** for SMB symlinks (§5.5), and whether IT's deduplication tolerates the scan
   I/O pattern.
6. **Videos and bursts.** The model supports `kind: burst | video`, but the viewers and extractors
   for them are a later phase.

## 8. Phases

| Phase | Scope | Size | Exit criterion |
|---|---|---|---|
| 0. "What we already have" | List and search the `test_inputs` QA-Board already holds: thumbnails through IIIF and the decoder, facets on `meta`, "used in runs". Store the hash from `manifest.inputs.json` | S (2-3 weeks) | People find inputs they didn't know existed. If nobody opens the page, stop here and save the rest of the budget |
| 1. Catalogue | Storage roots, crawler, samples/sidecars model, extractor and decoder plugins, tags written to sidecars, `qaboard.images` Python API, `collection:`/`query:` in qaboard.yaml | L | One team's batches are declared through collections |
| 2. Workflows | Ingest recipes (form + CLI), register drop folders, frozen collection versions, symlink views, sidecar history, "inputs changed since reference" | M-L | A lab shoot goes from the camera to a batch without anyone writing a path by hand |
| 3. Extras on demand | FUSE/NFS mounts, semantic search, FiftyOne export, videos/bursts | M each | Driven by requests from the previous phases |

Phase 0 is deliberately small. It tests the riskiest assumption, that people will come and look, with
data we already have, before we build crawlers and recipes.
