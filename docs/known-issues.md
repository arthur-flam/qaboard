# Known issues after the branch unification

Open-source, SIRC and DSK now share `master`. This lists what is still site-specific or rough,
roughly by priority. None of it breaks the open-source version.

## cde-python (was a git submodule)
`third_party/cde-python` was unused: images and the SIRC site package install `cde` with pip
(`CDE_PACKAGE` build arg, `qaboard-site-sirc` dependency). To hack on it locally:
```bash
git clone git@gitlab-srv:cde/cde-python.git ../cde-python
uv pip install -e ../cde-python
```

## SIRC-specific logic still in shared code
Harmless for other sites (it only triggers on SIRC project names, paths or files), but could become settings:
- webapp, tuning form (`components/tuning/forms.jsx`): pasted text with `=` rewritten as CDE registers (documented as
  a generic `key=value` paste). HW_ALG, the only active SIRC project relying on project-specific logic, sets its branch
  rule in its own qaboard.yaml: `tuning.branches: [develop, '*/*{subproject.name}*']`.
- webapp: the WebCDE button when outputs contain a `cde.sh` (`components/tags.jsx`), with `QABOARD_WEBCDE_INSTALLER`.
- webapp: `defaults.js` uses `reference_branch: 'develop'`, the CLI defaults to `master`.
- webapp: projects without `project.name` in their qaboard.yaml, or with `legacy` (`migrate_legacy_commits.py`), are
  hidden from the projects list.
- webapp: `package.json` has `"proxy": "http://qa:5001"` for the dev server; a few docs links bypass `docs_root`;
  the projects list links to spectrum.chat, which is gone.
- CLI: `\\netapp\vol23_algo` → `vol24_algo` rewrite (`compat.py`), `CDE-Users/HW_ALG` manifest rules (`utils.py`),
  `\\netapp` shares in the Jenkins Windows runner, `WEBCDE_CONNECTION_ID` in the celery runner, `idb.py`.
- backend: DSK's SAML settings are in `backend/backend/api/saml/settings.json` (DSK must set `QABOARD_SAML_DIR`),
  HW_ALG cleanup commands (`clean.py`), one-off SIRC scripts in `backend/backend/scripts/`,
  `PYTHONWARNINGS` hides TLS warnings for everyone (`backend/Dockerfile`).
- services: nginx runs with the `uucp` group.

## Ops
- The cantaloupe admin endpoint is enabled with `qaboard`/`qaboard`. It is only reachable inside the compose network.
- Base images: cantaloupe uses `alpine:3.10`, nginx uses Debian buster from archive.debian.org. Both are EOL.
- On a fresh database `alembic upgrade head` fails (the baseline migration is empty). `migrate.sh` falls back to
  `alembic downgrade head || alembic stamp head` and the app creates the tables, so it works, but it's noisy.
  Worse: the fallback also hides real migration failures, so `deploy.py`/the helm Job can't stop a deploy on them.
  Fix the baseline, then remove the fallback.
- PostgreSQL 12 is EOL (Nov 2024). Upgrade with dump/restore, e.g. when moving to kubernetes (CloudNativePG uses 16).
- Deploys need docker compose ≥ 2.24 on the hosts and on the GitLab runner (`${VAR:+...}` interpolation,
  `pull --policy`).
- With docker compose, nginx is restarted (~1s of refused connections) when the *proxy image* changes.
  Config changes are reloaded without a restart, and new versions with the same image don't restart it.
- With `QABOARD_LOGIN_RESTRICTED`, a missing or unreadable `QABOARD_LOGIN_RESTRICTED_YAML` is silently ignored
  (`backend/backend/api/auth.py`): restricted projects become visible to everyone. It should fail closed.
- Publishing:
  - PyPI: create a GitHub release with a tag matching the version (`pypy.yml`). Needs trusted publishing
    configured on PyPI, or a `PYPI_API_TOKEN` secret.
  - Docker images: `publish-docker-images` needs `DOCKERHUB_USERNAME`/`DOCKERHUB_TOKEN` secrets.
- CI tests the CLI on Python 3.11 only, but `requires-python` says `>=3.7`.
- The website deploy workflow only runs on master pushes that touch `website/`.
- The helm chart (`charts/qaboard`) is validated (lint, kubeconform) but was not run on a cluster yet.
