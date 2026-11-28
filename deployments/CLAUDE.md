# deployments/CLAUDE.md
Deploying the server: compose env files, `deploy.py`, the example site (`example/`), and the Helm chart in
`charts/qaboard/` (`charts/CLAUDE.md`). Generic server docs: `website/docs/backend-admin/`; what's still
site-specific: `docs/known-issues.md`; limits: `docs/warts.md` (Deployments, services, charts).

## Compose
```bash
# Zero-downtime deploys, one env file per environment (compose files, project, replicas, QABOARD_IMAGE...)
deployments/deploy.py deployments/production.env up [--version X] [--build] [--init]
deployments/deploy.py [--site-dir DIR] <env file> status|rollback|restore-db|seed-db|compose ...
```
- `production.env` is the generic single-host deployment (CI deploys it in its `docker` job); `quickstart/compose.yml`
  the laptop quickstart.
- **Site directories** (`website/docs/backend-admin/site-directory.mdx`): a site's env files, overlay, nginx config,
  site package (`cli/`), site docs, Helm values and runbooks live in a folder of its own repository, cloned next to
  this one. `deploy.py` finds it (`--site-dir`, `QABOARD_SITE_DIR`, else the folder above an env file outside the
  repository, else `deployments/`) and exports `QABOARD_SITE_DIR`: overlays write `${QABOARD_SITE_DIR}/<site>/...`.
  The `.env` and `.deploy-state.*.json` of such an env file are next to it. The images get the site's folder as the
  `site-config` build context (`backend/Dockerfile`, `website/Dockerfile`).
- `example/` is a complete site using every hook point: keep it working, `tests/test_site_dir.py` and the `docker` and
  `helm` CI jobs check it from outside the repository. A new hook point goes in it, with a check.
- SIRC and DSK: their site repository, `qaboard-deployments` (`sirc/`, `dsk/`), with their GitLab pipeline and
  runbooks. Never add a site's data here (hostnames, paths, certificates, people): the repository is public.

## Images
GitHub CI publishes the images to `ghcr.io/<owner>/qaboard`, tagged `<service>-<sha8>` on master, `<service>` once
every job passed, `<service>-<version>` for releases; and the chart to `oci://ghcr.io/<owner>/charts/qaboard`.
Compose and the chart default to `ghcr.io/samsung/qaboard`; `QABOARD_IMAGE` / `image.repository` override it.
