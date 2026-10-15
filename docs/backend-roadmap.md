# Backend roadmap (October 2026)

What to do with the backend, in which order, and how. Based on a survey of the code in October 2026.
Line numbers drift: search for the quoted code. Smaller bugs are tracked in [TODO_WARTS.md](../TODO_WARTS.md)
and [known-issues.md](known-issues.md); this file only repeats the ones that change the plan.

Effort: **S** = hours, **M** = days, **L** = weeks (one engineer, agent-assisted).

## TL;DR

1. **Weeks 1-2, stop the bleeding.** Rotate a leaked credential, close the broker/RCE and authz holes,
   make sure Postgres actually uses its config, fix Sentry. Turn on measurement (`pg_stat_statements`, slow-query log, traces).
2. **Weeks 2-6, database.** Upgrade PostgreSQL 12 → 18 in place (`pg_upgrade --link`, minutes of downtime),
   fix the pool math, then the SQL quick wins driven by `pg_stat_statements`. Squash migrations into a real
   baseline, check in `schema.sql`, write new migrations in SQL.
3. **Weeks 4-12, foundations for v2.** FastAPI mounted *next to* Flask in one ASGI process (strangler, no big-bang
   rewrite), a SQL-first data layer on psycopg 3, and the identity model first: users/groups/roles/project members,
   hashed scoped tokens, audit log, OIDC.
4. **Then, features on v2.** Project settings & members UI, admin dashboard, "my runs", storage dashboard,
   OpenAPI docs. The webapp moves page by page; v1 endpoints become thin shims and stay for old `qa` CLIs.

Two honest calls that shape this plan:

- **FastAPI/async will not make QA-Board faster by itself.** The slow parts are SQL (full scans, N+1, JSON as text),
  19 MB responses (the commit page returns every output), and blocking filesystem/git/image work in request handlers.
  Async helps for outbound HTTP (git hosts, Jenkins) and server-sent events, not for those. Move to FastAPI for
  typed request/response models, OpenAPI, dependency-injected auth and getting off uWSGI (maintenance mode) —
  and do it endpoint by endpoint, writing handlers as plain `def` unless they wait on the network.
- **Don't rewrite 18k lines in one go.** Every phase below ships on its own and leaves production better.

## Phase 0: really bad things (weeks 1-2)

Do these before any refactoring. All are **S** unless noted.

### Security
- **A NetApp filer admin credential is committed** (base64) in `backend/backend/scripts/quota.py`, in a public repo.
  Rotate it with IT *first*, then delete the script (it also disables TLS verification process-wide).
  Removing the file doesn't remove it from git history: treat it as leaked.
- **Remote code execution through the broker.** RabbitMQ is published on the host (`docker-compose.yml`, ports 5672/15672)
  with `guest`/`guest`, and the celery runner accepts pickle (`qaboard/runners/celeryconfig.py`) and runs
  `shell=True` commands (`celery_app.py: start`). Anyone on the network can run commands as the workers' user.
  Fix: stop publishing the ports (or bind to 127.0.0.1), a real broker user/password per site, `json` only.
  Flower (published on 8888 and proxied at `/flower/`) has no auth: put it behind nginx `auth_request` like `/s/`.
- **`QABOARD_LOGIN_REQUIRED` is only enforced by the webapp.** Add a `before_request` that requires a session or
  token for every `/api/` route except an explicit allowlist (health, config, login, webhooks with signatures).
- **Any logged-in user can delete/move/rename any project's batches and commits** (`api/batch.py`, `api/webhooks.py`:
  only the `?project=` query param is checked). Call `is_authorized_user(user, batch.ci_commit.project)` on the
  object actually being changed. (The real fix is roles, phase 2.)
- **Anonymous `POST /api/v1/task/celery/<id>`** busy-waits forever (TODO_WARTS top 10 #4). It uses `@app.post`, which the
  AST check in `tests/test_security.py` doesn't see: extend the check to `app.get/post/put/delete`.
- `QABOARD_LOGIN_RESTRICTED="false"` *enables* restrictions (`bool("false")`). Parse booleans in one helper.
- Cookies: set `SESSION_COOKIE_SECURE/HTTPONLY/SAMESITE=Lax` and the `REMEMBER_COOKIE_*` equivalents; restrict
  `CORS(app)` to the configured origins; reduce the 180-day remember cookie.
- Tokens: stop accepting `?token=` (it ends up in nginx logs and Sentry breadcrumbs), or at least scrub it.
- The rest of TODO_WARTS "Top 10" (SSRF in the proxies, anonymous image endpoints writing files) is the same size: do it in this batch.

### Postgres
- **The tuning file is probably not applied.** `services/db/init.sh` copies it to `$PGDATA/postgres.conf`;
  Postgres reads `postgresql.conf`. So production likely runs on defaults: `shared_buffers=128MB`, `max_connections=100`,
  and no `pg_stat_statements`. Check now: `SHOW shared_buffers; SHOW config_file; SELECT * FROM pg_extension;`.
  Fix: mount it as `postgresql.conf`, or better pass `-c config_file=/etc/postgresql/postgresql.conf`.
- **Connection math doesn't fit.** Each uWSGI process has a pool of 10+5 for 4 threads; SIRC prod has 2 replicas × 8 processes
  = 240 connections, ~480 during a rolling deploy. Set `pool_size` = threads (4), `max_overflow=2`, add `pool_pre_ping=True`.
- **Backups:** never pruned, and nothing proves they restore. Add retention, and a weekly job that restores the
  latest dump into a scratch database and runs `SELECT count(*)` on the big tables (a Sentry cron monitor alerts if it doesn't run).
- `Base.metadata.create_all()` runs at every import, and `migrate.sh` falls back to `stamp head` on failure, which hides
  failed migrations from `deploy.py` and the helm Job. Fixed properly in phase 1 (baseline); until then at least log loudly.

### Sentry (checked against the 2026 SDKs)
The setup works but is from 2020. Backend `sentry-sdk` 2.54, webapp `@sentry/react` 11: versions are fine; the config isn't.

| Today | Change |
|---|---|
| `InsecureHttpTransport` with `CERT_NONE` | `ca_certs=` the SIRC CA bundle (already fetched at build time via `CA_CERT_URL`) and delete the transport. Same in the CLI (`QABOARD_SENTRY_VERIFY`). |
| Enabled only if `FLASK_ENV=production` | `FLASK_ENV` was removed from Flask. Enable iff `SENTRY_DSN` is set. |
| No `environment`, no `release` | `environment` from the deploy env file (production/staging), `release=QABOARD_VERSION` in backend, webapp and CLI, so regressions show per deploy. |
| No sourcemaps uploaded (commented out in `sirc.yml`) | Upload them in the frontend image build (`@sentry/vite-plugin`); minified Vite traces are unreadable without them. |
| No scrubbing | `send_default_pii=False`, `set_user({"username": ...})` explicitly, and a `before_send` dropping `token`/`Authorization`/cookies. |
| Webapp traces at 100% | `tracesSampleRate` 0.1-0.2; one env var name for both (`SENTRY_TRACES_SAMPLE_RATE`; the backend reads `SENTRY_SAMPLE_RATE`). |
| Celery workers and cron jobs not instrumented | `sentry_sdk.init` in the worker and in `qaboard_clean`/backup; **Sentry Crons** monitors for the backup, the cleanup and the restore test. |
| No profiling, no DB spans to speak of | `profile_session_sample_rate` (continuous profiling) on a low rate; SQLAlchemy/psycopg spans come for free and show N+1s per endpoint. |

Check the self-hosted Sentry version first: newer SDK features (logs, continuous profiling, span-first tracing) need a recent server.
The CLI's DSN is committed in `deployments/sirc/cli/`: DSNs are not secrets (they're in every browser), so that's fine.

### Measure before optimizing
`pg_stat_statements`, `log_min_duration_statement=500ms`, `auto_explain` for > 2 s, `track_io_timing=on`.
Run `backend/benchmarks/bench_api.py` before/after each phase. Without this, the SQL work in phase 1 is guesswork.

## Phase 1: database (weeks 2-6)

### PostgreSQL 12 → 18, in place
Target **18** (PG 19 is too fresh; the helm chart's 16 should move to 18 too, CloudNativePG supports it).
18 also keeps planner statistics across `pg_upgrade`, so the database is fast right after the upgrade.

Options, for a database of a few hundred GB with 20M+ outputs:

| | Downtime | Risk | Notes |
|---|---|---|---|
| `pg_dump`/`pg_restore` | hours | low | What known-issues.md suggests. Too slow for prod. |
| **`pg_upgrade --link`** | **minutes** | low with a rehearsal and a backup | Recommended. Hard links: no copy, size doesn't matter. Once the new cluster starts, the old one is unusable, hence the backup. |
| Logical replication 12 → 18 | seconds | medium | Needs `wal_level=logical` (a restart), primary keys everywhere, sequences synced by hand. Only worth it if a 15-minute window is impossible. |

How:
1. **Stay on alpine** (`postgres:18-alpine`). The current cluster was initialized with musl's collation; moving to a Debian
   (glibc) image changes text sort order and silently corrupts text btree indexes (hexsha, branch, committer_name...).
   If you do switch libc, `REINDEX` every text index after the upgrade (`amcheck` finds them).
2. Rehearse on staging with a copy of the prod volume (not the 14-day seed: the point is timing on real size),
   using a `pg_upgrade` image that has both versions' binaries (e.g. `pgautoupgrade/pgautoupgrade:18-alpine`, or build one).
3. Prod: stop backend/celery/metabase, `pg_dump -Fc` (fallback), `pg_upgrade --check`, `pg_upgrade --link`,
   start 18 with the fixed config, `vacuumdb --all --analyze-in-stages --missing-stats-only`, smoke test, start backends.
4. Add `deployments/sirc/runbooks/postgres-upgrade.md` and a `deploy.py upgrade-db` command so the next major is routine.

### Config (`services/db/postgresql.conf`)
Size it from the host's RAM; with ~32 GB dedicated: `shared_buffers=8GB`, `effective_cache_size=24GB`, `work_mem=32MB`,
`maintenance_work_mem=2GB`, `random_page_cost=1.1` (SSD/NetApp), `max_connections=150`.
Plus, cheap and high-value:
- roles: `qaboard_app` with `statement_timeout=30s` and `idle_in_transaction_session_timeout=60s`;
  `qaboard_migrate` (owner, no timeout); `qaboard_readonly` for Metabase, people and agents (see below).
- autovacuum on the hot tables: `ALTER TABLE outputs SET (autovacuum_vacuum_scale_factor=0.02, autovacuum_analyze_scale_factor=0.01)`.
- PgBouncer (transaction mode) only if connections are still a problem after the pool fix; with psycopg 3 it's easy to add later.

### Schema as code, migrations in SQL
Agents (and people) are good at SQL when they can read the whole schema in one file and test against a real database.
- **Squash** the 24 migrations into a real baseline generated from production's schema (`pg_dump --schema-only`).
  This fixes the empty baseline, so remove the `stamp head` fallback in `migrate.sh` and `create_all()` at import.
- **`backend/schema.sql` is checked in** and is the reference. CI: `alembic upgrade head` on an empty database, then
  `pg_dump --schema-only` must equal `schema.sql`. That also catches model drift like `outputs.data` (JSONB in prod, JSON in the model).
- **New migrations are raw SQL** (`op.execute("""...""")`), with `CREATE INDEX CONCURRENTLY` in `autocommit_block` as today.
  Keep Alembic as the runner: it's already wired into `deploy.py`, the helm Job and the `flock`. A new tool
  (dbmate, Atlas, sqitch) mostly re-does that wiring; reconsider only if you want declarative diffs (Atlas — check which lint
  features are in the free edition).
- Expand/contract for anything a running old backend reads (rolling deploys run old and new side by side).

### SQL quick wins (validate each with `pg_stat_statements` first)
- `/api/v1/projects` does a `GROUP BY` over all `ci_commits` on every call: keep `latest_commit_at`/counts on `projects`
  (updated at ingest) or a materialized view refreshed every minute.
- Search: `CREATE EXTENSION pg_trgm` and GIN trigram indexes for the `ILIKE '%term%'` on message/branch/committer/label.
- hexsha prefix lookups (`startswith` → `LIKE 'abc%'`) can't use the default btree index with a non-C collation:
  add `(project_id, hexsha text_pattern_ops)` or query a range.
- `outputs.metrics` is `json` text, re-parsed per row and per key in every aggregation: convert to `jsonb`
  (on 20M+ rows: new column, backfill in batches, swap — not one `ALTER`).
- `compare.find_branch_commit` lazy-loads every batch and every output of up to 50 commits to compute `has_results`:
  one `EXISTS` query.
- Ingest (`POST /api/v1/output`, the hottest write): one transaction instead of 2-3, a unique key on outputs plus
  `INSERT ... ON CONFLICT` instead of a JSONB-equality lookup (and the duplicates it lets through), batches selected by label in SQL.
- Commits in loops (`api/batch.py`, `webhooks.py`, `Batch.stop`): set-based `UPDATE`/`DELETE`, then the filesystem work in a background job.
- Drop the redundant indexes (`ix_ci_commits_project_id`, duplicate `output_dir_override`).
- `clean.py`: Python `bool()`/`not` on SQL columns make filters constant; a `Query` is always truthy so artifacts are never deleted
  (TODO_WARTS #7). Fix, then expect a big first run: watch bloat, `VACUUM` after.
- Not needed yet: partitioning. 20M rows is ordinary for Postgres with the right indexes. Revisit at ~200M or for retention by month.

## Phase 2: foundations for v2 (weeks 4-12, overlaps phase 1)

### FastAPI next to Flask, one process
- One ASGI app: FastAPI owns `/api/v2/*`, and the Flask app is mounted for everything else (`a2wsgi.WSGIMiddleware`).
  Served by uvicorn (or granian) workers; drop uWSGI. One deploy, one image, no nginx routing change.
- Sessions are shared: v2 reads the Flask session cookie (same `SECRET_KEY`) until login itself moves to v2.
- Pydantic models for every request/response → OpenAPI 3.1 for free; errors as RFC 9457 `application/problem+json`;
  cursor (keyset) pagination everywhere.
- Handlers are sync `def` by default (FastAPI runs them in a thread pool); `async def` for outbound HTTP (git hosts, Jenkins)
  and for SSE (live run status).
- Move blocking work out of requests: `rmtree` on deletes, `git pull` on push webhooks, export, image diff reports.
  Simplest: a `jobs` table consumed with `SELECT ... FOR UPDATE SKIP LOCKED` (or procrastinate, which does that).
  It's visible in SQL, it's transactional with the change that enqueues it, and it doubles as the data for the runs dashboard.
  Keep Celery/RabbitMQ for what it does today (running users' jobs), separately.

### SQL-first data layer
- psycopg 3 (binary wheels: removes the source build of psycopg2 from the image; sync + async; `COPY`; pipeline mode).
- Queries are SQL strings in `backend/db/queries/*.py` (or `.sql` files), one function per query, rows mapped to
  Pydantic models with `class_row`. No ORM in v2 code; SQLAlchemy stays only under v1 until v1 code is gone.
- Tests run against a real Postgres (already the case with `QABOARD_TEST_DATABASE_URL`); add a test that `EXPLAIN`s the hot queries
  on the benchmark dataset and fails on a seq scan of `outputs`.
- `qaboard_readonly` role + `schema.sql` + a few documented views (`v_runs`, `v_storage_by_user`) = agents and Metabase can answer
  questions on production data safely. Prefer a replica if you have one.

### Identity, permissions, tokens, audit (do this before any dashboard)
Everything user-facing in phase 3 needs "who is this, what may they do in this project".

Tables:
- `users` (+ `is_admin`, `disabled_at`, `last_login_at`), `groups`, `group_members` (synced from LDAP groups / OIDC claims, plus manual).
- `project_members(project_or_namespace, principal_type user|group, principal_id, role)` with roles
  **viewer < contributor (runs, tags) < maintainer (deletes, settings, members) < owner**. Namespaces inherit (`CDE-Users/` applies to
  `CDE-Users/HW_ALG`), like today's longest-prefix rules. A site default role (e.g. everyone is viewer) keeps today's open behaviour.
  Import `users_restrict.yml` with a one-off script, then delete the YAML mechanism.
- `api_tokens`: store a **SHA-256 hash**, show the secret once, prefix (`qab_...`) so secret scanners catch leaks, `scopes`
  (`read`, `runs:write`, `admin`), `expires_at` (default 1 year), `last_used_at`, `revoked_at`; list/revoke in the UI.
  **Project service tokens** for CI, so pipelines stop using someone's personal token. Migrate existing plaintext tokens by hashing them in place.
- `audit_events(at, actor, via token|session, action, project_id, target, ip, user_agent, details jsonb)`, insert-only, written in the same
  transaction as the change. Every delete/move/rename/redo/settings/members/token change. Monthly partitions so retention is `DROP`.
- Logins: add **OIDC** (Authlib) — covers Entra ID, Keycloak, Okta, GitLab as an IdP — next to LDAP/SAML; LDAPS; validate SAML RelayState;
  rate-limit password logins; server-side sessions (Redis) so admins can end sessions.
- Authorization is one FastAPI dependency: `require(project, "maintainer")`. One place, one test matrix.

## Phase 3: features on v2 (from ~week 8)

In order of dependency:
1. **Project settings & members in the UI.** Split cleanly: what describes the *code* (inputs, metrics, runners, outputs) stays in
   `qaboard.yaml` in git; what's *operational* moves to the database and the UI (members/roles, retention/garbage policy, integrations,
   visibility, quotas, notifications). A key lives in one place only; the UI shows the git-defined ones read-only.
2. **"My runs" dashboard** (ongoing and past, per user, filterable by project/status): `outputs` already has
   `is_pending/is_running/is_failed`; add who started it and a `(user, created_date)` index, live updates by SSE fed by `LISTEN/NOTIFY`.
3. **Storage dashboard for users** (replaces the Metabase link): a `storage_usage(day, project, user, bytes)` rollup refreshed by a job,
   not `SUM()` over 20M rows per page view. Shows what `qaboard_clean` will delete and when.
4. **Admin dashboard:** users and tokens, projects and their owners, storage per project, runner queues/Flower data, DB size and
   slowest queries (`pg_stat_statements`), backup/restore-test status, audit log search.
5. **API docs:** OpenAPI rendered in the website (Scalar or Redoc page under Project integration), a generated TS client
   (`openapi-typescript`) for the webapp. Keep the `qa` CLI on stable, versioned endpoints: it runs against older servers and vice versa.

## Phase 4: retire v1
- Count v1 traffic per route and per `qa` version (add a `User-Agent: qaboard/<version>` header in the CLI now, so the data exists later).
- Webapp pages move to v2 one by one; v1 handlers that remain become shims calling the v2 query functions.
- The commit endpoint is the big one: split into commit summary + paginated outputs (today ~19 MB for 25k outputs).
- When v1 traffic is only old CLIs: freeze v1, remove SQLAlchemy and Flask.

## Other improvements worth slotting in
- Structured JSON logs with a request id (also passed to Sentry), instead of `print()`.
- Prometheus `/metrics` (request latency per route, pool usage, job queue depth) if SIRC has Prometheus; otherwise Sentry is enough.
- `ruff` + `pyright` on `backend/`, typed v2 code from day one.
- One `settings.py` (pydantic-settings) for every env var, with validation at startup instead of `os.getenv` scattered around.
- A `readonly` mode switch for the upgrade window and incidents.

## Suggested sequencing at a glance

```
week   1   2   3   4   5   6   7   8   9  10  11  12  ...
P0     ████████                                            security, pg config, Sentry, measure
P1         ████████████████████                            pg18, pool, SQL wins, baseline+schema.sql
P2                 ████████████████████████████            FastAPI shell, psycopg3 layer, identity/tokens/audit, OIDC
P3                                 ████████████████████…   settings/members UI → my runs → storage → admin → docs
P4                                             ░░░░░░░░…   retire v1 as traffic drops
```

Critical path: measurement (P0) → SQL wins (P1); baseline/schema.sql (P1) → new tables (P2); identity (P2) → every dashboard (P3).
The PG upgrade is independent of the rest: do it as soon as the staging rehearsal passes.
