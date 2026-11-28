# Upgrading PostgreSQL 12 to 18 (SIRC)

SIRC's plan. The generic version is on the docs website:
[Upgrading PostgreSQL](https://samsung.github.io/qaboard/docs/backend-admin/deployment#upgrading-postgresql)
(source: `website/docs/backend-admin/deployment.mdx`).

**Why**: PostgreSQL 12 is end-of-life since November 2024: no security fixes. 18 is the current major version.

**How**: `deployments/deploy.py <env file> upgrade-postgres` stops QA-Board, starts the old data with PostgreSQL 12 in
a temporary container, dumps it with 18's `pg_dump` (directory format, in parallel), restores it in parallel into a
new volume (`<project>_db18`), copies the roles, analyzes, and compares the row count of every table between the two.
`services/db/postgres-18.yml` switches the db service (and the backups' `pg_dump`) to 18 and to the new volume.
CI runs the whole procedure on every change of it, then the backend's integration tests on 18 (the `docker` job).

**Safety nets**
- The old volume (`qaboard_prod_db`) is never written: removing the overlay from `COMPOSE_FILE` and deploying goes
  back to 12 with the data of before the upgrade.
- With the overlay but before the restore, `deploy.py up` refuses to start (the new volume would be an empty
  database). An automatic deploy that runs too early fails instead of starting production empty.
- `upgrade-postgres` stops at the first error, and refuses to run if the new volume exists.

**Downtime**: QA-Board is down from the dump to the end of the analyze. Staging's rehearsal gives the number
(`Dumped, restored and analyzed in N minutes` in its log). Expect more than a daily backup takes, as the restore
rebuilds every index.

Every command below runs on the host, from the checkout, in bash, with the shortcuts of [database.md](database.md):
```bash
ssh qa   # staging: ssh alginfra1, /home/ispq/qaboard_staging, deployments/sirc/staging.env
bash
cd /home/ispq/qaboard_prod && source deployments/sirc/.envrc
dc() { docker compose --env-file deployments/sirc/production.env "$@"; }
psql_qa() { dc exec -T db psql -U qaboard -d qaboard "$@"; }
```

## 1. Before: sizes, clients, disk, memory (both hosts, a few days before)
```bash
# a. The database's size, and the newest dumps' (the dump in the new volume is about a dump's size)
psql_qa -c "SELECT pg_size_pretty(pg_database_size('qaboard'))"
ls -lht /home/ispq/qaboard_data_prod/database_backups/ | head -3
# b. Free disk where docker keeps its volumes: needs the database's size + a dump, with a margin
df -h "$(docker info -f '{{.DockerRootDir}}')"
# c. Free memory: both PostgreSQLs run during the upgrade (the old one with shared_buffers=512MB, the new one with qaboard.conf's)
free -g
# d. Who connects, from where: clients other than the backend, Metabase and pgAdmin are teams' scripts on port 5432
psql_qa -c "SELECT usename, client_addr, application_name, count(*) FROM pg_stat_activity WHERE datname = 'qaboard' GROUP BY 1, 2, 3 ORDER BY 4 DESC"
# e. Roles other than qaboard (copied, but their md5 passwords must be set again on 18)
psql_qa -c "\du"
```
- **b.** Available ≥ 2 × the database's size. Otherwise free space first (old images: `docker image prune`).
- **c.** `available` ≥ 6 GB.
- **d.** Each client from another host needs a driver that supports SCRAM passwords (libpq 10+, psycopg2 2.8+,
  JDBC 42.2+): tell their owners the date. Old drivers fail with `authentication method 10 not supported`.
- **e.** Write down the roles other than `qaboard` and who owns them: their passwords must be set again (step 4).

## 2. Rehearsal on staging (alginfra1), with production's data
Staging goes first anyway: once production runs 18, staging's `seed-db` and `restore-db` need 18 too.
1. A full copy of production: `deployments/deploy.py deployments/sirc/staging.env restore-db latest`.
2. Merge a merge request that adds `services/db/postgres-18.yml` to `COMPOSE_FILE` in `deployments/sirc/staging.env`,
   right after `production.yml`:
   ```bash
   COMPOSE_FILE=docker-compose.yml:production.yml:services/db/postgres-18.yml:${QABOARD_SITE_DIR:-deployments}/sirc/sirc.yml:${QABOARD_SITE_DIR:-deployments}/sirc/staging.yml
   ```
   Staging's automatic deploy then **fails on purpose**: `The db service's volume qaboard_staging_db18 doesn't exist`.
3. On alginfra1, at the merge commit: `deployments/deploy.py deployments/sirc/staging.env upgrade-postgres`, then
   `deployments/deploy.py deployments/sirc/staging.env up`.
4. Check like step 5 below. Write down: the time it took, the database's size before and after (`\l+`), the disk used
   (`docker system df -v | grep db18`).
5. Run a few batches on staging (LSF, and `qa batch --runner=celery` if a team uses it), open their results, and
   `seed-db` once (`deployments/deploy.py deployments/sirc/staging.env seed-db`): it reads production's 12 with 18's
   `pg_dump`.

If anything fails: fix it in a merge request and start again from 1 (`docker volume rm qaboard_staging_db18` first).

## 3. Production: plan the window
- A quiet time, the length of staging's rehearsal plus 30 minutes. Not around 02:00 (the daily backup).
- Announce it: the web app is down, and runs that end meanwhile can't send their results to it. Their files are
  on the storage: step 5f adds them back.
- Pause production deploys: GitLab CI/CD variable `QABOARD_PROD_DEPLOY=manual`
  (see [deployment.md](deployment.md#automatic-deploys-gitlab-ci-at-sirc)).
- Prepare the merge request for `deployments/sirc/production.env` (same `COMPOSE_FILE` change as staging, with `prod.yml`).

## 4. Production: the upgrade
```bash
# a. A fresh backup, on the shared storage (the upgrade's dump is in the new volume and deleted at the end)
dc run --rm cron-backup-db /etc/periodic/daily/backup before-pg18.dump
ls -lh /home/ispq/qaboard_data_prod/database_backups/before-pg18.dump
# b. Merge the merge request, then check out its merge commit
git fetch && git checkout --detach <merge commit>
grep postgres-18 deployments/sirc/production.env
# c. The upgrade (stops QA-Board)
deployments/deploy.py deployments/sirc/production.env upgrade-postgres --jobs 4
```
Expect `Old: PostgreSQL 12...`, then the dump and restore, `N tables, the same rows. New: PostgreSQL 18...`, maybe
the roles with md5 passwords, and `✅ The database runs postgres:18-alpine`.
```bash
# d. Roles other than qaboard listed with md5 passwords: set them again, and send them to their owners
psql_qa -c "ALTER ROLE <role> PASSWORD '<new password>'"
# e. Start everything
deployments/deploy.py deployments/sirc/production.env up --version <first 8 characters of the merge commit>
deployments/deploy.py deployments/sirc/production.env status
```

## 5. Checks
```bash
# a. 18, with qaboard.conf's settings and pg_stat_statements
psql_qa -c "SELECT version()"
psql_qa -c "SELECT name, setting FROM pg_settings WHERE sourcefile LIKE '%qaboard.conf' ORDER BY 1"
psql_qa -c "SELECT count(*) FROM pg_stat_statements"
# b. No errors in the database's and the backends' logs
dc logs --since 15m db | grep -E "FATAL|ERROR" | tail
dc logs --since 15m backend | grep -iE "error|operationalerror" | tail
```
- [ ] **a.** `PostgreSQL 18.x`; the 24 settings of [database.md](database.md#5-post-checks); a count (not an error).
- [ ] The web app opens; a recent commit shows its runs and images; **Admin → Settings → Security → Encryption of the
      stored secrets** decrypts every secret.
- [ ] Sign in with SSO and with LDAP.
- [ ] A batch on LSF ends and shows its results. A Celery batch, if teams use it.
- [ ] Metabase: **Admin → Databases → QA-Board → Save** (tests the connection). pgAdmin: https://qa/pgadmin4/ opens the server.
- [ ] **f.** **Admin → Storage → Scan the storage**: add the runs that ended during the window (**Add runs to QA-Board**).
- [ ] The next morning: the daily backup exists and has a size like the previous ones
      (`ls -lht /home/ispq/qaboard_data_prod/database_backups/ | head -3`), made with 18's `pg_dump`.
- [ ] Remove `QABOARD_PROD_DEPLOY=manual` in GitLab.

## Rollback
Decide during the window: once people use 18, going back loses what they did since.
1. Revert the merge request of `production.env` (or edit it on the host), check out the revert.
2. `deployments/deploy.py deployments/sirc/production.env up`: the db container is recreated with 12 on
   `qaboard_prod_db`, untouched since the upgrade started.
3. Later, to try again: `docker volume rm qaboard_prod_db18`, then from step 4.

**If `upgrade-postgres` failed**: read its last lines (`The restored database differs`, a `pg_restore` error...).
Production is stopped: roll back (above) to restart on 12 now, and fix it on staging.

## After 2 to 3 weeks
- [ ] `docker volume rm qaboard_prod_db` (and `qaboard_staging_db` on alginfra1): the old data. `before-pg18.dump`
      can go too, with the shared storage's daily dumps of 12 (`pg_restore` 18 reads them, if ever needed).
- [ ] [database.md](database.md#slow-queries): `pg_stat_statements` has `total_exec_time` and `mean_exec_time` since 13.
- [ ] `services/db/qaboard.conf`: only 18 to check from now on (its header).
