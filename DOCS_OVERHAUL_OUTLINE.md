# QA-Board Documentation Overhaul — Proposed Outline

Status: proposal. Based on a survey of the `unified-master` branch (compose overlays,
`MIGRATION.md`, GitHub support, `/api/v1/config`) and the existing `website/` docs.

## TL;DR

1. **Keep Docusaurus** — `unified-master` already migrated to Docusaurus 3.5.2 + TypeScript.
   Add two plugins (OpenAPI rendering, client redirects) instead of switching frameworks.
2. **Restructure around four tracks** (Diátaxis-style): *Get Started* (tutorials),
   *Recipes* (how-to cookbook), *Reference* (config/CLI/env/HTTP API), *Admin Guide* —
   plus a small *Developer* section.
3. **Fix the onboarding traps before writing new prose**: the `qa init` template ships
   SIRC storage paths and an LSF runner that break every outside user's first run.
4. **The API needs an OpenAPI spec and a security pass** — the docs work surfaces real
   issues (open SSRF proxies, unauthenticated destructive endpoints) that should be fixed
   rather than documented as-is.

---

## 1. Docs framework: stay on Docusaurus 3, invest in plugins

**Recommendation: keep Docusaurus.** The migration cost is already paid on `unified-master`
(`website/package.json`: `@docusaurus/core 3.5.2`, React 18, MDX 3, TS config), and two
QA-Board-specific features are wired to it:

- The `QABOARD_DOCS_FOR_WEBAPP=true` build mode that rebases docs to `/docs/` so the
  webapp serves them in-app (nginx `website_builds` volume).
- Algolia DocSearch (`docusaurus.config.ts`).

Alternatives considered, for the record:

| Framework | Pros | Why not |
|---|---|---|
| Astro Starlight | Faster builds, lighter output, nice defaults | Re-migration cost; loses in-app docs mode + versioning; MDX components need rework |
| VitePress | Very fast, simple | Vue ecosystem; no docs versioning story as strong; same migration cost |
| MkDocs Material | Python-native, great search | Would orphan existing React/MDX components (video embeds, feature pages) |
| Mintlify / Fern | Hosted, OpenAPI-native, polished | SaaS dependency; awkward for the self-hosted in-app docs use-case |

**Do adopt (cheap, high-value):**

- `docusaurus-plugin-openapi-docs` or Redocusaurus — render the (to-be-written) OpenAPI
  spec as a real API reference section (§4.5, §6).
- `@docusaurus/plugin-client-redirects` — needed anyway to fix the shipped misspelled slug
  `backend-admin/getting-SSL-certiticates-from-IT` and any URLs changed by restructuring.
- Mermaid (`@docusaurus/theme-mermaid`) for the architecture diagrams the docs currently lack.
- De-Samsung the config: `organizationName`, `url`, `editUrl`, footer, announcement bar all
  point at `Samsung/qaboard`; decide the canonical home (e.g. `arthur-flam/qaboard`) once and
  update `docusaurus.config.ts` + README badges + `QABOARD_DOCS_ROOT` default in
  `backend/backend/api/api.py`.

---

## 2. Proposed information architecture

Four top-level sidebar sections replacing today's "Getting Started / Guides / Backend Admin":

```
Get Started        (tutorial track, linear, a new user reads top-to-bottom)
Recipes            (task-oriented how-tos + example projects)
Reference          (qaboard.yaml, CLI, env vars, Python API, HTTP API)
Admin Guide        (deploy, configure, operate)
Developer          (architecture, contributing — small)
```

### 2.1 Get Started (rewrite; mostly new pages)

1. **What is QA-Board** — rewritten `introduction.mdx`: neutral branding, one architecture
   diagram (CLI ⇄ server ⇄ shared storage ⇄ git host), screenshots instead of the SlideShare
   embed, links into each track. Drop internal `https://qa` links.
2. **Quickstart — local, no server (5 min)** — *new page, currently missing entirely*:
   `pip install qaboard` → `qa init` → `qa run -i <input>` → results under `output/`.
   Makes the "you can evaluate QA-Board without deploying anything" path explicit.
3. **Quickstart — with a server (15 min)** — `docker compose up` one-liner, create a
   project, first `qa --share run`, see it in the UI. Ends with a smoke-test checklist
   ("you should now see X at http://localhost:5151/...") — today no doc confirms success.
4. **Wrap your own code** — rewrite of `running-your-code.mdx`. Keep the three `run()`
   use-cases but **fix the broken samples** (missing `/` operator, `os.copy`,
   `str(context.output_dir]`) and make every block copy-paste runnable.
5. **Inputs and batches** — `inputs.mdx` is in good shape; clarify `inputs.database` vs
   `storage`, fix typos.
6. **Metrics** — from `computing-quantitative-metrics.mdx`, linked forward from `qa init`
   output (the generated `metrics.yaml` is currently never explained in the init flow).
7. **Visualizations** — from `visualizations.mdx`.
8. **Sharing results & CI** — `--share`, `QATOOLS_SHARE`, then webhook + CI setup for
   **both GitHub and GitLab** (today only GitLab is documented although
   `docs/github-support-roadmap.md` shows GitHub webhooks/cloning/avatars are implemented).

**Prerequisite code fix (P0):** `qaboard/sample_project/qaboard.yaml` ships
`storage: /stage/algo_data/ci` (Samsung NetApp), `runners: lsf: queue: alg_isp_q`, and
`http://qa-docs` comment links. Every outside user's first `qa init` produces a broken
project. Replace with neutral defaults (`/mnt/qaboard`, `local` runner) and comment links
to the public docs. This one change is worth more than any new page.

### 2.2 Recipes (new section; the "cookbook")

Each recipe = one page + one runnable example directory in a new `examples/` folder in-repo
(or a companion `qaboard-examples` repo), CI-tested so they can't rot:

- **Python image-processing pipeline** (OpenCV; the canonical demo).
- **Wrapping a compiled executable** (C++/Makefile; from `run()` use-case 2).
- **ML training/eval tracking** (metrics per epoch, comparing runs; seeds from `deep-learning.mdx`).
- **Benchmark suite** (promote `arthur-flam/sysbench-qaboard` to an official example).
- **CI integration**: GitHub Actions recipe + GitLab CI recipe + Jenkins recipe
  (from `ci-integration.mdx`, `jenkins-integration.mdx`, `triggering-external-ci-tools.mdx`).
- **Parameter tuning & optimization**: tuning from the webapp; `qa optimize`
  (rehabilitate the orphaned `auto-optimization.mdx`; the internal draft
  `drafts/tutorial-tweaking-the-optimization-loop.md` has the right shape — strip internal paths).
- **Distributed runs**: local multiprocessing / Celery / LSF (existing sub-category, lightly edited).
- **Bit-accuracy workflows** (`bit-accuracy.mdx`).
- **Monorepos & subprojects** (`monorepo-support.mdx`).
- **Windows setup** — new; Windows paths/mounts are referenced all over but never get a page.
- **Metadata from external databases** (`metadata-integration-external-databases.mdx`).
- Retire or merge: `tuning-workflows.mdx` (visibly broken, already unlisted),
  `drafts-contributing.txt`, `alternatives-and-missing-features.mdx` (fold into FAQ).

### 2.3 Reference (new section; mostly consolidation)

- **`qaboard.yaml` schema** — *the single biggest missing page*: every key
  (`project`, `inputs`, `outputs`, `storage`, `artifacts`, `runners`, `integrations`,
  `metrics`, `garbage`) with type, default, and a link to the relevant guide. Today this
  is scattered across five pages and partly only discoverable in the sample project.
- **`qa` CLI reference** — one page per command group; can be generated from the click
  definitions in `qaboard/qa.py` (11 commands + global options).
- **Python API** — `run(context)` contract, `RunContext` fields/properties (the table in
  `running-your-code.mdx` is a good seed), `qaboard.config`, and deprecate the legacy
  `ctx.obj` dict surface explicitly.
- **Environment variables** — two consolidated tables, *client* (`QABOARD_HOST`,
  `QABOARD_PROTOCOL`, `QATOOLS_SHARE`, …) and *server* (~40 vars found in the backend:
  DB, auth/LDAP/SAML, runners, integrations, telemetry). Fix the `UWSGI_PROCESSS`
  misspelling in the current table.
- **HTTP API** — generated from the OpenAPI spec (§4.5). Replaces the thin
  `integrating-qa-in-scripts.mdx` ("apis") page.
- **Storage layout** — where data lives (`storage/where-is-the-data-saved.mdx` is solid;
  resolve its TODOs).

### 2.4 Admin Guide (rewrite; generic-first, site overlays as appendix)

Today's admin docs are four pages of which three are SIRC runbooks (`ssh qa`,
`-f sirc.yml`, `/home/ispq/...`, uid `1411:10`, Samsung IT CSR forms). Proposed structure:

1. **Architecture** — service diagram from `docker-compose.yml`: proxy (nginx), backend
   (Flask/uWSGI), frontend, postgres, rabbitmq + flower, cantaloupe (IIIF), redis, pgadmin;
   which are optional (`profiles: [disabled]`).
2. **Install & first boot** — from `start-server.mdx`; fix the `mkdir -p /mnt/qabaord`
   typo (appears twice in copy-paste blocks), document default port 5151, and add the
   post-install smoke test.
3. **Configuration model** — *new page, the key missing concept*: the compose overlay
   layering (`docker-compose.yml` + `production.yml`|`development.yml` +
   `deployments/<site>.yml`), `.env` interpolation, build args, and the new runtime
   `/api/v1/config` endpoint (what's runtime-configurable vs still a frontend build arg).
   `deployments/example.yml` becomes the documented template.
4. **Users & authentication** — LOCAL / LDAP / SAML from `managing-users.mdx`, plus a
   documented **first-admin bootstrap** (today: "insert a DB row or un-comment the signup
   endpoint" — needs a small product fix, e.g. a `flask create-admin` command, then docs).
5. **Git hosting integration** — GitLab *and* GitHub side-by-side: webhook URLs, token
   scopes (`GITLAB_ACCESS_TOKEN` vs `GITHUB_ACCESS_TOKEN`), Enterprise variants, avatars;
   plus the current limitations from `docs/github-support-roadmap.md` (no commit-status
   to GitHub yet, no Actions dispatch, **no webhook signature verification** — flag as a
   security caveat until fixed).
6. **Storage & image serving** — mounts, the nginx `/s/` serving pattern (documented; today
   it's a comment in `services/nginx/conf.d/qaboard.conf`), cantaloupe sizing, multiple
   image servers (`QABOARD_IMAGE_SERVERS`). Flag and fix the `/:/repository` host-root
   mount (already marked FIXME in compose).
7. **HTTPS & reverse proxy** — generic guide (own certs, Let's Encrypt, or behind an
   existing proxy; SAML's `X-Forwarded-*` requirements). Retire the Samsung-IT CSR page
   (`getting-SSL-certificates-from-IT.mdx`) with a redirect.
8. **Operations**
   - *Upgrades*: promote `MIGRATION.md` content into the docs; document that
     `init.sh` runs `alembic upgrade head` on start and what to do when it can't.
   - *Backups & restore*: `cron-backup-db` in `production.yml` (make it opt-out or at
     least prominent), `services/db/backup|restore`, generic restore runbook
     (replace SIRC paths in `host-upgrades.mdx`).
   - *Data cleanup*: per-project GC (`storage.garbage.after`), `qaboard_clean` cron,
     milestone protection; document the "GC requires a git-host integration" caveat.
   - *Monitoring*: flower, Sentry, PostHog, logs (`docker compose logs`); note fluentd is
     a site-specific overlay.
   - *Sizing & tuning*: uWSGI processes/cheaper, cantaloupe memory, postgres.
9. **Security hardening checklist** — *new*: change `SECRET_KEY`, postgres and pgadmin
   default credentials, restrict/disable the proxy endpoints (§6), webhook secrets,
   don't expose flower/pgadmin publicly.
10. **Troubleshooting** — genericized `troubleshooting.mdx` (logs, shells, rebuilds,
    disk-full/IIIF-cache recovery) without `-f sirc.yml` / `at-sirc-before-up.py`.
11. **Appendix: site overlays** — the SIRC/DSK overlays as *reference implementations*
    of the pattern; consider moving truly internal runbooks out of the public site.

### 2.5 Developer (small, new)

- **Architecture for contributors** — promote the accurate content in `CLAUDE.md`
  (compose dev env, `development.yml`, webapp dev server, `uv`, test/lint commands).
- **Contributing** — refresh `CONTRIBUTING.md`; replace the dead Spectrum chat link.
- **Custom visualizations** — the topic `drafts-contributing.txt` gestures at, done properly.

---

## 3. Content-hygiene pass (applies across all sections)

- Purge internal hostnames from public pages: `https://qa`, `http://qa-docs`,
  `gitlab-srv`, `dag.sirc.co.il`, `/stage/algo_data`, `/home/ispq`.
- Fix shipped typos in copy-paste commands (`mkdir -p /mnt/qabaord`,
  MIGRATION.md `up up`) and the misspelled public slug (redirect via plugin).
- Reconcile contradictions: README's `deployments/sirc.yml` vs actual
  `deployments/sirc/sirc.yml`; docs' `/mnt/qaboard` vs template's `/stage/algo_data/ci`;
  `inputs.mdx` "local by default" vs template's "LSF by default".
- Resolve or remove the ~8 inline "Work in Progress"/TODO stubs; either finish
  (`--share` default) or state the limitation plainly.

---

## 4. API: issues to fix and quick wins

Surveyed: single Flask app, ~30 routes under `/api/v1` (plus unversioned `/webhook/*`),
no blueprints, no OpenAPI/Swagger anywhere, only prose doc is `integrating-qa-in-scripts`.

### Security (fix before documenting)

1. **Open SSRF proxies**: `GET /api/v1/gitlab/proxy` fetches any `?url=`
   (`backend/backend/api/integrations.py:117`) and `POST /api/v1/webhook/proxy` sends an
   arbitrary method/url/Basic-auth request (`integrations.py:137`) — both unauthenticated,
   both `verify=False`. Add host allow-lists + auth, or remove.
2. **Unauthenticated destructive endpoints**: `DELETE /api/v1/batch/<id>`
   (`api/batch.py:158`), `DELETE /api/v1/commit/.../batches` (`api/webhooks.py:17`),
   output PUT/DELETE/redo (`api/outputs.py`). Only `commit.py`'s GET checks authorization.
3. **Webhooks unauthenticated**: `/webhook/github` should verify `X-Hub-Signature-256`
   (already on the roadmap); `/webhook/gitlab` should check the secret token. Both also
   accept GET.
4. **Wildcard CORS on credentialed routes**: `CORS(app)` at `backend/__init__.py:65`
   covers the cookie-login `/api/v1/user/*`; scope to explicit origins.
5. **`verify=False` on outbound TLS** throughout `integrations.py` and the Sentry transport.

### Docs-enabling quick wins

6. **Write an OpenAPI spec** (flask-smorest/apispec, or a hand-written `openapi.yaml`
   to start) and render it in the docs. The surface is small (~30 routes) and already
   consistently under `/api/v1`.
7. **Kill the trailing-slash double-decorators** (~20 endpoints × 2 routes) with
   `app.url_map.strict_slashes = False`.
8. **One JSON error envelope** — today: plain-string `"ERROR ..."` bodies, strings that
   embed the status code, and `jsonify({"error": ...})` all coexist.
9. **Fix invalid webhook responses** — `"{status:'OK'}"` literal (single quotes, not JSON)
   at `api/webhooks.py:51,71`.

### Hygiene

10. **GET-with-side-effects**: `GET /api/v1/export` writes to the filesystem
    (`api/export_to_folder.py:179`); `GET /api/v1/output/diff/report` generates a PDF
    (`api/image.py:171`). Make POST-only.
11. **Dead code**: `api/auto_rois.py` (never imported, duplicates `image.py` routes,
    hardcodes `/algo/qa_db/image_cache`); duplicate route registration in
    `scripts/user_storage_server.py:89,93`.
12. **Hardcoded internal paths in live code**: `api/image.py:45` cache-dir override,
    `clean.py` `/algo/*` paths, `utils.py` `gitlab-srv.transchip.com` and SIRC avatar host.
13. **Naming/pagination** (lower priority): singular/plural drift (`/commit` vs
    `/commits`, `/tests/group` POST-as-read), no pagination on `/projects` / `/commits`.

---

## 5. Suggested phasing

| Phase | Scope | Outcome |
|---|---|---|
| **P0 — stop the bleeding** (days) | Fix `sample_project/qaboard.yaml` defaults; fix `mkdir /mnt/qabaord` + broken code samples; document GitHub webhook setup; publish MIGRATION.md content; add redirects plugin | A new user's first hour works |
| **P1 — restructure** (1–2 wks) | New sidebar (4 tracks), local + server quickstarts, `qaboard.yaml` reference page, consolidated env-var reference, de-Samsung config | Onboarding path is linear and self-service |
| **P2 — admin guide + recipes** (2–4 wks) | Admin rewrite per §2.4 (incl. first-admin bootstrap fix), 3–4 CI-tested example projects, security hardening checklist | Outside admins can deploy and operate without reading compose files |
| **P3 — API** (parallel) | Security fixes §4.1–5, OpenAPI spec + rendered reference, error-format cleanup | HTTP API becomes a documented, safe integration surface |
