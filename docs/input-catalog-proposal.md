# Proposal: an input catalog in QA-Board

Status: proposal v2 (2026-10-26), for discussion. Nothing here is implemented.
v2 adds §6 (scope, permissions, several storages), §9 (what already exists) and renames "images" to "inputs".
Line references point to this branch's tree.

**Wording.** This document says *inputs*, as QA-Board does everywhere else. An input is whatever a run takes:
a file or a folder (`use_parent_folder`). Images, raw captures, videos, sensor recordings and point clouds are
all inputs. Images get the most attention (thumbnails, the image viewer) because they are most of our data.
Other kinds of input get metadata, search, collections and folders, with a thumbnail when a decoder exists.

## TL;DR
- **The previous attempts didn't fail on technology.** The projects failed on adoption (a desktop app nobody
  could install), on lock-in (data you can only reach through the tool, formats the tool can't preview), on
  data models that don't fit (a list of runs stored in each image document), and on conventions that die
  when the person pushing them leaves.
- **Recommendation: "index, don't ingest", built into QA-Board.** The files stay on the IT shared storage, in
  plain folders, and they remain the source of truth. Metadata lives in **sidecar files next to the inputs**.
  Postgres only holds an **index** that can be rebuilt from the files. If the application dies, the data is
  still usable as plain folders and files: that is the antidote to "a new project every two years".
- **Day 1 does not start empty, and adoption is incremental.** QA-Board already records every input that
  every run used (`test_inputs`). Those show up in the catalog the moment a run uses them, before anyone
  uploads or tags anything. Users can ignore the catalog completely. Then a team points one batch at a
  collection, and later its ingestion scripts write sidecars and register new captures.
- **Four ways to consume the same data:**
  1. a FiftyOne-style web grid (facets, tags, bulk select, the image viewer we already have);
  2. a Python API (`qaboard.inputs`);
  3. `qaboard.yaml` batches that point at a **query or a pinned collection**, a native replacement for
     `only`/`exclude` over external databases;
  4. collections **materialized as folders of symlinks**, with mounts later only if people ask.
  Plus **ingest recipes** (Python functions whose typed parameters become a web form) to add inputs and
  write sidecars.
- **Scope: one catalog per QA-Board server, readable by everyone, with no new permission system.** An input
  is visible to whoever can read its file under the rules QA-Board already applies to the file browser. Projects
  get default views and own their collections. Several shared storages become a `storages` table (§6).
- **Don't try to out-FiftyOne FiftyOne.** Its strengths are labels, embeddings and model evaluation. Ours
  would be: files first, custom raw formats, sidecars, a multi-user server with authentication, the link to
  runs, and folder views. Offer `to_fiftyone()` for the people who want its ML tooling.
- **Most of the pieces exist** (§9). Phase 0, browsing the inputs QA-Board already knows, is about 2 weeks.

---

## 1. Why the previous attempts failed (and what each one teaches)

| Attempt | What went wrong | Design rule we take from it |
|---|---|---|
| Desktop application | Nobody figured out how to install it, and the UX was poor | Web only. Zero install for people who browse. `pip`/`uv` only for scripting |
| Commercial DAM (web, tag search) | Thumbnails for our custom formats were hard to get. Once the data was in, it could not be exposed as plain folders. We were an awkward customer for the vendor | We own the decoders through plugins. Files stay plain files. No vendor relationship to maintain |
| Internal "idb" (hash-tracked, MongoDB) | No web UI to look at images. Each image document held an array of the runs it appeared in, so the data model doesn't support real queries | Relational joins (input ↔ run), not embedded arrays. The UI ships first, not last |
| Folder naming conventions | A new convention every two years, then it fades | Don't rely on paths carrying the meaning. Metadata belongs to the file (sidecar, hash), wherever it is |

There is a pattern we should be honest about: **every attempt had a champion and no forcing function.**
When the champion moved on, nobody kept tagging, and the database decayed. The defence is to put the tool on
a path people **already walk**. For us that path is QA-Board batches: if the catalog is how you declare what
a batch runs on, it gets used and kept up to date as a side effect of normal work.

## 2. What QA-Board already has (verified)

- **Inputs are already in the database, global across projects**, but barely modelled. Table `test_inputs`
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
  - Missing: a thumbnail pipeline and a decoder for custom formats that Cantaloupe can use.
- **File access rules already exist** (`backend/backend/api/files.py:66-130`). They come from
  `QABOARD_LOGIN_RESTRICTED_YAML` (path → rules), from project storage folders (project roles), and from
  `QABOARD_FILES_UNIX_PERMISSIONS` (UNIX permissions of the signed-in account). nginx checks them for every
  file through `/api/v1/files/authorize`.
- **The rest of an app is there:** project roles and groups (`backend/backend/models/Access.py`), Celery
  workers, the storage scans (`StorageScan`), the file browser (`/s/`), and a Python client (`qaboard/api.py`).
- **The idb integration** (`qaboard/idb.py`) pushes output image md5s to the internal image database. It's a
  migration source and a lesson, not a base.
- **Roadmap:** "Dataset slices" (metrics per slice of inputs) is listed as exploring
  (`website/docs/roadmap.mdx:88`). It falls out of this design almost for free.

## 3. Requirements, reordered by what actually matters

**Must have**
1. Find inputs that already exist ("we're about to shoot this for the 10th time"). Search by tags, dates,
   sensor and capture settings, project, free text, and "used in which runs".
2. Look at them in a browser: thumbnails for our custom formats, plus a full viewer with pixel values.
3. Get a selection out as **a folder**, **a Python list**, or **a QA-Board batch**.
4. Add inputs easily, with metadata filled automatically, and with project-specific sidecars (white balance)
   produced by custom scripts that take parameters from the user.
5. Storage stays on the IT shared storage, as plain files you can edit.

**Should have**
- Saved, versioned collections, so a batch is reproducible even when its query would now match more inputs.
- History of sidecar edits, and "this run used white balance v2".

**Not now**
- Full revision control of input data. Nobody has reported it as a real pain point. Sidecar history (§5.6)
  covers the realistic need at almost no cost.
- Annotation and labelling tools. Use CVAT or Label Studio and import their outputs as sidecars.
- Object storage. The shared storage is cheaper for us, IT already deduplicates, and files stay editable.
  The `storages` table (§6.3) leaves room for it.

## 4. Design directions

### Direction A — "Index, don't ingest" (catalog over the existing storage) — **recommended**
The application never owns the files.
- Admins declare **storages** (`/datasets/...`, the per-project database folders already used in
  `inputs.database`). A crawler indexes the ones marked for it.
- Uploads are just a convenient way to **write files into a storage, in a layout the app generates**.
- Metadata of record is in sidecars. The database holds a rebuildable index, plus the things that have no
  natural home in a file: tags and collections. Those are also exported to sidecars and manifests, so the
  "the app died" scenario loses nothing.
- (+) No migration: legacy folders become searchable as they are. Survives the app dying. Edit files with any
  tool. IT's deduplication and scaling are free.
- (−) Crawling NFS/SMB is slow, so scans must be incremental and scoped. Files get moved or renamed behind
  our back, so identity has to come from content hashes. People can break sidecars by hand, so they need
  to be validated and the errors shown.

### Direction B — "Managed library" (the app owns a content-addressed store)
Upload everything into `<store>/<hash[:2]>/<hash>`. The database is the source of truth. Folders are only
ever views.
- (+) Clean: real deduplication, natural revisions, simple identity, ready for object storage.
- (−) This is the internal idb and the commercial DAM again. Everything has to be imported, and nobody knows
  where their files are without the app. It fights the "everything is a file, edit the sidecar" habit, which
  is the one thing users like about the current mess. **Not recommended.**

### Direction C — Adopt FiftyOne OSS and integrate
- (+) A great UI and API on day one.
- (−) The open-source app is designed for one user: no multi-user authentication or permissions (those are
  in the paid product), and a MongoDB of its own. It doesn't know our raw formats, and has no folder views.
  It would be a second system to deploy and keep in sync.
- Worth keeping as an **export target** (`to_fiftyone()`), not as the foundation.

### Direction D — A standalone new application, outside QA-Board
- (+) Neutral branding, so teams that don't use QA-Board might adopt it more easily.
- (−) This is the failure pattern: a new app, its own authentication, deploy and on-call, and no forcing
  function. It also loses the image viewer, workers, file access rules and file serving we already have.
- **Mitigation if neutrality matters:** keep the model and API separable inside QA-Board (its own blueprint,
  tables and Python module), with an "Inputs" entry in the sidebar that works without any project.

**Recommendation: A, inside QA-Board, with separable boundaries (the D mitigation) and C as an export.**

## 5. Proposed design (Direction A)

### 5.1 Core model: a catalog input is a group of files
An input is rarely one file. There is the raw capture, its `.wb.json`, maybe a `.jpg` preview and capture
logs; or a folder of frames. As in FiftyOne's groups, the catalog's unit is an **input** with its files:

```
storages          id, name, paths per OS (linux, windows), indexed, writable, restriction, layout rules, extractors
catalog_inputs    id, storage_id, relpath (of the primary file or folder), kind (file | folder | burst | video)
                  content_hash, size, mtime, width, height, format, captured_at
                  first_seen_at, indexed_at, missing_since (null while the file exists)
                  meta JSONB (merged: extractors + sidecars + user fields), with a GIN index
                  restriction_key (§6.2), extractor_versions, thumb_state
catalog_files     input_id, role (primary | sidecar:<name> | preview | aux), relpath, hash, size, mtime
tags              (input_id, tag), who and when, also written to the sidecar
collections       id, owner (project or user, §6.1), name, query (JSON) | null
collection_versions  collection_id, version, created_at, manifest: list of (input_id, content_hash, sidecar hashes)
sidecar_history   input_id, role, hash, content (small text), changed_at, detected_by (scan | ui | api)
```

- The table is `catalog_inputs`, not a change of `test_inputs`. `test_inputs.path` names the output folders
  of runs (`TestInput.output_folder`), so changing what it holds would move run results. Instead
  `test_inputs` gets a nullable `catalog_input_id` (§6.3): several ways of referencing a file (different
  `database` and `path` splits, symlinks) point to one catalog input.
- Typed columns only for what every input has. Everything else goes in `meta` JSONB: Postgres handles tens of
  millions of rows with GIN and expression indexes on hot keys.
- **Link to runs:** `outputs.input_hashes`, taken from `manifest.inputs.json` when the run is posted. "Which
  runs used this input?" becomes a join, and so does "which runs used it with the old white balance?".
- **Identity:** `(storage, relpath)` gives the location, `content_hash` gives the content. A moved file is
  detected as missing in one place and appearing elsewhere with the same hash. Its tags and history follow it.
- **Hashing large files over NFS is expensive.** Use size, mtime and a hash of the first and last MiB as a fast
  fingerprint, then compute the full hash (md5, to match the manifests) in the background with low priority.

### 5.2 Sidecars: the convention that has to outlive the app
- `IMG_0042.raw` → `IMG_0042.raw.meta.yaml` holds the user metadata and tags, editable by hand.
  A folder input keeps it inside: `<folder>/.meta.yaml`.
- Other sidecars are `IMG_0042.raw.<name>.<ext>` (for example `.wb.json`). Each storage declares the sidecar
  patterns it knows (`<stem>.wb.json` for legacy layouts).
- The app reads sidecars. It writes them when tags or fields change in the UI, on storages marked `writable`
  where its service account can write. Elsewhere, edits stay in the database and
  `qa inputs export-sidecars` writes them as the user.
- A file edited by hand is picked up at the next scan of that folder, or immediately with `qa inputs rescan <path>`.
- **Why this matters:** grep, editors, scripts and any future tool can read the metadata without our
  database. The convention is the durable part. The application is replaceable.

### 5.3 Crawler and extractors
- **Scans are incremental**, per storage. Walk the folders, then for each one compare its mtime and listing
  with the last scan. NFS doesn't support inotify, so use scheduled scans plus explicit `rescan` calls from
  uploads and the CLI. Reuse the existing `StorageScan` job.
- **Run inputs need no crawler.** When a run is posted, its input is resolved and indexed (§6.3). That alone
  fills the catalog with everything QA-Board has ever run on.
- **Extractors** are Python plugins, versioned in git like the rest of a project's QA code, run on Celery or
  LSF workers. Each one declares the files it applies to and returns metadata fields, and can also produce a
  thumbnail or a decoded preview:

```python
# extractors/hm3_raw.py
from qaboard.inputs import extractor

@extractor(match="*.raw", version=3)
def hm3(input):
    header = read_hm3_header(input.primary)
    return {"sensor": header.sensor, "exposure_us": header.exposure, "iso": header.gain * 100,
            "captured_at": header.timestamp}

@extractor.decoder(match=["*.raw", "*.hex"])
def decode(path):            # numpy array, used for thumbnails, IIIF and pixel values
    return cde.image.read_image(path)
```

- The existing `metadata()` and `iter_inputs()` hooks keep working. The metadata they return when runs are
  posted is stored too, as one more source next to the extractors, instead of being overwritten by the last run.
- Bump `version` to re-extract. The extractor version is stored per input, so we know what is stale.
- **Thumbnails for custom formats** are solved once, by the decoder. It writes a JPEG/WebP thumbnail into a
  cache and, for large images, a pyramidal TIFF that Cantaloupe already serves. The image viewer, pixel probes
  (`backend/backend/api/image.py`) and thumbnails share one decoding path. Inputs without a decoder show an
  icon for their type, and folders show their first image.

### 5.4 Ingest recipes (adding inputs, sidecars, custom scripts with parameters)
```python
# recipes/lab_shoot.py
from typing import Literal
from qaboard.inputs import recipe, Path

@recipe(title="Lab shoot", storage="lab", layout="{date:%Y/%m}/{scene}/{sensor}")
def lab_shoot(files: list[Path], scene: str, illuminant: Literal["D50", "D65", "A", "TL84"],
              wb_from_grey_card: bool = True):
    for f in files:
        wb = estimate_wb(f) if wb_from_grey_card else default_wb(illuminant)
        f.write_sidecar("wb.json", wb)
        f.meta.update(scene=scene, illuminant=illuminant)
```

- **The signature is the form.** Type hints become a web form (enums become dropdowns), and also CLI
  options for `qa inputs ingest lab_shoot --scene ...`. One definition gives a UI and a CLI.
- **The layout is generated, not a convention people have to remember.** The recipe decides where files land,
  so the next "new convention" is a one-line change, not a two-year project.
- **Large shoots don't go through the browser.** Copy the files to a drop folder on the shared storage, then
  "register" the folder from the UI or the CLI. Browser upload is for tens of files.
- **Recipes run on workers, as the user who asked**, with logs streamed like a run. A recipe is in fact a
  QA-Board run without a commit, so we reuse that machinery instead of building a second job system.
- **Existing ingestion scripts don't need to become recipes.** Calling `qi.register(path, meta=...)` at their
  end is enough. That's the step after "my batch uses a collection" in the adoption path.

### 5.5 Collections and how you get them out
**A query is live, a collection version is frozen.** Both are first class.

**Python API (FiftyOne-like):**
```python
from qaboard import inputs as qi
view = (qi.find(tags="outdoor", sensor="HM3", captured_at=">=2025-01")
          .exclude(tags="blurry")
          .sort_by("captured_at")
          .take(200, seed=0))
for i in view:
    i.path, i.meta["iso"], i.sidecar("wb.json")   # i.path is the path on this machine's OS
c = view.save("camera/night-hdr")                  # creates camera/night-hdr@1, a frozen manifest
qi.collection("camera/night-hdr@1").materialize("/scratch/me/night-hdr")   # a folder of symlinks
view.to_fiftyone("night-hdr")                      # optional export
```

**qaboard.yaml (native, replacing `only`/`exclude` for this use case):**
```yaml
night:
  inputs:
    collection: camera/night-hdr@3   # pinned: reproducible, CI-safe
smoke:
  inputs:
    query: {tags: [smoke], sensor: HM3}   # live, fine for exploration
    limit: 20
```
- Implement it as a built-in `iter_inputs` (the hook at `qaboard/iterators.py:238` exists). Each catalog input
  resolves to a `database` (its storage's path) and a `path` (its relpath), so `test_inputs` and run folders
  stay what they are today, and user `run()` code doesn't change. The input's `meta` becomes the run's
  `input_metadata`.
- **Warn when a batch uses a live query in CI**, since the reference run could then differ in its inputs.

**Folders.** In order of cost:
1. **Symlink farm on the shared storage**: `collections/<owner>/<name>@<v>/<original relative path>`. Works with
   every Linux tool and LSF job. Windows over SMB only follows symlinks if the NAS allows it ("wide links" or
   the equivalent): **ask IT, it's a setting on their side**. As a fallback, hardlinks work over SMB on the
   same volume, but break when an editor saves through a temporary file and a rename.
2. **ZIP download** of small collections from the UI.
3. **A virtual filesystem, only if 1 is not enough**: queries as paths. A read-only FUSE daemon on Linux, or a
   userspace NFSv3 server if installing FUSE on clients is a problem. Not WebDAV: the Windows client is slow
   and has file size limits.

### 5.6 Revisions, honestly scoped
- **Input files:** no versioning. A changed hash means a new content version: the change is recorded in the
  history, but the old bytes are not kept.
- **Sidecars:** they are small, so the crawler stores **every version it sees** (`sidecar_history`): who
  changed the white balance, when, and what it was before.
- **Runs:** keep the sidecar hashes along with the input hashes, then:
  - flag "inputs changed since the reference run" on the comparison page, a cheap explanation for
    bit-accuracy breaks that nobody can explain today;
  - offer "re-run the batches affected by this change", building on `docs/dag-pipelines-proposal.md` later.

### 5.7 The web UI
- **"Inputs" in the sidebar**, next to projects. It works without a project, and inside a project it opens on
  the project's view (§6.1).
- **Grid**: virtualized thumbnails. The JUnit viewer already handles 50k rows.
- **Facets** in a sidebar, with counts, generated from `meta` key statistics. Admins pick which keys show
  as facets per storage.
- **Query bar** that shares its syntax with the Python API and YAML. Copy a view as Python or YAML.
- **Bulk select → tag, add to collection, run recipe, export**, plus "run a batch on these".
- **Detail page**: the existing image viewer with pixel values, the sidecars (editable, schema-validated),
  the history, "used in N runs" with links, and copies elsewhere.
- **Selection in the URL** (`src/selection.js`), so views can be shared.
- **Dataset slices on the history pages**: metrics grouped by any `meta` key or tag of the inputs.

## 6. Scope, permissions and several storages

### 6.1 Scope: one catalog per server, projects as views
- **One catalog for the whole server.** `test_inputs` is already global, and the same captures are reused by
  many projects (a calibration set serves AWB, AE and denoising). Splitting the catalog per project would
  bring back the "where is it?" problem between teams.
- **What projects get:**
  - **A default view.** A project's Inputs page filters on the storages its `qaboard.yaml` uses and on the
    inputs its runs used. One click removes the filter.
  - **Ownership of collections.** Collections are named `<project>/<name>` or `~<user>/<name>`. A project's
    collections are edited by its developers (the existing `project.write`-style roles), read by everyone
    who can read the project. Personal collections are for exploring.
- **Tags are global and free-form.** Anyone signed in can add a tag; the history says who did. Convention
  over policy: teams that want their own tags prefix them (`awb:golden`). We can restrict later if it gets
  noisy; I don't expect it to.
- **Editing sidecars and metadata** needs write access to the storage (§5.2). The UI shows them read-only otherwise.

### 6.2 Permissions: visibility follows the files
**No new permission system.** QA-Board already decides who can read which file (`backend/backend/api/files.py:66-130`):
path rules from `QABOARD_LOGIN_RESTRICTED_YAML`, project storage folders (project roles), UNIX permissions
under `QABOARD_FILES_UNIX_PERMISSIONS`, and "sign in required". nginx applies them to every file it serves.
The catalog shows an input to whoever can read its file, nothing more.

- **Default: everyone signed in sees everything.** Most storages have no rule, so this is what you get without
  configuring anything, and it matches how the shared storage is used today.
- **Listing must be fast,** so we can't run the per-file check on every row of a search. When an input is
  indexed, it gets a `restriction_key`: the most specific rule that covers its path (`none`, a YAML rule, a
  project folder, a UNIX-permissions root). There are only a handful of distinct keys. Each search computes
  once which keys the user may read, and filters on them in SQL.
- **UNIX permissions are the exception.** They can differ file by file, and listing would need a check per file.
  For listing, a storage under `QABOARD_FILES_UNIX_PERMISSIONS` is restricted unless an admin marks it as
  listable by everyone; the bytes are still checked per file when they are served, as today.
- **Rules change.** When the access rules change, recompute `restriction_key` (a quick SQL update by path
  prefix). The worst case between the change and the update is showing names and metadata, never file
  contents, since nginx still checks every file.
- **API tokens** (`qa` in CI) see what their projects can read, as with files today.

### 6.3 Several shared storages
Today an input is just a `database` string (an absolute path given by the client) plus a `path`, and the
server assumes it mounts the storage at the same path as the clients. That works with several storages already.
What's missing is a name for each one, for the catalog to know what to index and how to show paths to Windows
users. The changes:

- **A `storages` table** (admins edit it in the web app): a name, the paths per OS (the same
  `{linux:, windows:}` shape as `location_from_spec`, `qaboard/conventions.py:18`), whether to crawl it,
  whether the server may write sidecars there, and its sidecar patterns and extractors. Object storage
  (`s3://`) can be a storage later, with the same table.
- **Resolving a run's input to a catalog input:** take `database/path`, resolve symlinks, find the storage with
  the longest matching path, and get `(storage, relpath)`. This happens when a run is posted. Existing
  `test_inputs` are back-filled once by a migration job.
- **Inputs outside any declared storage** still enter the catalog, under an implicit storage named after
  their top folder, marked "not crawled". They appear because runs used them, which is exactly the
  adoption path. An admin can later promote that folder to a real storage.
- **The same content in several places** (copies, mirrors between sites, a storage mounted under two paths)
  is one content hash with several locations. The UI shows "also at…" and the Python API returns the
  location readable from the current machine.
- **Paths per OS:** the API and the UI give paths for the reader's OS, using the storage's paths. That fixes
  the long-standing problem of copying a Linux path to a Windows user.
- **Two QA-Board servers** (say, two sites) each index what they mount. Sharing a catalog between servers is
  out of scope.

### 6.4 Database changes, in summary
- New tables: `storages`, `catalog_inputs`, `catalog_files`, `tags`, `collections`, `collection_versions`,
  `sidecar_history`.
- `test_inputs`: add `catalog_input_id` (nullable). Nothing else changes, so run folders and every existing
  query keep working.
- `outputs`: add `input_hashes` (nullable JSONB), from `manifest.inputs.json`.
- Migrations add nullable columns only, as the recent ones do, so they are quick on a large database.

## 7. Where we can stand out, and where we shouldn't compete

| | FiftyOne (open source) | This proposal |
|---|---|---|
| Multi-user, authentication, permissions | Paid product only | Yes, the file rules QA-Board already has |
| Custom raw formats | Convert first | Native decoder plugins, pixel values |
| Data stays in plain folders + sidecars | No (its own DB is the source of truth) | Yes, the core principle |
| Collections as folders | Export | Symlink folders, mounts later |
| Link to algorithm runs and regressions | No | Joins on hashes, slices, "inputs changed" |
| Labels, embeddings, model evaluation | Excellent | Out of scope, export to FiftyOne |

**Semantic search** ("night scenes with traffic lights") is the one ML feature worth considering early. It
answers "do we already have this?" directly, and costs one embedding per thumbnail, stored with `pgvector`
in the Postgres we already run.

## 8. Risks and open questions
1. **Ownership.** Who curates the storages, extractors and recipes per team? Without a named owner in each
   algorithm team, metadata decays, whatever the tool.
2. **Scale.** How many files are in the storages we would crawl? This decides how long the first full hashing
   pass takes (could be weeks on a slow NAS, hence the fast fingerprints). Phase 0 doesn't crawl anything, so
   it doesn't depend on the answer.
3. **Symlinks over SMB** for Windows users (§5.5): a setting for IT.
4. **The current internal image database.** Import its hash ↔ run data, and position this as its successor.
   A conversation with its team, not a technical issue.
5. **Writing sidecars from the server** needs a service account with write access to some storages. If IT
   won't grant it, edits from the UI go through `qa inputs export-sidecars` run by users.
6. **Videos and bursts.** The model supports them, but their viewers and extractors come later.

## 9. How close it is: what exists and what is new

| Need | Already in QA-Board | New work |
|---|---|---|
| Know every input runs used | `test_inputs`, posted by every run | Resolve to a catalog input, back-fill |
| Metadata | `metadata()`, `iter_inputs()` hooks | Keep it per source instead of overwriting; extractors |
| Thumbnails and viewer | Cantaloupe IIIF, internal raw reader, image viewer | Thumbnail cache, decoder plugins |
| Permissions | File rules, project roles, nginx `auth_request` | `restriction_key` at indexing |
| Background jobs and scans | Celery, LSF runners, `StorageScan` | The crawler |
| Big lists in the browser | Virtualized lists (JUnit viewer, 50k rows) | The grid and facets |
| Batches from a selection | `iter_inputs` hook | Built-in `collection:` and `query:` |
| Python access | `qaboard/api.py` with tokens | `qaboard.inputs` and its endpoints |
| Folders | The file browser | Symlink materializer |

## 10. Phases

| Phase | Scope | Size | Exit criterion |
|---|---|---|---|
| 0. "What we already have" | An Inputs page over `test_inputs`: thumbnails through IIIF, facets on the metadata runs already posted, "used in runs". `storages` table, resolution and back-fill, `restriction_key` | S (about 2 weeks) | People find inputs they didn't know existed. If nobody opens the page, stop here |
| 1. Catalog | Crawler, input files and sidecars, extractor and decoder plugins, tags written to sidecars, `qaboard.inputs` Python API, `collection:`/`query:` in qaboard.yaml, collections with symlink folders | L | One team declares its batches through collections |
| 2. Workflows | Ingest recipes (form + CLI), `register()` from ingestion scripts, sidecar history, "inputs changed since reference" | M-L | A lab shoot reaches a batch without anyone writing a path by hand |
| 3. Extras on demand | FUSE/NFS mounts, semantic search, FiftyOne export, videos/bursts | M each | Driven by requests from the previous phases |

Phase 0 tests the riskiest assumption, that people will come and look, with data we already have, before we
build crawlers and recipes. It also lays the parts every later phase needs: the storages, the resolution of
run inputs, and the permission filter.
