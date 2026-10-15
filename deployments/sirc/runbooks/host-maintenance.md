# Maintenance of the `qa` host (SIRC)

Things to fix *on the host*, outside the repository: they came up in a performance check of the production host
(8 cores, 31GB of RAM, shared with other docker stacks: `dvd-*`, `publish-*`, `fluentd`, `portainer_agent`).
It was short on memory (the kernel's `kswapd` busy swapping, 2GB of swap used), its disks were busy,
the root disk 83% full and the NFS volumes 93-99% full.

Every step has: a **check** (with what you should see), the **action**, and how to **roll back**.
Run them as yourself with `sudo` where needed. For the steps that restart docker or the stacks, use the
maintenance night (see [host-upgrades.md](host-upgrades.md): pause deploys, backup first). The other steps
can be done any day, **with every stack running** (see the volumes step).

```bash
ssh qa
cd /home/ispq/qaboard_prod
source deployments/sirc/.envrc
# shortcut used below
export COMPOSE="docker compose --env-file deployments/sirc/production.env"
```

| # | Step | When |
|---|------|------|
| 1 | [Docker's API open to the network](#1-dockers-api-open-to-the-network-port-4243) | **Now**: firewall it today, remove it during the maintenance night |
| 2 | [Swap less](#2-swap-less-vmswappiness) | Any day |
| 3 | [nginx's log files in the proxy container](#3-nginxs-log-files-in-the-proxy-container) | Any day, until the change that sends them to docker is deployed |
| 4 | [Old database dumps](#4-old-database-dumps) | Any day |
| 5 | [Unused docker volumes](#5-unused-docker-volumes) | Any day, **never while a stack is stopped** |
| 6 | [Full NFS volumes](#6-full-nfs-volumes) | Escalate now |
| 7 | [Root disk and docker logs](#7-root-disk-and-docker-logs) | Any day, daemon settings during the maintenance night |
| 8 | [Deploying the memory limits](#8-deploying-the-memory-limits-and-nginx-changes) | Maintenance night (restarts the database) |

## 1. Docker's API open to the network (port 4243)
**Why it's urgent**: dockerd listens on `0.0.0.0:4243` without TLS. Anyone who can reach the host can run
`docker -H tcp://qa:4243 run -v /:/host ...` and is root on it, with no password. Don't wait for the
maintenance night: firewall it today (1c), then remove it properly during the night (1d).

### 1a. Check
```bash
sudo ss -tlnp 'sport = :4243'
# where it's configured: daemon.json "hosts", or -H in the systemd unit or a drop-in
cat /etc/docker/daemon.json
systemctl cat docker | grep -nE 'ExecStart|-H|--host'
ps -o args= -C dockerd
docker info --format 'live-restore={{.LiveRestoreEnabled}} logging={{.LoggingDriver}} version={{.ServerVersion}}'
```
Expected (the problem): a `LISTEN ... 0.0.0.0:4243` (or `*:4243`) line for `dockerd`, and `tcp://0.0.0.0:4243`
in `daemon.json` or in `ExecStart`. Note where it is: you'll change it there, and only there.

### 1b. Find who uses it
Connections to the API are short: watch for new ones for a few hours (a working day is better):
```bash
# source IPs of new connections, counted. Ctrl-C to stop, or leave it with timeout (8 hours here)
sudo timeout 8h tcpdump -nn -i any 'tcp dst port 4243 and tcp[tcpflags] & tcp-syn != 0' 2>/dev/null \
  | awk '{for (i = 2; i <= NF; i++) if ($i == ">") print $(i-1)}' | cut -d. -f1-4 | sort | uniq -c | sort -rn
# a quick look at the current ones
sudo ss -tnp state established '( sport = :4243 )'
# who is that IP
getent hosts 10.x.x.x
```
Usual suspects: a Jenkins docker cloud (4243 is its plugin's default port), GitLab runners, a Portainer server
(`portainer_agent` uses the docker socket and its own port, not 4243), monitoring, people's `DOCKER_HOST`.
Tell their owners before step 1d, and agree on what they use instead:
- from the host itself: the socket (nothing to configure), or `127.0.0.1:4243` if a tool needs TCP;
- from other hosts: ssh, `DOCKER_HOST=ssh://<user>@qa` (needs an account in the `docker` group), or TLS with
  client certificates (port 2376, certificates from IT: https://docs.docker.com/engine/security/protect-access/).

### 1c. Today: firewall it
Only the hosts found in 1b (and the host itself) can connect. Takes effect at once, restarts nothing, and goes
away at the next reboot (that's the rollback if you lock out something important).
```bash
# one line per IP that needs it (from 1b), before the DROP
sudo iptables -I INPUT -p tcp --dport 4243 -s 10.x.x.x -j ACCEPT
sudo iptables -A INPUT -p tcp --dport 4243 -s 127.0.0.1 -j ACCEPT
sudo iptables -A INPUT -p tcp --dport 4243 -j DROP
sudo iptables -L INPUT -n --line-numbers | grep 4243
```
Check from another machine: `curl -m 5 http://qa:4243/version` must time out (and still answer from the allowed hosts).
If the host uses firewalld or ufw, use it instead (`firewall-cmd`, `ufw`), so that it doesn't override these rules.

Rollback: `sudo iptables -D INPUT <line number>` for each rule (numbers from the `-L` above).

### 1d. Maintenance night: remove the listener
Restarting dockerd restarts **every container on the host** (all the stacks), unless live-restore is on.

1. Edit the place found in 1a, only there (dockerd refuses to start when `hosts` is set both in `daemon.json`
   and with `-H`). Keep a copy first:
   ```bash
   sudo cp -a /etc/docker/daemon.json /etc/docker/daemon.json.bak-$(date -I)
   # systemd drop-in, if that's where -H is
   ls /etc/systemd/system/docker.service.d/
   sudo cp -a /etc/systemd/system/docker.service.d/<file>.conf /root/<file>.conf.bak-$(date -I)
   ```
   - in `daemon.json`: remove `"tcp://0.0.0.0:4243"` from `"hosts"` (keep `"unix:///var/run/docker.sock"`), or
     replace it by `"tcp://127.0.0.1:4243"` if a local tool needs TCP;
   - or in the drop-in: remove `-H tcp://0.0.0.0:4243` from `ExecStart=` (or change it to `-H tcp://127.0.0.1:4243`).

   While you're at it (same restart), in `daemon.json`, see step 7 for the log rotation of the other stacks:
   ```json
   "log-driver": "json-file",
   "log-opts": {"max-size": "50m", "max-file": "5"}
   ```
2. Validate before restarting (`--validate` needs docker 23+):
   ```bash
   sudo dockerd --validate --config-file /etc/docker/daemon.json && echo OK
   python3 -m json.tool /etc/docker/daemon.json > /dev/null && echo "valid JSON"
   ```
3. Restart (the stacks restart too, `restart: always` brings them back):
   ```bash
   sudo systemctl daemon-reload
   sudo systemctl restart docker
   ```
4. Check:
   ```bash
   sudo ss -tlnp 'sport = :4243'        # nothing, or 127.0.0.1:4243
   docker ps --format '{{.Names}}\t{{.Status}}' | sort   # every stack is back
   ./at-sirc-before-up.py && deployments/deploy.py deployments/sirc/production.env status
   ```
   And the checks of [host-upgrades.md](host-upgrades.md#restart). Then remove the iptables rules of 1c.

Rollback: put the backups back (`sudo cp -a /etc/docker/daemon.json.bak-<date> /etc/docker/daemon.json`, the
drop-in), `sudo systemctl daemon-reload && sudo systemctl restart docker`. Keep the firewall rules of 1c then.

> **live-restore** (`"live-restore": true` in `daemon.json`) keeps containers running while dockerd restarts.
> It can be turned on without a restart (`sudo systemctl reload docker`, then check `docker info`), which would
> make *this* restart painless. But turn it on deliberately, it has its own caveats (containers that log a lot
> block while dockerd is down: https://docs.docker.com/engine/daemon/live-restore/). Changing `hosts` still needs
> a restart.

## 2. Swap less (`vm.swappiness`)
With the default (60), the kernel swaps out the services' memory to keep more file cache. On this host, it's
mostly slow reads of swapped-out memory, on disks that are already busy.

Check:
```bash
cat /proc/sys/vm/swappiness     # expected now: 60
free -h                          # Swap: used
ls /etc/sysctl.d/                # 99-qaboard.conf must not exist yet
```
Action:
```bash
echo 'vm.swappiness = 10' | sudo tee /etc/sysctl.d/99-qaboard.conf
sudo sysctl --system | grep swappiness   # "vm.swappiness = 10"
cat /proc/sys/vm/swappiness             # 10
```
It applies at once and after reboots. What's already in swap comes back slowly as it's used: no need to
`swapoff`. Look again at `free -h` and `vmstat 5` (columns `si`/`so`) a few days later.

Rollback:
```bash
sudo rm /etc/sysctl.d/99-qaboard.conf
sudo sysctl -w vm.swappiness=60
```

## 3. nginx's log files in the proxy container
nginx wrote its logs to files in the container (`/tmp/access.log` reached 850MB), which docker never rotates.
Once the change sending them to docker's logs is deployed (step 8), the proxy is recreated and they disappear.
Until then:

Check:
```bash
$COMPOSE exec proxy sh -c 'ls -lh /tmp/*.log'
```
Action: empty them. nginx keeps writing at the new end (it opened them in append mode), nothing to restart:
```bash
$COMPOSE exec proxy sh -c 'for f in /tmp/*.log; do truncate -s0 "$f"; done; ls -lh /tmp/*.log'
```
Rollback: none needed (old access logs are lost: copy them out first with `docker cp` if anyone wants them).

After step 8, check they are gone and that docker rotates the new logs:
```bash
$COMPOSE exec proxy sh -c 'ls /tmp/*.log'   # No such file
docker inspect -f '{{.HostConfig.LogConfig}}' $($COMPOSE ps -q proxy)   # {json-file map[max-file:5 max-size:50m]}
$COMPOSE logs --tail 5 proxy                  # recent requests
```

## 4. Old database dumps
The backups folder takes 102GB, with two one-off dumps of ~13GB each from 2025. Delete those once you know the
daily backups work. (The daily `<date>.dump` files are handled separately.)

Check:
```bash
cd /home/ispq/qaboard_data_prod/database_backups
du -sh .
ls -lhS | head -20            # biggest first: find the 2025 one-off dumps
ls -lht *.dump | head -3      # the latest daily dump: today's or yesterday's date, a size close to the previous ones
```
Prove the latest daily dump is usable:
```bash
cd /home/ispq/qaboard_prod
latest=$(ls -t /home/ispq/qaboard_data_prod/database_backups/*.dump | head -1); echo $latest
# reads its table of contents: it's a pg_dump archive of the whole database (but it doesn't read the data)
$COMPOSE exec -T db pg_restore --list /backups/$(basename $latest) | grep -c 'TABLE DATA'
```
Expected: a number (the tables), no `pg_restore: error`. The real proof is a restore: on staging
(`alginfra1`, which mounts the same backups folder; check it has the disk space first), `deployments/deploy.py deployments/sirc/staging.env restore-db
<name>.dump` replaces *staging's* database with it (then `seed-db` brings staging back to normal, see
[deployment.md](deployment.md)). Do it at least once a quarter.

Action: delete the one-off dumps by name, after reading the list twice:
```bash
cd /home/ispq/qaboard_data_prod/database_backups
rm -i <the-2025-one-off-1>.dump <the-2025-one-off-2>.dump
du -sh .
```
Rollback: none, it's deleted. If unsure, ask whether anyone still needs them (e.g. before a migration).

## 5. Unused docker volumes
64 volumes, 13 used, ~100GB that nothing uses anymore.

> **DANGER**: a volume is "unused" (dangling) as soon as no container *exists* for it. During the maintenance
> night, after `docker compose down` (or when a stack's containers were removed), the database's volume
> (`qaboard_prod_db`) and every other stack's volumes are "unused": `docker volume prune` would delete them.
> **Never use `docker volume prune`.** Only remove volumes one by one, by name, while every stack is running.

Check that every stack is running (all of them: `dvd-*`, `publish-*`, `fluentd`, QA-Board...), and list the candidates:
```bash
docker ps -a --format '{{.Names}}\t{{.Status}}' | sort      # nothing "Exited" that should be running
docker compose ls -a                                        # every project "running"
docker volume ls -qf dangling=true | while read v; do
  docker volume inspect -f '{{.Name}}  created={{.CreatedAt}}  project={{index .Labels "com.docker.compose.project"}}  volume={{index .Labels "com.docker.compose.volume"}}' "$v"
  sudo du -sh "$(docker volume inspect -f '{{.Mountpoint}}' "$v")" 2>/dev/null
done
```
For each candidate, before removing it:
- its `project` is not a running project (`docker compose ls`). **Skip** anything from `qaboard_prod` or
  `qaboard_staging` (e.g. `qaboard_prod_db`, `qaboard_prod_data`, `qaboard_prod_redis_data`,
  `qaboard_prod_rabbitmq-data`), and ask the owners of the other stacks about theirs;
- no project label, a 64-hex-character name: an anonymous volume, left by a removed container. Look inside
  if it's big: `sudo ls $(docker volume inspect -f '{{.Mountpoint}}' <name>)`;
- old creation date.

Action, by explicit name:
```bash
docker volume rm <name1> <name2> ...
docker system df        # "Local Volumes": less space
```
Rollback: none. To keep a copy of a volume you're unsure about:
`docker run --rm -v <name>:/v:ro -v /some/big/disk:/backup alpine tar czf /backup/<name>.tgz -C /v .`

## 6. Full NFS volumes
The QA-Board outputs volumes are 93-99% full. Runs fail to write their outputs, and a full volume is a likely
cause of "mount problems" reports (tools report write errors on full volumes in confusing ways).

Check:
```bash
df -h /algo /stage /stage/algo_data /stage/qa_data /stage/algo-datasets /stage/algo_archive /sim /data /home /raid
```
Expected: below 90%. Action: ask IT for more space or a bigger quota, and ask the projects to clean up their
outputs (the storage cleanup dashboard linked from QA-Board, deleting old batches' outputs, `qaboard_clean`).
Rollback: n/a.

## 7. Root disk and docker logs
The root disk is 83% full. Docker's logs aren't rotated by default (`json-file` without `max-size`): QA-Board's
proxy now sets its own rotation (`production.yml`), other stacks don't.

Check:
```bash
df -h / /var/lib/docker
docker system df
sudo sh -c 'du -sh /var/lib/docker/containers/*/*-json.log' | sort -h | tail -5
docker info --format '{{.LoggingDriver}}'; cat /etc/docker/daemon.json
```
Actions:
- set `log-opts` in `daemon.json` during the maintenance night (step 1d): it only applies to containers created
  after the restart, so recreate the other stacks' containers with their owners (`docker compose up -d --force-recreate`);
- remove images nothing uses: `docker image prune` (only untagged images), then `docker system df`.
  `deploy.py` already removes QA-Board's old versions.

Rollback: `daemon.json` from its backup (step 1d); a removed image is pulled again when needed.

## 8. Deploying the memory limits and nginx changes
The change limiting the backend's memory and sending nginx's logs to docker is deployed like any other, but:
- the **database container is recreated** (`oom_score_adj`): it restarts, which fails requests for a few seconds;
- the **proxy is recreated** (log rotation): a few seconds without nginx.

So deploy it during the maintenance night (with the other database changes, if any), not with an automatic deploy.

Check first that the host accepts the database setting, else the database wouldn't start:
```bash
docker run --rm --oom-score-adj -500 postgres:12-alpine true && echo OK
```
If it fails: `QABOARD_DB_OOM_SCORE_ADJ=0` in `deployments/sirc/production.env`.

After the deploy:
```bash
deployments/deploy.py deployments/sirc/production.env status
docker inspect -f '{{.Name}} memory={{.HostConfig.Memory}} swap={{.HostConfig.MemorySwap}}' $($COMPOSE ps -q backend)   # 8589934592 twice
docker inspect -f '{{.HostConfig.OomScoreAdj}}' $($COMPOSE ps -q db)    # -500
docker stats --no-stream $($COMPOSE ps -q)
# memory seen by uwsgi: sum of the workers' RSS, compared with QABOARD_UWSGI_RSS_LIMIT_SOFT (6GB)
$COMPOSE exec backend python -c "import json,socket; s=socket.socket(socket.AF_UNIX); s.connect('/tmp/stats.sock'); d=json.loads(b''.join(iter(lambda: s.recv(65536), b''))); w=[x for x in d['workers'] if x['status']!='cheap']; print(len(w), 'workers', sum(x['rss'] for x in w)//2**20, 'MB')"
```
Rollback: `deployments/deploy.py deployments/sirc/production.env rollback`, or override the values in the env file
(`QABOARD_BACKEND_MEM_LIMIT`, `QABOARD_UWSGI_RSS_LIMIT_SOFT`, `QABOARD_UWSGI_RELOAD_ON_RSS`, see
[deployment.md](deployment.md#settings)) and deploy again.
