# Performance and host hygiene (SIRC)

The `qa` host (`qatools01`) has 8 cores and 31GB of RAM, **shared** with other stacks (`dvd`, `publish`, fluentd,
portainer). An audit in October 2026 found I/O pressure (PSI io avg300 ~11), recurring memory pressure (swap in use,
`kswapd0` busy), and that the postgres settings of the repository were never loaded. What the repository fixes is
applied by deploys, except the database settings: they need a restart of the `db` container, see below.

```bash
ssh qa
cd /home/ispq/qaboard_prod
source deployments/sirc/.envrc
```

## Applied by deploys
- nginx logs to docker (`docker compose --env-file deployments/sirc/production.env logs proxy`), rotated (5 x 100MB),
  instead of `/tmp/access.log` in the container, which grew forever (850MB). Recreating the proxy drops the old file.
- nginx only accepts TLS 1.2/1.3, compresses with `gzip_comp_level 4` instead of 6.
- uwsgi stops spawning workers past 6GB per replica (`QABOARD_UWSGI_CHEAPER_RSS_LIMIT_SOFT` in `production.env`),
  not 16GB: 2 replicas x 16GB was more than the host.
- `cron-backup-db` keeps the 7 latest daily backups (`BACKUP_KEEP` in `prod.yml`). Named backups stay: remove the old
  one-off dumps by hand.
- The backend replaces pooled connections that the database closed (`pool_pre_ping`).

Not done: `open_file_cache` in nginx. On NFS it serves stale sizes/contents for up to its validity (logs of
running jobs look frozen) and keeps deleted files open, which makes deleting output folders fail (`.nfsXXXX` files).

## Database settings (needs a restart)
`services/db/postgres.conf` is copied to `$PGDATA/postgres.conf` by `services/db/init.sh`, and included at the end
of `postgresql.conf`. Before October 2026 it was copied but never included: production ran with near-defaults
(and hand-edits in `postgresql.conf`). Deploys don't restart the database (its compose config didn't change), so:

```bash
# Before: save the current settings, to compare
docker compose --env-file deployments/sirc/production.env exec -T db psql -U qaboard -Atc \
  "SELECT name, setting, unit, source FROM pg_settings WHERE source NOT IN ('default', 'override') ORDER BY 1" > /tmp/pg-settings-before.txt
docker compose --env-file deployments/sirc/production.env exec -T db psql -U qaboard -Atc "SELECT count(*) FROM pg_stat_activity"

# Restart (~10s of errors for the backend)
docker compose --env-file deployments/sirc/production.env restart db
docker compose --env-file deployments/sirc/production.env logs --tail 50 db

# After: our settings come from postgres.conf
docker compose --env-file deployments/sirc/production.env exec -T db psql -U qaboard -Atc \
  "SELECT name, setting, unit, sourcefile FROM pg_settings WHERE source = 'configuration file' ORDER BY 1"
# once: what queries cost the most
docker compose --env-file deployments/sirc/production.env exec -T db psql -U qaboard -c "CREATE EXTENSION IF NOT EXISTS pg_stat_statements"
```

If postgres doesn't start (e.g. not enough memory for `shared_buffers`), comment out the settings at fault in
`services/db/postgres.conf` in the checkout (to go back to the previous settings: empty it) and restart again.
`init.sh` copies it at every start.

Watch afterwards:
- Slow queries (>1s), lock waits, checkpoints, temp files and long autovacuums are now logged (to ELK, `qaboard.db`).
- `idle_in_transaction_session_timeout = 10min` terminates sessions left idle in a transaction: if errors like
  `terminating connection due to idle-in-transaction timeout` show up in the backend, find the code that doesn't
  close its session.
- Top queries:
  ```sql
  SELECT round(total_time) AS ms, calls, round(mean_time) AS mean_ms, left(query, 120)
  FROM pg_stat_statements ORDER BY total_time DESC LIMIT 20;
  ```

Postgres 12 is end-of-life since November 2024. Upgrading (dump/restore to 16, or `pg_upgrade`) is its own
maintenance: it changes the `db` and `cron-backup-db` images, and `restore-db` must keep working with the old dumps.

## Host settings (not in the repository)
Need root on `qatools01`, ask IT if needed.

- **Docker's API on TCP 4243 without TLS**: anyone who reaches the port controls docker, i.e. is root on the host.
  Check `ss -ltnp | grep 4243` and `systemctl cat docker` / `/etc/docker/daemon.json` for `-H tcp://0.0.0.0:4243`.
  Find out who uses it first (portainer's agent doesn't need it), then remove it, or bind it to `127.0.0.1`, or
  set up TLS (`--tlsverify`). QA-Board's CI deploys over ssh, not over this port.
- **Swappiness**: `sysctl vm.swappiness` (60 by default). With 10, the kernel prefers dropping page cache to swapping
  out processes:
  ```bash
  echo 'vm.swappiness = 10' | sudo tee /etc/sysctl.d/90-qaboard.conf && sudo sysctl --system
  ```
- **Docker's log rotation** for the other stacks: `/etc/docker/daemon.json`
  `{"log-driver": "json-file", "log-opts": {"max-size": "100m", "max-file": "5"}}` (applies to new containers,
  restarts docker).

## Cleanup
Root disk was 83% full.
```bash
docker system df -v | less
# Volumes no container uses (~50, ~100GB). Check the list first: a stopped stack's volumes look unused too!
docker volume ls -qf dangling=true
docker volume ls -qf dangling=true | grep -v -e qaboard_prod -e qaboard_staging | xargs -r docker volume rm
# Old one-off dumps (BACKUP_KEEP only removes daily backups)
ls -lhS /home/ispq/qaboard_data_prod/database_backups/ | head -20
```
The NFS volumes with outputs were 93-99% full: see the storage cleanup dashboard.
