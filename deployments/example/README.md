# The example site
A complete site folder: copy it into your **site directory**, the private repository that holds your site's
configuration, cloned next to this one. Docs: website/docs/backend-admin/site-directory.mdx.

```bash
git clone https://github.com/Samsung/qaboard
git init qaboard-site && cp -r qaboard/deployments/example qaboard-site/mysite   # then rename "example" in its files
cd qaboard
deployments/deploy.py ../qaboard-site/mysite/production.env up --version <version> --init
```

| File | What | Used by |
|---|---|---|
| `production.env` | An environment: compose files, project, replicas, `QABOARD_SITE` | `deploy.py`, `docker compose --env-file` |
| `example.yml` | The compose overlay: paths are `${QABOARD_SITE_DIR}/example/...` | `COMPOSE_FILE` in `production.env` |
| `.env` (from `.env.example`, not committed) | The site's secrets | `deploy.py`, the backend |
| `before-deploy.sh` | Runs before each deploy (`QABOARD_DEPLOY_PRE_HOOK`) | `deploy.py` |
| `nginx/` | Added to nginx's `conf.d` | the `proxy` service |
| `backend/` | Files mounted in the backend at `/etc/qaboard/site/` | the `backend` service |
| `cli/` | The site package, `qaboard-site-example`: defaults for `qa` and the server, hooks, a base `qaboard.yaml` | users' `pip install`, the backend image (`QABOARD_EXTRA`) |
| `site-docs/` | Pages for the site's users, in the web app's docs | the `website` image (`QABOARD_SITE`) |
| `values.yaml` | Values for the Helm chart | `helm -f` |
| `.dockerignore` | Keeps `.env` out of the image builds: this folder is their `site-config` context | the images |

CI checks that this folder renders with docker compose from outside the repository (`tests/test_site_dir.py`), and that
the backend image installs its site package and the website builds its docs (the `docker` job); it renders the chart with
its `values.yaml` (the `helm` job).
