# Upgrading PostgreSQL from 12 to 18 (SIRC)

SIRC's runbook. The generic version is on the docs website:
[Upgrading PostgreSQL](https://samsung.github.io/qaboard/docs/backend-admin/deployment#upgrading-postgresql)
(source: `website/docs/backend-admin/deployment.mdx`). See also [database.md](database.md) (settings, backups).

## 0. What, why, how long

PostgreSQL 12 is out of support since November 2024 (no security or bug fixes). We upgrade the existing data **in
place** to PostgreSQL 18 with `pg_upgrade --link`: no dump and restore of the whole database, the data files are
reused (hard links). The tooling is in `services/db/upgrade/`:
- `Dockerfile`: the `db-upgrade` image, `postgres:18-alpine` plus the binaries of `postgres:12-alpine` (pg_upgrade
  needs both). Both are alpine/musl images: **never switch to a Debian (glibc) image**, text would sort differently
  and the indexes on text columns would be silently wrong.
- `upgrade.sh`: every step, with checks that stop at anything unexpected. `upgrade.sh help` lists the commands.
- `docker-compose.yml`: the `db-upgrade` service (profile `db-upgrade`, only runs on demand), and
  `QABOARD_POSTGRES_IMAGE`, the image of the db **and** of `cron-backup-db` (pg_dump 12 refuses to dump a server 18).
- `deployments/deploy.py` refuses to deploy a db image whose version doesn't match the data
  (`The db image is PostgreSQL 18 but the data in its volume is PostgreSQL 12`), so changing
  `QABOARD_POSTGRES_IMAGE` too early can't take the database down. Even without it, PostgreSQL 18 refuses to start on
  data of version 12 (`FATAL: database files are incompatible with server`): it doesn't touch the data.

During the upgrade, `$PGDATA` (the root of the `qaboard_prod_db` volume) holds both clusters:
`pg12-old/` (the old cluster, moved there), `pg18-new/` (the new one, moved to the root at the end), and
`pg-upgrade/` (logs, state). After it, the root is PostgreSQL 18, and `pg12-old/` + `pg-upgrade/` stay until `cleanup`.

**The point of no return**: in link mode the old and new clusters share the data files. Until PostgreSQL 18 has
started on the data, `upgrade.sh rollback` puts PostgreSQL 12 back in seconds. Once 18 has started, the way back is a
backup: the copy of the files taken during the window (`restore-files`, as long as copying takes), or the dump
(`restore-db`, hours). See [4. Rollback](#4-rollback-decision-points-and-time-budget).

**Downtime**: the web app's API and `qa` are down from step 3.2 to step 3.10. What takes time is the final backup
(the pg_dump, as long as the nightly backup, and the copy of the files if there is room), not the upgrade (seconds to a
minute in link mode: it depends on the number of tables and indexes, not on the size). Measure it all in the rehearsal
([2.](#2-rehearsal-at-sirc-on-a-copy-of-production)). Expect 1 to 3 hours; book a half day.

**What users see**: there is no maintenance page. The web app loads (nginx serves it) but every API call fails with
502 errors: empty pages and error messages. `qa run`/`qa batch` can't save results: runs started during the window
(LSF, CI pipelines) are not shown in QA-Board. Metabase's dashboards on QA-Board's data fail. Staging keeps working.

**Notify**, a few days before and when starting/finishing: QA-Board's users (mailing list / chat channel), the owners
of CI pipelines that call `qa`, Metabase users. Give the window and "don't start batches, results won't be saved".

**Go / no-go** (decide at the end of the rehearsal, and again on the night before step 3.5):
- [ ] the pre-checks of [1.](#1-days-before) are as expected, or explained
- [ ] staging runs PostgreSQL 18 for a few days already, without new errors
- [ ] the rehearsal on a copy of production passed: `check`, `run`, `post`, identical fingerprints, the web app works on it
- [ ] the window fits the measured times, with the rollback time (dump restore if no copy of the files)
- [ ] a fresh dump will be made in the window and there is room for it (the backups' NFS is 93-99% full)
- [ ] someone who knows the procedure is available the next morning

## Shortcuts
Every command runs on the host, from the checkout, in bash (like [database.md](database.md)):
```bash
ssh qa
bash  # the login shell (tcsh) can't source .envrc
cd /home/ispq/qaboard_prod && source deployments/sirc/.envrc
dc() { docker compose --env-file deployments/sirc/production.env "$@"; }
psql_qa() { dc exec -T db psql -U qaboard -d qaboard "$@"; }
upgrade() { dc --profile db-upgrade run --rm db-upgrade "$@"; }        # upgrade.sh in the db-upgrade container
in_db() { dc exec -T -u postgres db bash /opt/upgrade/upgrade.sh "$@"; }  # upgrade.sh in the running db container
mkdir -p ~/pg-upgrade && D=$(date +%F)
```
For staging: `ssh alginfra1`, `cd /home/ispq/qaboard_staging`, and `deployments/sirc/staging.env` in `dc`.

## 1. Days before

### 1.1 Pre-checks on production (read-only, no downtime)
```bash
psql_qa -c "SELECT version()" -c "SHOW data_directory" -c "SHOW data_checksums" -c "SHOW server_encoding" \
  -c "SHOW lc_collate" -c "SHOW lc_ctype" -c "SHOW password_encryption"
psql_qa -c "SELECT datname, pg_size_pretty(pg_database_size(oid)) AS size, pg_encoding_to_char(encoding) AS enc,
  datcollate, datctype, datallowconn FROM pg_database ORDER BY pg_database_size(oid) DESC"
for db in $(psql_qa -Atc "SELECT datname FROM pg_database WHERE datallowconn"); do
  echo "== $db: $(dc exec -T db psql -U qaboard -d "$db" -Atc "SELECT string_agg(extname || ' ' || extversion, ', ') FROM pg_extension")"
done
psql_qa -c "SELECT rolname, rolsuper, left(rolpassword, 3) AS hash FROM pg_authid WHERE rolcanlogin"
dc exec -T db sh -c 'cat $PGDATA/postgresql.auto.conf; echo ---; grep -vE "^[[:space:]]*(#|$)" $PGDATA/pg_hba.conf'
psql_qa -c "SELECT * FROM pg_replication_slots" -c "SELECT * FROM pg_prepared_xacts"
psql_qa -c "SELECT c.oid::regclass, a.attname, a.atttypid::regtype FROM pg_attribute a JOIN pg_class c ON c.oid = a.attrelid
  JOIN pg_namespace n ON n.oid = c.relnamespace WHERE a.atttypid::regtype::text LIKE 'reg%' AND a.attnum > 0
  AND n.nspname NOT IN ('pg_catalog', 'information_schema')"
psql_qa -c "SELECT client_addr, usename, application_name, state, count(*) FROM pg_stat_activity
  WHERE backend_type = 'client backend' GROUP BY 1, 2, 3, 4 ORDER BY 5 DESC"
psql_qa -c "SELECT pid, client_addr, state, now() - xact_start AS xact_age, left(query, 80) FROM pg_stat_activity
  WHERE xact_start < now() - interval '5 minutes'"
# The image, the volume, its filesystem and free space
docker inspect qaboard_prod-db-1 --format '{{.Config.Image}} {{range .Mounts}}{{.Name}}:{{.Destination}} {{end}}'
dc exec -T db sh -c 'cat /etc/alpine-release; postgres -V; stat -f -c "filesystem: %T" $PGDATA; df -h $PGDATA; du -sh $PGDATA'
docker volume inspect qaboard_prod_db --format '{{.Driver}} {{.Mountpoint}} {{.Options}}'
# The nightly dump: its size, and how long it takes (it starts at 02:00, its time is when it ended)
ls -lh --time-style=+%F_%H:%M /home/ispq/qaboard_data_prod/database_backups/ | tail -5
df -h /home/ispq/qaboard_data_prod/database_backups/
```
Write the answers down in `~/pg-upgrade/prechecks-$D.txt`. What to expect, and what to do if it differs:
- `PostgreSQL 12.x ... musl`, `data_directory` = `/var/lib/postgresql/data`, `data_checksums` = `off`, `UTF8`,
  `en_US.utf8` twice, `password_encryption` = `md5`. Any of them can differ: `upgrade.sh` reads them from the cluster
  and creates the new one with the same. Only a non-musl `version()` (glibc) is a no-go: ask for help.
- Databases: `qaboard` (the big one), `postgres`, `template0` (`datallowconn` false: normal), `template1`. Another
  database with `datallowconn` false: pg_upgrade needs to connect to it, `ALTER DATABASE x ALLOW_CONNECTIONS true`.
- Extensions: `plpgsql` everywhere, `pg_stat_statements` in `qaboard` (from the PostgreSQL config change). Others: all
  of contrib exists in the 18 image, `upgrade.sh post` updates them; anything not from contrib is a no-go until we
  know its 18 version is installed.
- Roles: `qaboard`, superuser, `md5`. Other login roles keep working (md5 still works in 18), list them for 3.9.
- `postgresql.auto.conf`: only two comment lines. Lines like `shared_buffers = '2GB'` (`ALTER SYSTEM`): do
  [database.md's step 3](database.md#3-remove-the-alter-system-values-only-if-step-2-found-some) first, else `upgrade.sh`
  stops. `pg_hba.conf`: `local all all trust` (needed) ... `host all all all md5`. It is carried over as is.
- No replication slots, no prepared transactions, no `reg*` columns (`(0 rows)` three times). Otherwise a no-go
  until removed (`pg_upgrade --check` would stop on them anyway).
- Connections: the backends (docker IPs), maybe Metabase's host, maybe people (psql, pgadmin, DBeaver...). Note who,
  to tell them. Long transactions: none older than a few minutes.
- `postgres:12-alpine qaboard_prod_db:/var/lib/postgresql/data`. Filesystem: `ext2/ext3` (ext4) or `xfs` (local
  disk), or `nfs`. Hard links work inside one filesystem, NFS too (the volume is in one export): link mode works on
  all of them. The volume's free space must be at least 2 GB (new catalogs and WAL).
- The nightly dump: its size (~20 GB) and duration (its time minus 02:00) are what the final backup of 3.4 takes.
  The backups' filesystem needs room for one more dump; if not, free some first.

### 1.2 Build the upgrade image, on `qa` and on `alginfra1`
After the merge of the upgrade's code (it changes nothing on its own: the db stays on 12):
```bash
docker pull postgres:18-alpine
dc --profile db-upgrade build db-upgrade
docker run --rm --entrypoint sh qaboard-db-upgrade:12-to-18 -c '$OLD_BINDIR/postgres -V; postgres -V; pg_upgrade -V'
```
Expect `postgres (PostgreSQL) 12.22`, `postgres (PostgreSQL) 18.x`, `pg_upgrade (PostgreSQL) 18.x`. The build prints
`compat: libicu...` (old libraries the new image doesn't have) and `removed ...plperl.so` etc. (unused, fine). It
fails if the old binaries can't run. Docker Hub rate limits: add
`--build-arg NEW_IMAGE=mirror.gcr.io/library/postgres:18-alpine --build-arg OLD_IMAGE=mirror.gcr.io/library/postgres:12-alpine`.

### 1.3 Upgrade staging first
Staging's `seed-db` runs pg_dump in staging's db container against production: it must not be older than production's
server. So staging goes to 18 first (and it's a full run of the procedure on a small database). On `alginfra1`, with
the staging `dc`: do the steps of [3. The night](#3-the-night) with `staging.env`, except: staging has no
`cron-backup-db` (leave it out of the `dc stop`/`dc up` commands), skip the dump of 3.4 and the copy of 3.4b (staging's
way back is `seed-db`), and use port 7432 in 3.8. Then commit `QABOARD_POSTGRES_IMAGE=postgres:18-alpine` in
`deployments/sirc/staging.env` (uncomment the line). Production deploys are not affected: `production.env` doesn't
change. Check `seed-db` still works: `deployments/deploy.py deployments/sirc/staging.env seed-db`.

## 2. Rehearsal at SIRC on a copy of production
On a full-size copy: the 14-day seed of staging is too small to time anything. Two ways to get the copy, pick by space.

**A. A physical copy (best: the same files, settings and size), on `qa` if a local disk has room for the whole
cluster** (`du -sh` of 1.1, plus 10%). `pg_basebackup` copies it while production runs (it reads at full speed:
do it in the evening). It connects from inside the db container's network namespace, so `pg_hba.conf`'s
`host replication all 127.0.0.1/32 trust` lets it in without changing anything on production:
```bash
dcr() { COMPOSE_PROJECT_NAME=qaboard_rehearsal QABOARD_EXPOSED_DB_PORT=7433 docker compose --env-file deployments/sirc/production.env "$@"; }
dcr up --no-start db                               # creates the volume qaboard_rehearsal_db (and a stopped container)
docker run --rm -v qaboard_rehearsal_db:/copy --entrypoint chown postgres:12-alpine postgres:postgres /copy
time docker run --rm --network container:qaboard_prod-db-1 -v qaboard_rehearsal_db:/copy -u postgres postgres:12-alpine \
  pg_basebackup -h 127.0.0.1 -U qaboard -D /copy -X stream -c fast -P
dcr up -d db && dcr logs --tail 5 db                # replays the WAL of the copy: "ready to accept connections"
```
Expect the progress line to reach `(100%), 1/1 tablespace`, then the copy to start (`consistent recovery state
reached`, `ready to accept connections`). `dcr` is a compose project of its own (`qaboard_rehearsal`, its own
volume, the db on port 7433): never use `dc` for it. If the docker volumes are not on a disk with room, use B.

**B. A restore of the latest nightly dump**, on `alginfra1` (or any host with room). Same size and schema, but freshly
written (no bloat), and it can't show surprises of production's files (1.1's checks cover those). It also measures
the dump's restore time, the slow way back of [4.](#4-rollback-decision-points-and-time-budget):
```bash
dcr() { COMPOSE_PROJECT_NAME=qaboard_rehearsal QABOARD_EXPOSED_DB_PORT=7433 docker compose --env-file deployments/sirc/staging.env "$@"; }
dcr up -d db
time dcr exec -T db /opt/restore /backups/$(date +%F).dump      # staging mounts production's backups read-only
```

Then, on the copy, **the same commands as the night**, with `dcr` instead of `dc` (`upgrade() { dcr --profile db-upgrade
run --rm db-upgrade "$@"; }`, same for `in_db`), timing each one: 3.4 (`in_db fingerprint` before; the dump and the
files copy, to time them), 3.5 to 3.8. Then point staging's web app at it:
```bash
# on alginfra1: staging's backend on the rehearsal database (it runs its migrations on it: fine, it's a copy)
QABOARD_DB_HOST=qa QABOARD_DB_PORT=7433 deployments/deploy.py deployments/sirc/staging.env compose up -d --no-deps backend
```
and do the smoke tests of 3.9 on http://alginfra1:7159. Also time `in_db amcheck` (optional step 5) and a rollback:
`dcr stop db; upgrade restore-files ...` or the dump restore. Record every time in `~/pg-upgrade/rehearsal-$D.txt`.
Clean up: put staging's backend back (`deployments/deploy.py deployments/sirc/staging.env up`), then
`dcr down -v` (deletes the copy: check `dcr` is the rehearsal project first, `dcr ls`).

Reference times, from a rehearsal in docker on a 2.2 GB copy with 2.5 M outputs (fast local disk, data in cache):
`check` 4 s, `run` 5 s (pg_upgrade 3 s), start of 18 under 1 s, `post` 2 s, `rollback` 1 s, `backup-files` of 5.5 GB 10 s,
`restore-files` 21 s, pg_dump 28 s, its restore 80 s, `fingerprint` 4 s, `amcheck` 9 s for 1 GB of indexes (21 s with
`--heapallindexed`). On `qa`'s IO-bound disks with ~10x the data, expect the copies, dumps and amcheck to take 10-50x longer;
`run` stays short.

## 3. The night
T0 is step 3.2. Write the time of each step in `~/pg-upgrade/night-$D.txt`.

**3.1 Before T0**: GitLab CI/CD variable `QABOARD_PROD_DEPLOY=manual` (nothing deploys production meanwhile). Don't run
`staging:refresh-db`. Announce the start. Check the last nightly backup worked (1.1's `ls`).

**3.2 Stop what writes** (T0):
```bash
dc stop backend flower cron-backup-db pgadmin
dc ps
```
Expect them `exited`; `db`, `proxy`, `redis`, `rabbitmq` still `running`. The web app now shows errors (502).
Don't restart the proxy until 3.10: nginx doesn't start while no backend runs.

**3.3 No one connected**:
```bash
psql_qa -c "SELECT pid, usename, client_addr, application_name, state, backend_start FROM pg_stat_activity
  WHERE backend_type = 'client backend' AND pid <> pg_backend_pid()"
```
Expect `(0 rows)`. Otherwise: a `client_addr` from the docker network (172.x) is a container we didn't stop
(`docker ps`); another IP is Metabase or a person: ask them, or end the sessions with
`psql_qa -c "SELECT pg_terminate_backend(pid) FROM pg_stat_activity WHERE backend_type = 'client backend' AND pid <> pg_backend_pid()"`.
(We don't use `ALTER DATABASE ... ALLOW_CONNECTIONS false`: pg_upgrade needs to connect, and it would be carried over.
Stopping the db in 3.4 keeps everyone out.)

**3.4 Fresh backups, the fingerprint, stop the db**:
```bash
in_db fingerprint > ~/pg-upgrade/fingerprint-12-$D.txt   # row counts and samples, compared in 3.8
wc -l ~/pg-upgrade/fingerprint-12-$D.txt
time dc run --rm cron-backup-db /etc/periodic/daily/backup before-pg18.dump
ls -lh /home/ispq/qaboard_data_prod/database_backups/before-pg18.dump
psql_qa -c "CHECKPOINT"
dc stop db
docker ps -a --filter volume=qaboard_prod_db --format '{{.Names}} {{.Status}}'
```
Expect: a few dozen lines (`outputs|rows|...`, `...|first rows by id|<md5>`); the dump's log ending with
`mv .../.before-pg18.dump.tmp .../before-pg18.dump`, a size close to the nightly one; `CHECKPOINT`; only
`qaboard_prod-db-1 Exited (0)` using the volume. A dump that failed (no file, much smaller): fix it before going on
(space?), or stop here: start everything again (3.10 with the 12 image, i.e. without `QABOARD_POSTGRES_IMAGE`).

**3.4b The copy of the files** (if 1.1/2. found a disk with room for `du -sh` + 5%; it is the fast way back after 18 started):
```bash
COPY=/path/with/room/qaboard-pg12-$D && mkdir -p $COPY && chmod 777 $COPY   # not the backups' NFS if it's full
time dc --profile db-upgrade run --rm -v $COPY:/backup db-upgrade backup-files /backup
```
Expect `copy: done in ...s` and `backup-files: done`. `not enough space`: skip it, the dump is the way back.

**3.5 Check** (read-only):
```bash
upgrade check 2>&1 | tee ~/pg-upgrade/check-$D.log
```
Expect `OK:` lines like 1.1's answers, `*Clusters are compatible*`, `clone: not supported` (or `SUPPORTED`, see below), and
`check: everything is ready`. Lines `sh: locale: not found` and `no usable system locales were found` are normal on
alpine. Any `ERROR:` says what to do; nothing was changed: fix it, or stop (start the db again: 3.10 with the 12 image).
If `clone: SUPPORTED` (XFS with reflinks): use `export UPGRADE_MODE=clone` for 3.6, then PostgreSQL 12's files stay
intact and `rollback --discard-new` works even after 18 started (the copy of 3.4b is then not needed).

**3.6 The upgrade**:
```bash
time upgrade run 2>&1 | tee ~/pg-upgrade/run-$D.log
```
Expect the steps 1 to 6, `Upgrade Complete`, `Checking for extension updates ... notice` (pg_stat_statements: done in
3.8), and `Done in ...s: /var/lib/postgresql/data is PostgreSQL 18`. If it stops with `ERROR`: nothing ran on the data
yet. Run `upgrade status`, then either fix and `upgrade run` again (it resumes or starts over), or go back:
`upgrade rollback` (seconds), then 3.10 with the 12 image.

**3.7 Start PostgreSQL 18** (from here, going back needs a backup: see [4.](#4-rollback-decision-points-and-time-budget)):
```bash
export QABOARD_POSTGRES_IMAGE=postgres:18-alpine   # in this shell only, until production.env is committed (3.11)
dc up -d --no-deps db
dc logs --tail 20 db
```
Expect `[init.sh] ... includes qaboard.conf`, `starting PostgreSQL 18.x`, `database system is ready to accept
connections`. `FATAL: database files are incompatible`: the variable is not set (the 12 image started), check
`docker inspect qaboard_prod-db-1 --format '{{.Config.Image}}'`. Any other `FATAL` before `ready`: read it; if it's a
setting, start once without our settings (`QABOARD_DB_CONFIG=none dc up -d --no-deps db`) and fix qaboard.conf later.

**3.8 Post steps, and the checks**:
```bash
in_db post 2>&1 | tee ~/pg-upgrade/post-$D.log
in_db fingerprint > ~/pg-upgrade/fingerprint-18-$D.txt
diff ~/pg-upgrade/fingerprint-12-$D.txt ~/pg-upgrade/fingerprint-18-$D.txt && echo IDENTICAL
psql_qa -c "SELECT version()" -c "SELECT extname, extversion FROM pg_extension"
PGPASSWORD=password psql -h 127.0.0.1 -p 5434 -U qaboard -d qaboard -c "SELECT current_user"   # as the backend logs in, over TCP
```
Expect: `pg_stat_statements updated to 1.12`, `qaboard's password is now stored as SCRAM-SHA-256 (same password)`, the
`vacuumdb` stages, `post: done`; `IDENTICAL`; `PostgreSQL 18.x`; `qaboard`. (No `psql` on the host: run the last line
in a container: `docker run --rm --network host -e PGPASSWORD=password postgres:18-alpine psql -h 127.0.0.1 -p 5434 -U qaboard -d qaboard -c "SELECT current_user"`;
use the port of `QABOARD_EXPOSED_DB_PORT` in `.env`, and the real password if it was changed.)
- `POSTGRES_PASSWORD is not qaboard's current password`: the password was changed by hand. Logins still work (the md5
  hash and `pg_hba.conf` were kept); re-set it later with `\password qaboard` in `psql_qa`.
- The fingerprints differ: stop, don't start the backends. Look at which line; if it's real, [roll back](#4-rollback-decision-points-and-time-budget).
Then the settings of [database.md's post-checks](database.md#5-post-checks) (a, b, d): same 24 values, from `qaboard.conf`.

**3.9 Start the backends, smoke tests**:
```bash
dc up -d --no-deps cron-backup-db flower pgadmin
deployments/deploy.py deployments/sirc/production.env up     # the same version: recreates nothing but the backends' check
deployments/deploy.py deployments/sirc/production.env status
dc logs --since 10m db | grep -E "FATAL|ERROR|PANIC"
```
Expect `PostgreSQL 18, data 18`, the deploy to end with `Deployed`, all containers running, backends healthy, no
`FATAL`/`ERROR` but the `terminating connection` of 3.3. Then in a browser: https://qa (projects list), a big project's
commits list, a commit page with images, a batch's logs, search, the "History" page. With `qa` (in any project
checkout): `qa batch` of a small batch, check its results appear. Check
`dc exec -T cron-backup-db pg_dump --version` says 18.

**3.10 If you stop before 3.7** (any `ERROR` you can't fix, or no-go): `upgrade status` must say `old` (else
`upgrade rollback`), then `unset QABOARD_POSTGRES_IMAGE; dc up -d --no-deps db; deployments/deploy.py deployments/sirc/production.env up`.

**3.11 Done**: commit `QABOARD_POSTGRES_IMAGE=postgres:18-alpine` in `deployments/sirc/production.env` (uncomment the
line) and merge it. Deploy it by hand (`deploy:production` in GitLab): it recreates nothing. Then remove
`QABOARD_PROD_DEPLOY=manual`. Until this is merged, a deploy from a shell without the variable stops with
`The db image is PostgreSQL 12 but the data in its volume is PostgreSQL 18` (nothing changed). Announce the end.

## 4. Rollback: decision points and time budget
| When | Symptom | Do | Takes |
|------|---------|----|-------|
| 3.5, 3.6 fail | `ERROR` | `upgrade rollback` (if not `old`), 3.10 with the 12 image | seconds |
| 3.6 done, before 3.7 | you change your mind | `upgrade rollback`, 3.10 | seconds |
| 3.7 to 3.9, **before users write** | 18 doesn't start, fingerprints differ, the app fails and it's not a quick fix | below: back to the copy or the dump | copy: as 3.4b; dump: hours (measured in 2.) |
| after 3.9 (users wrote) | problems found later | fix forward on 18 if at all possible; going back loses what was written since 3.9 | |

**Budget**: if 3.8 is not `IDENTICAL` with a working login **by T0 + (3.4's duration) + 45 min**, roll back. If the
app's smoke tests (3.9) fail and the cause isn't clear in 30 minutes, roll back while nothing was written.

Back to PostgreSQL 12 after 18 started:
```bash
dc stop backend flower cron-backup-db db
unset QABOARD_POSTGRES_IMAGE
upgrade status        # "PostgreSQL 18 has run on the data"; `upgrade rollback` refuses in link mode, that's expected
# a) with the copy of 3.4b (UPGRADE_MODE=clone: `upgrade rollback --discard-new` instead)
dc --profile db-upgrade run --rm -v $COPY:/backup db-upgrade restore-files /backup --yes
dc up -d --no-deps db && dc logs --tail 5 db          # PostgreSQL 12, "ready to accept connections"
# b) without it, from the dump of 3.4: an empty volume, a new PostgreSQL 12, the dump (hours)
upgrade empty-volume --yes
dc up -d --no-deps db && dc logs --tail 5 db          # "PostgreSQL init process complete", then "ready"
deployments/deploy.py deployments/sirc/production.env restore-db before-pg18.dump
# both
in_db fingerprint | diff ~/pg-upgrade/fingerprint-12-$D.txt - && echo IDENTICAL
deployments/deploy.py deployments/sirc/production.env up
```
Expect `IDENTICAL` (after b, the `rows` lines must match; `first rows` too). Keep production deploys manual, and find out
what went wrong before trying again.

## 5. After
- **The first day**: watch Sentry (new errors?), `dc logs --since 1h db | grep -E "ERROR|FATAL|duration:"` (slow
  queries: compare with before), the web app's speed. Slow queries since the upgrade: in PostgreSQL 18,
  pg_stat_statements has `total_exec_time`/`mean_exec_time` (see [database.md](database.md#slow-queries)). Planner
  statistics were carried over; for a full refresh at a quiet time: `dc exec -T db vacuumdb -U qaboard --all --analyze-only`.
- **Optional, at a quiet time**: verify every btree index (read-only, reads all the indexes: IO load). Its speed was
  measured in 2.:
  ```bash
  time in_db amcheck       # add --heapallindexed to also check every row is indexed (~2x longer, reads the tables)
  ```
  Expect `amcheck: 0 errors, N GB of indexes in Ns`. An error names the index: `REINDEX INDEX CONCURRENTLY <name>`.
- Until then, a `pg_basebackup` of production also copies `pg12-old/` (twice the size): do the cleanup first.
- **After 7 days** without problems, delete the old cluster (link mode: it frees its old catalogs and WAL, a few GB) and
  the copy of 3.4b:
  ```bash
  in_db cleanup --yes
  rm -rf $COPY
  ```
  and the `before-pg18.dump` when you no longer want it (`cron-backup-db` never deletes it).
- `docs/known-issues.md`: remove the "PostgreSQL 12 is EOL" entry. The Helm chart (`charts/qaboard`) uses PostgreSQL 16
  and is not concerned by this; when moving to Kubernetes, the dump of production restores into it.
