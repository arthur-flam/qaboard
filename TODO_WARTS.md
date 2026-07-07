# QA-Board warts & suggested fixes

An opinionated list of the rough edges in the codebase, and how to sand them down.

## Fixed on this branch
- **GitHub (and generic git hosts) are first-class citizens**: `/webhook/github` and `/webhook/git` next to `/webhook/gitlab`, a normalized `Project.data['git']` schema (`backend/git_hosts.py`), host-aware cloning credentials (`$GITHUB_ACCESS_TOKEN`, `$GIT_HOSTS`), a data migration (`backend/scripts/migrate_to_multi_git_hosts.py`), commit statuses from `qa batch` on GitHub (`qaboard/git_status.py`), and host-agnostic URL building in the webapp (`git_info()` in `webapp/src/utils.js`).
- **Home page pagination**: `/api/v1/projects` supports `limit/offset/search`, ordered by latest activity; the home page loads 50 projects at a time with debounced server-side search and a "Load more" button.
- **Branches**: `/api/v1/project/branches` supports `filter/limit` and orders by recent activity; the navbar only fetches branches when the menu is opened or searched (debounced), instead of downloading the full list on every page.
- **Commits pagination + fast aggregation**: `/api/v1/commits` supports `limit/offset` and a `search` that matches hash, branch, message, committer and batch labels. Output counts/metrics are aggregated with one lightweight query (`batch_output_summaries`) instead of loading every `Output` row of every batch. Composite indexes on `ci_commits(project_id, [branch,] authored_datetime)` (alembic migration `a1b7f0e3c9d2`).
- Misc: fixed the dead-code second fetch in `fetchCommits` (was never dispatched), the navbar date-range fetch ignoring hour normalization, `default_commits_data.date_range` holding the `default_date_range` *function* instead of a range, the hardcoded `http://gitlab-srv` in `backend/utils.py` and `webapp/src/utils.js`, gravatar over https, public avatars no longer routed through the authenticated gitlab proxy, and removed the company-specific `CDE-Users/HW_ALG` hack in `api.py`.

## Security (high priority)
- [ ] **Webhook endpoints are unauthenticated.** Verify `X-Gitlab-Token` and GitHub's `X-Hub-Signature-256` HMAC against a `$QABOARD_WEBHOOK_SECRET`; reject otherwise.
- [ ] **`/api/v1/gitlab/proxy?url=` is an open proxy (SSRF).** It fetches any URL with the server's gitlab session cookie attached. Restrict to the avatar paths of known git hosts (the hosts stored in `Project.data['git']`).
- [ ] **`/api/v1/webhook/proxy` forwards arbitrary requests with `verify=False`.** Same fix: allow-list target hosts from project configs, re-enable TLS verification.
- [ ] **No authorization on destructive endpoints.** `flask_login` is wired up (`api/auth.py`) but nothing uses `login_required`: anyone can DELETE batches/commits or trigger CI jobs. Add opt-in auth enforcement per instance.
- [ ] **`fill_template` in the webapp uses `new Function`** to evaluate `${...}` templates from qaboard.yaml — effectively `eval` of repo-controlled strings. Replace with a safe interpolation (regex lookup in the context object).
- [ ] `gitlab_session_cookie()` screen-scrapes the GitLab login form to get a session cookie (fragile, breaks on GitLab upgrades). Prefer avatar fetching with a `Private-Token`, or cache avatars server-side.

## Backend
- [ ] **Stop cloning every repository server-side.** `update_project` needs a full clone per project to read `qaboard.yaml` files; with hundreds of projects it costs disk/CPU and needs tokens. Use the host's contents API (GitLab `repository/files`, GitHub `contents`) — the normalization layer in `git_hosts.py` is the natural place for per-host implementations.
- [ ] **`get_users_per_name` downloads *all* GitLab users** (admin-only API) into an in-process cache, and is called from `CiCommit.to_dict()`. Store committer→avatar in the `users` table (filled from webhook payloads which include author info) and drop the API sweep.
- [ ] **Pre-aggregate output counts.** `batch_output_summaries` is a big improvement, but counts could be maintained incrementally (columns on `batches` updated when outputs change, or a trigger) so listing commits never touches `outputs`.
- [ ] **`CiCommit.get_or_create` matches `hexsha.startswith(...)`** — a short prefix can raise `MultipleResultsFound` and creates ambiguity. Require full hashes on write; resolve prefixes only on read.
- [ ] **`Project.milestone_commits` requires live git access** on every batch deletion (and `repo.tags` iteration). Store milestone commits in the DB when milestones are defined.
- [ ] **Schema management is split**: `Base.metadata.create_all(engine)` runs at import while alembic owns migrations — fresh installs and migrated installs can drift. Boot should run `alembic upgrade head` instead.
- [ ] **Unpinned dependencies**: `sqlalchemy = "*"`, `flask = "*"`... The code uses `flask._app_ctx_stack` (removed in Flask 2.3) and SQLAlchemy 1.x idioms (`Base.query`). Pin known-good versions, then migrate.
- [ ] `pool_size=100` per uwsgi worker can exhaust postgres connections; use a smaller pool + pgbouncer.
- [ ] Dead/unused columns: `CiCommit.parents`, `commit_type`. Drop them with a migration.
- [ ] `db_echo = bool(os.getenv('QABOARD_DB_ECHO', False))` is truthy for `"false"` — parse env booleans properly.
- [ ] Bare `except:` clauses everywhere swallow `KeyboardInterrupt`/`SystemExit` and hide bugs — catch `Exception` and log.

## Webapp
- [ ] **Redux state grows without bound**: commits accumulate in `state.commits[project][id]` and are persisted by redux-persist (localStorage quota!). Evict commits not referenced by any branch bucket, and blacklist `commits` from persistence.
- [ ] **Polling**: `CiCommitList` refetches every 60s even in background tabs. Pause on `document.visibilityState === 'hidden'`; longer-term, push updates over SSE/websocket.
- [ ] `batchSelector` re-aggregates every output on each state change — memoize per (commit, batch) or use the server-side aggregation now available.
- [ ] No central API client: each thunk builds URLs by hand, error handling is inconsistent. Add a small `api.js` wrapper (base URL, error normalization, aborts on unmount).
- [ ] Aging toolchain: CRA 5 + react-app-rewired + React 17, TypeScript installed but unused. Consider Vite + incremental TS.
- [ ] The branch `<Suggest>` in the navbar doubles as the commit search box — two features in one input is confusing. Split "jump to branch/commit" from "filter commits".
- [ ] Home page favorites are stored only in the browser (redux-persist). Store per-user favorites server-side now that auth exists.

## CLI (`qaboard/`)
- [ ] `qaboard/gitlab.py` still owns "wait for CI to pass" (`lastest_successful_ci_commit`, used by bit-accuracy checks) — gitlab-only. Move it behind `git_status.py` with a GitHub implementation (checks API).
- [ ] Credentials come from env vars and a `secrets` file with no precedence documentation. Document, and support per-host tokens like the backend's `$GIT_HOSTS`.
- [ ] `sample_project/qaboard.yaml` mentions `remote_type: gitlab | github | gitea` — now actually honored by `git_status.py`; document it in the website docs.

## Ops / deployment
- [ ] `docker-compose` images build everything from scratch; publish versioned images.
- [ ] The backend prints to stdout instead of structured logging; no request IDs. Adopt `logging` with per-request context.
- [ ] No health/readiness endpoints beyond the implicit `/`; add `/healthz` checking DB connectivity for orchestrators.
