# Known issues after the branch unification

Open-source, SIRC and DSK now share `master`. This lists what is still site-specific or rough,
roughly by priority. None of it breaks the open-source version.

## Deployment configs live in their own repository
SIRC's and DSK's configuration (overlays, env files, nginx, site packages, certificates, `.envrc`, site docs,
runbooks, the GitLab pipeline) is in private repositories, `qaboard-site-sirc` and `qaboard-site-dsk`, cloned next to
this one: their *site directory* (`QABOARD_SITE_DIR`, website/docs/backend-admin/site-directory.mdx). Its README has
the day-to-day commands and the migration runbook. `deployments/example/` is the template, checked by CI.
- **Still in this repository's git history**: removing the folders from HEAD doesn't purge them. Paths to purge, if
  wanted (a history rewrite of every clone and fork): `deployments/sirc/`, `deployments/dsk/`,
  `website/site-docs/sirc/`, `website/site-docs/dsk/`, `services/backend/passwd`, `services/backend/ldap/`,
  `services/nginx/passwd`, `backend/backend/api/saml/settings.json`, `backend/backend/api/saml/dev_settings.json`,
  the IdP certificates in older versions of `backend/backend/api/saml/settings_example.json`, `at-sirc-before-up.py`,
  `.gitlab-ci.yml`, the SIRC scripts that were in `backend/backend/scripts/` (migrate.py, migration_utils.py,
  gen_parallel_migration.py...), the HW_ALG folders in older `backend/backend/clean.py`, the quota URL in older
  `backend/backend/scripts/user_storage_server.py`, and the SIRC path mappings in older `tests/test_compat.py`.

## cde-python (was a git submodule)
`third_party/cde-python` was unused: images and the SIRC site package install `cde` with pip
(`CDE_PACKAGE` build arg, `qaboard-site-sirc` dependency). To hack on it locally:
```bash
git clone <cde-python repository> ../cde-python
uv pip install -e ../cde-python
```

## SIRC-specific logic still in shared code
Harmless for other sites (it only triggers on SIRC project names, paths or files), but could become settings:
- webapp, tuning form (`components/tuning/forms.jsx`): pasted text with `=` rewritten as CDE registers (documented as
  a generic `key=value` paste). HW_ALG, the only active SIRC project relying on project-specific logic, sets its branch
  rule in its own qaboard.yaml: `tuning.branches: [develop, '*/*{subproject.name}*']`.
- webapp: the WebCDE button when outputs contain a `cde.sh` (`components/tags.jsx`), with `QABOARD_WEBCDE_INSTALLER`.
- webapp: projects with `legacy` (`migrate_legacy_commits.py`) are hidden from the projects lists (`is_listed` in
  `explore/tree.js`).
- webapp: a few docs links bypass `docs_root` (`tuning/optimize_config.js`, `login/SetupPage.jsx`).
- CLI: `CDE-Users/HW_ALG` manifest rules (`utils.py`), `WEBCDE_CONNECTION_ID` in the celery runner, `idb.py`.
  (The `vol23_algo` → `vol24_algo` rewrite is now SIRC's `QABOARD_PATH_REWRITES`, in its site package.)
- backend: `PYTHONWARNINGS` hides TLS warnings for everyone (`backend/Dockerfile`). (DSK's SAML settings, the
  HW_ALG folders of the untracked-folders cleanups and the one-off SIRC scripts are in the site repository.)
- services: nginx runs with the `uucp` group. (The passwd files and LDAP config are in the site repository.)

## Ops
- The cantaloupe admin endpoint is enabled with `qaboard`/`qaboard`. It is only reachable inside the compose network.
- Base images (November 2026): Debian 13 trixie (backend, nginx), Alpine 3.24 (cantaloupe, vips, the web app's and
  website's rsync images), Node 24. The backend runs Python 3.14.
- Cantaloupe 5.0.8 (Java 21) ships a `logback.xml` that logs nothing with its Logback 1.6:
  `services/cantaloupe/logback.xml` is upstream's fix, put in the jar by the Dockerfile. Drop it with the next release.
- RabbitMQ (`rabbitmq:3-management`, compose and chart) is EOL. 4.x needs `rabbitmqctl enable_feature_flag all` on
  3.13 first, and has no classic mirrored queues.
- The baseline migration is empty: a new database can't be built by `alembic upgrade head`. The app creates the
  tables when it's imported (`Base.metadata.create_all`), and `migrate.sh` stamps a new database (no data, no version)
  at the head. A database with data but no version makes `migrate.sh` fail: stamp it by hand at the migration it has.
- PostgreSQL 12 is EOL (Nov 2024). Upgrade with dump/restore, e.g. when moving to kubernetes (CloudNativePG uses 16).
- Deploys need docker compose ≥ 2.24 on the hosts and on the GitLab runner (`${VAR:+...}` interpolation,
  `pull --policy`). SIRC's hosts run Docker Engine 24 (API 1.43): fine, but not healthcheck `start_interval`.
- With docker compose, nginx is restarted (~1s of refused connections) when the *proxy image* changes.
  Config changes are reloaded without a restart, and new versions with the same image don't restart it.
- SIRC staging uses the shared storage, the LSF bridge and `/home/ispq/qaboard/shared` like production.
- The SIRC GitLab pipeline was written without access to SIRC's GitLab/runners: check on the first run that
  the registry is enabled (else `QABOARD_BUILD_ON_HOST=1`), and the one-time host setup in
  `sirc/runbooks/deployment.md` of the site repository.
- Publishing:
  - PyPI: create a GitHub release with a tag matching the version (`pypy.yml`). Needs trusted publishing
    configured on PyPI, or a `PYPI_API_TOKEN` secret.
  - GitLab's manual `publish:PyPi` job (twine from planet31) is redundant with the GitHub release.
  - Docker images: `publish-docker-images` pushes to ghcr.io with `GITHUB_TOKEN`; Docker Hub is a mirror only with
    `DOCKERHUB_USERNAME`/`DOCKERHUB_TOKEN` secrets.
- The website deploy workflow only runs on master pushes that touch what the site builds from (`paths:` in
  `website.yml`: `website/`, the chart, examples, the shots...).
- The helm chart (`charts/qaboard`) runs on kind in CI (`helm-e2e`), not yet on a real cluster.
  SIRC's `sirc/values.yaml` has `TODO(sirc)`s, see `website/docs/backend-admin/kubernetes.mdx`.
