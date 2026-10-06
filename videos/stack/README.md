# Demo stack: the real QA-Board server, locally

A scripted way to run the QA-Board server built from this checkout (with its unreleased changes), fill it with
results uploaded by the `qa` CLI, and open the pages worth showing in a video.

```bash
videos/stack/stack.sh up      # build the images if missing, start, wait until healthy
videos/stack/stack.sh seed    # demo git repo with 2 commits, results uploaded with `qa batch`
videos/stack/stack.sh url     # URLs of the key pages
# open http://localhost:5151
```

| Command | What it does |
|---|---|
| `up [--build]` | Builds the missing images (all of them with `--build`), starts the stack, waits until the API, the web app and the image server answer. Idempotent. |
| `down` | Stops and removes the containers. The database (docker volume) and the results are kept. |
| `reset` | `down` + deletes the database and the results storage. No confirmation. Then `up` and `seed` again. |
| `status` | Containers and API health. |
| `logs [service...]` | Follows the logs (default: backend and proxy). |
| `url` | Prints the URLs of the web app and of the demo pages, with the commits from the API. |
| `seed` | Creates the demo git repo in `/tmp/qaboard-demo/repo` and uploads its results (see below). For a clean state with fresh "x minutes ago" dates before recording: `reset && up && seed` (re-running `seed` alone adds 2 more commits). |
| `build` | Rebuilds the images from the current sources (Docker's cache makes it fast if little changed). |
| `compose ...` | Any `docker compose` command on the demo stack, e.g. `compose ps`, `compose exec backend bash`. |

`videos/stack/screenshots.py` takes screenshots of the key pages with Playwright, to check that everything displays:

```bash
PLAYWRIGHT_BROWSERS_PATH=/opt/pw-browsers uv run videos/stack/screenshots.py /tmp/shots
```

## What runs

`stack.sh` runs `docker compose -p qaboard-demo -f docker-compose.yml -f production.yml -f videos/stack/compose.demo.yml`
(see `stack.sh compose config`), with only these services:

- `db` (postgres), `redis`
- `backend` (Flask/uwsgi, runs the database migrations at startup)
- `frontend`: a one-shot container that copies the production build of the web app to a volume, then exits (`Exited (0)` is normal)
- `proxy` (nginx): serves the web app, `/api`, the result files under `/s/`, and the image server under `/iiif/`, on port 5151
- `cantaloupe`: the IIIF image server used by the image viewers

Not started: rabbitmq/flower (celery), website, pgadmin, backups. Images are built from this checkout and tagged
`qaboard-demo:<service>`, so they don't shadow the published `arthurflam/qaboard:*` images. Volumes are prefixed with
`qaboard-demo_`, so the demo doesn't touch another QA-Board stack on the same host.

| Variable | Default | |
|---|---|---|
| `QABOARD_DEMO_PORT` | `5151` | The web app is at `http://localhost:$QABOARD_DEMO_PORT` |
| `QABOARD_DEMO_STORAGE` | `/mnt/qaboard` | Results storage, see below |
| `QABOARD_DEMO_PROJECT` | `qaboard-demo` | docker compose project name |
| `QABOARD_DEMO_CA_BUNDLE` | | CA bundle the builds must trust, behind a TLS-intercepting proxy, see below |
| `QABOARD_DEMO_WORKDIR` | `/tmp/qaboard-demo` | Demo git repo (`repo/`), build and seed logs |
| `QA` | `.venv/bin/qa` | The `qa` CLI used by `seed` (`uv sync` creates it) |

## Key pages

`stack.sh url` prints them with the real commit ids. With `<new>` the latest commit and `<ref>` the first one:

| Page | URL |
|---|---|
| Projects list | http://localhost:5151/ |
| Project (latest commits) | http://localhost:5151/demo/sample |
| Commit (summary of the metrics) | http://localhost:5151/demo/sample/commit/<new> |
| Outputs with image viewers | http://localhost:5151/demo/sample/commit/<new>?selected_views=output-list |
| One output's viewers | http://localhost:5151/demo/sample/commit/<new>?selected_views=output-list&filter=blobs |
| Compare 2 commits: metrics | http://localhost:5151/demo/sample/commit/<new>?reference=<ref>&selected_views=table-compare |
| Compare 2 commits: images side by side | http://localhost:5151/demo/sample/commit/<new>?reference=<ref>&selected_views=output-list&filter=checker |
| A `qa --share` experiment vs CI | http://localhost:5151/demo/sample/commit/<new>?batch=%40root%7C%20radius-3&reference=<new>&batch_ref=default&selected_views=table-compare |
| History, dashboard | http://localhost:5151/demo/sample/history, http://localhost:5151/demo/sample/dashboard |

In the UI: the commit page shows a `new` row and a `ref` row at the top; "Change" on the `ref` row picks the commit
to compare against (by default, the latest commit of `reference_branch`, here the same commit). The batch selector on
the right of each row picks the batch (`CI` = `default`). The sidebar switches views: Summary, Metrics Table,
Metrics Diff (`table-compare`), Visualizations (`output-list`), Output Files, Logs... Other query parameters:
`batch`/`batch_ref`, `filter`/`filter_ref` (filter outputs by input name), `selected_views`.

## Demo data (`seed`)

`videos/stack/demo_project/` is a toy "denoiser" with no dependencies (pure python, writes PNGs): for each input
(`scenes/*.json`), it renders a synthetic image, adds noise, denoises it, saves `clean.png`, `noisy.png`, `output.png`,
and returns the metrics PSNR, PSNR gain and runtime. `seed` copies it to `/tmp/qaboard-demo/repo`, then:

1. commit "Denoise images with a box blur" on `master`, `CI=true qa batch demo` (4 runs, ~4s)
2. commit "Use a 5x5 median filter to preserve edges", `CI=true qa batch demo`: PSNR improves on every input, runtime gets worse
3. `qa --share --label radius-3 --tuning '{"radius": 3}' batch demo`: a developer's experiment on the latest commit, shown as batch `@<user>| radius-3`
4. a local account to log in with: `ada` / `lovelace`

`CI=true` makes `qa` behave like in CI: results go to the `default` batch, the one the web app shows first. Outside
CI, `qa --share` labels batches `@<user>| <label>`.

### What the CLI needs

```bash
export QABOARD_URL=http://localhost:5151   # (or QABOARD_HOST=localhost:5151)
export QA_NO_CHECK_FOR_UPDATES=1
export PATH=/path/to/qaboard/.venv/bin:$PATH   # `qa batch` starts `qa run` subprocesses: qa must be in the PATH
cd my-project && qa --share batch my-batch     # results are written under `storage` from qaboard.yaml
```

- **Storage**: the CLI writes results under `storage` from *qaboard.yaml* (`/mnt/qaboard` in the demo project), e.g.
  `/mnt/qaboard/demo/sample/<sha[:2]>/<sha[2:16]>/output/...`. The backend must see them at the same path: the
  overlay mounts `$QABOARD_DEMO_STORAGE` at the same path in the backend (and sets `QABOARD_STORAGE_ROOTS`, outside
  of which the server refuses paths), in nginx under `/var/qaboard` (for `/s/...` URLs), and in cantaloupe under
  `/repository` (for `/iiif/...`). If you change `QABOARD_DEMO_STORAGE`, change `storage` in qaboard.yaml too, or set `QA_STORAGE`.
- **Git**: the server needs no access to the git repo, no GitLab/GitHub integration and no webhook. With each run,
  `qa` posts the commit's metadata (sha, branch, message, author, date, parents) and the project's *qaboard.yaml*
  and metrics definitions. The project is created on the first upload. Its configuration (visualizations, metrics)
  is updated from the commits on `reference_branch` (`master`): commit changes there.
- `project.url` is required in *qaboard.yaml* (the CLI crashes with `KeyError: 'url'` without it); it is only used for links.

## Timings

Measured on a 4-CPU VM:

- First build: ~5 min (`uv sync` compiles uwsgi/lxml/xmlsec: ~3 min; the web app build ~1.5 min). ~1 min more behind a TLS-intercepting proxy, for the CA base images.
- `up` on a fresh database: ~13s (migrations). `up` after `down`: ~6s. `reset`: ~3s.
- `seed`: ~12s. `screenshots.py`: ~30s.
- Disk: ~3.2 GB of images (backend 2 GB).

## Gotchas

These are worked around in `compose.demo.yml` / `stack.sh`, without changing tracked files:

- **Image viewers stay empty** with the default `QABOARD_IMAGE_SERVERS={"default": "/iiif"}`: the web app appends the
  image identifier right after the endpoint (`/iiifmnt%2F...`, a JS error "IIIF required parameters not provided").
  The overlay sets `{"default": "/iiif/2/"}`.
- **Tiles requested without the port**: nginx forwards `X-Forwarded-Port: 80`, so cantaloupe's `info.json` points to
  `http://localhost/iiif/...`. The overlay sets cantaloupe's `BASE_URI=http://localhost:5151`. If you change the port,
  use `QABOARD_DEMO_PORT` (it updates both). Opening the app via another hostname than `localhost` would break the image tiles.
- **nginx doesn't start on hosts without IPv6** (`socket() [::]:80 failed (97: Address family not supported)`): the
  overlay deletes `listen [::]:80` from the generated config when the kernel has no IPv6.
- **nginx resolves backend/cantaloupe once**: if they are recreated (new IPs) while nginx keeps running, requests
  hang or fail with 502. `stack.sh up` reloads nginx; after a manual `compose up`, run `stack.sh up` again.
- **docker-compose.yml's proxy waits for flower**, and nginx needs the `flower` hostname to resolve: the overlay drops
  the dependency and gives cantaloupe the network alias `flower`.
- **"What's new" popup** on the first visit of a browser profile: click "Got it" before recording, or set
  `localStorage['qaboard.release-notes.last-seen'] = '9999-12-31'` (what `screenshots.py` does).
- **The Controls panel** floats over the right of the commit page: collapse it with its arrow button.
- **Avatars**: without a GitLab integration, commits have no avatar and the circle shows the author's name, cut ("Ada Lovela").
- **Docker Hub rate limits** (`429 Too Many Requests` on `load metadata for docker.io/library/...`): BuildKit asks
  the registry for the base images on every build, even cached ones. Wait, or `docker login`.
- **Behind a TLS-intercepting proxy** (sandboxes, corporate networks), `uv sync` and `npm ci` fail with
  certificate errors during the build. Set `QABOARD_DEMO_CA_BUNDLE=/path/to/ca-bundle.crt`: `stack.sh` builds
  `qaboard-demo-ca/python:3.13-slim-bookworm` and `qaboard-demo-ca/node:24` from `ca/Dockerfile` (the bundle is a
  build secret, installed in those local images' trust store), and `compose.ca.yml` substitutes them for the
  Dockerfiles' `FROM` images (BuildKit named contexts). No proxy settings are needed if HTTP(S) is intercepted
  transparently; the repo's `PROXY_URL` build args remain available otherwise.
- **Default reference = the same commit**: without `?reference=`, the `ref` row is the latest commit on `master`,
  i.e. the latest commit itself, and images are marked "same-image".
- `reset` only wipes `$QABOARD_DEMO_STORAGE` if `stack.sh` created it (it has a `.qaboard-demo-storage` file): it
  won't delete real results if you point it to a shared folder.
