#!/bin/bash
# Upgrades the PostgreSQL cluster of the db volume from 12 to 18, in place, with pg_upgrade.
#
# In the db-upgrade container (docker-compose.yml, profile db-upgrade: PostgreSQL 18 + the binaries of 12), the db
# being stopped:
#   docker compose --profile db-upgrade run --rm db-upgrade check      # read-only: preflight + pg_upgrade --check
#   docker compose --profile db-upgrade run --rm db-upgrade run        # the upgrade (seconds to minutes with --link)
#   docker compose --profile db-upgrade run --rm db-upgrade status
#   docker compose --profile db-upgrade run --rm db-upgrade rollback   # back to 12, while 18 never ran on the data
#   # A copy of the stopped old cluster's files, to go back to 12 after 18 ran (needs as much space as the data):
#   docker compose --profile db-upgrade run --rm -v /host/dir:/backup db-upgrade backup-files /backup
#   docker compose --profile db-upgrade run --rm -v /host/dir:/backup db-upgrade restore-files /backup --yes
#   # To restore a dump instead: empty the volume, the db then starts with a new, empty PostgreSQL 12
#   docker compose --profile db-upgrade run --rm db-upgrade empty-volume --yes
# In the db container, PostgreSQL 18 running (services/db is mounted at /opt):
#   docker compose exec -u postgres db bash /opt/upgrade/upgrade.sh post          # extensions, password, statistics
#   docker compose exec -u postgres db bash /opt/upgrade/upgrade.sh fingerprint   # row counts + samples (12 or 18)
#   docker compose exec -u postgres db bash /opt/upgrade/upgrade.sh amcheck       # optional: verify the btree indexes
#   docker compose exec -u postgres db bash /opt/upgrade/upgrade.sh cleanup --yes # days later: delete the old cluster
#
# Layout. The data is at the root of the db volume, $PGDATA (/var/lib/postgresql/data), before and after. For
# pg_upgrade --link, the old and new clusters must be on the same filesystem, so both live in the volume during the
# upgrade (moving a directory inside a filesystem is a rename: instant, whatever its size):
#   1. the old cluster moves to $PGDATA/pg12-old/             (renames)
#   2. initdb $PGDATA/pg18-new/, with the old cluster's encoding, locale, checksums and WAL segment size
#   3. pg_upgrade --check, then pg_upgrade --link from pg12-old/ to pg18-new/
#   4. pg18-new/ gets the old pg_hba.conf and pg_ident.conf (same logins), then moves to the root of $PGDATA (renames)
# After: $PGDATA is PostgreSQL 18, with pg12-old/ (the old cluster: its files are hard links to the same data) and
# pg-upgrade/ (logs, state) in it, until `cleanup`. PostgreSQL ignores them.
#
# Modes (UPGRADE_MODE or --mode): link (default), clone, copy.
# - link: hard links, seconds. The old cluster stays usable until PostgreSQL 18 starts on the data, then it isn't
#   (they share the files): `rollback` works until then, after that the way back is a backup.
# - clone: copy-on-write copies (reflinks: XFS with reflink, btrfs...), seconds too, and the old cluster stays intact:
#   `rollback --discard-new` works even after 18 ran. `check` says if the filesystem supports it.
# - copy: copies the data (needs as much free space, and time, as the data).
# (PostgreSQL 18's --swap is not offered: it is only faster than --link with very many files, and gives up the old
# cluster during pg_upgrade already.)
#
# Every step refuses to run on anything unexpected, and `run` can be re-run after a failure: it resumes or starts over.
set -euo pipefail

OLD_MAJOR=12
NEW_MAJOR=18
OLD_BINDIR=${OLD_BINDIR:-/usr/local/pg$OLD_MAJOR/bin}
NEW_BINDIR=${NEW_BINDIR:-/usr/local/bin}
VOL=${PGDATA:?PGDATA must be set: the root of the db volume}
VOL=${VOL%/}
OLD_DIR=$VOL/pg$OLD_MAJOR-old
NEW_DIR=$VOL/pg$NEW_MAJOR-new
STATE_DIR=$VOL/pg-upgrade
CHECK_DIR=$STATE_DIR/check-cluster
SOCKET_DIR=/tmp
OLD_PORT=50432
NEW_PORT=50433
MODE=${UPGRADE_MODE:-link}
MODE_GIVEN=${UPGRADE_MODE:+1}
MIN_FREE_KB=$((2 * 1024 * 1024))
JOBS=$(nproc 2> /dev/null || echo 2)
[ "$JOBS" -gt 4 ] && JOBS=4
DB_USER=${POSTGRES_USER:-postgres}
DB_NAME=${POSTGRES_DB:-$DB_USER}
START=$SECONDS

# --- logs ---
ts() { date '+%H:%M:%S'; }
log() { echo "[upgrade $(ts) +$((SECONDS - START))s] $*"; }
step() { echo; echo "[upgrade $(ts) +$((SECONDS - START))s] ===== $* ====="; }
ok() { echo "[upgrade $(ts)]   OK: $*"; }
warn() { echo "[upgrade $(ts)]   WARNING: $*" >&2; }
die() { echo; echo "[upgrade $(ts)] ERROR: $*" >&2; echo "[upgrade] Nothing more was done. Docs: https://samsung.github.io/qaboard/docs/backend-admin/deployment#upgrading-postgresql" >&2; exit 1; }
timed() {  # timed LABEL CMD...: runs CMD, logs how long it took, returns its exit status
  local label=$1 t0=$SECONDS rc=0; shift
  "$@" || rc=$?
  if [ "$rc" = 0 ]; then log "$label: done in $((SECONDS - t0))s"; else log "$label: FAILED (exit status $rc) after $((SECONDS - t0))s"; fi
  return "$rc"
}

usage() { sed -n '2,/^set -euo/p' "$0" | sed '$d' | sed 's/^# \{0,1\}//'; }

major_of() { local v; v=$("$1" -V); v=${v##* }; echo "${v%%.*}"; }  # major_of /path/to/postgres
pg_version_at() { cat "$1/PG_VERSION" 2> /dev/null || true; }
controldata() { "$1/pg_controldata" -D "$2"; }
controldata_field() { controldata "$1" "$2" | sed -n "s/^$3: *//p"; }

# --- the old server, started on a socket only, for the preflight queries ---
OLD_STARTED=
start_old() {
  log "Starting PostgreSQL $OLD_MAJOR on $1, socket only (port $OLD_PORT), for the checks"
  "$OLD_BINDIR/pg_ctl" -D "$1" -w -t 300 -l "$STATE_DIR/preflight-old-server.log" \
    -o "-c listen_addresses='' -p $OLD_PORT -k $SOCKET_DIR" start > /dev/null \
    || { cat "$STATE_DIR/preflight-old-server.log" >&2; die "could not start PostgreSQL $OLD_MAJOR on the old cluster (log above)"; }
  OLD_STARTED=$1
}
stop_old() {
  if [ -n "$OLD_STARTED" ]; then
    "$OLD_BINDIR/pg_ctl" -D "$OLD_STARTED" -w -t 300 -m fast stop > /dev/null
    OLD_STARTED=
    log "Stopped PostgreSQL $OLD_MAJOR"
  fi
}
trap 'stop_old' EXIT
old_sql() {  # old_sql DB SQL: unaligned, tab-separated output
  "$OLD_BINDIR/psql" -X -q -v ON_ERROR_STOP=1 -h "$SOCKET_DIR" -p "$OLD_PORT" -U "$INSTALL_USER" -d "$1" -AtF $'\t' -c "$2"
}

# --- state of the volume ---
# old: PostgreSQL 12 at the root.  moved: old cluster in pg12-old/, upgrade not finished.  done: PostgreSQL 18 at the
# root, old cluster in pg12-old/.  new: PostgreSQL 18 at the root, no old cluster.
detect_state() {
  local root old
  root=$(pg_version_at "$VOL")
  old=$(pg_version_at "$OLD_DIR")
  if [ "$root" = "$OLD_MAJOR" ] && [ ! -e "$OLD_DIR" ] && [ ! -e "$NEW_DIR" ]; then echo old
  elif [ -z "$root" ] && [ "$old" = "$OLD_MAJOR" ]; then echo moved
  elif [ "$root" = "$NEW_MAJOR" ] && [ "$old" = "$OLD_MAJOR" ] && [ ! -e "$NEW_DIR" ]; then echo "done"
  elif [ "$root" = "$NEW_MAJOR" ] && [ ! -e "$OLD_DIR" ] && [ ! -e "$NEW_DIR" ]; then echo new
  else echo "unknown (root PG_VERSION='$root', $OLD_DIR PG_VERSION='$old', $NEW_DIR $( [ -e "$NEW_DIR" ] && echo exists || echo absent))"
  fi
}

show_layout() {
  log "Contents of $VOL:"
  ls -la "$VOL" | sed 's/^/    /'
}

# Moves every entry of $1 into $2, except the names given after. Renames only (same filesystem).
move_entries() {
  local from=$1 to=$2 name; shift 2
  local skip=" $* "
  for entry in "$from"/* "$from"/.[!.]* "$from"/..?*; do
    [ -e "$entry" ] || continue
    name=${entry##*/}
    case "$skip" in *" $name "*) continue;; esac
    mv -n "$entry" "$to/$name"
    [ -e "$entry" ] && die "could not move $entry to $to/$name (it exists there?)"
  done
  return 0
}

# --- preflight ---
preflight() {  # preflight DATADIR: the old cluster, stopped
  local data=$1
  step "Preflight checks of $data"
  [ "$(id -u)" = "$(stat -c %u "$VOL")" ] \
    || die "run as the owner of $VOL (uid $(stat -c %u "$VOL")), not uid $(id -u): docker compose ... run --rm db-upgrade (user: 70:70)"
  ok "running as uid $(id -u), the owner of the data"

  [ "$(major_of "$OLD_BINDIR/postgres")" = "$OLD_MAJOR" ] || die "$OLD_BINDIR/postgres is not PostgreSQL $OLD_MAJOR"
  [ "$(major_of "$NEW_BINDIR/postgres")" = "$NEW_MAJOR" ] || die "$NEW_BINDIR/postgres is not PostgreSQL $NEW_MAJOR"
  ok "binaries: $("$OLD_BINDIR/postgres" -V), $("$NEW_BINDIR/postgres" -V)"

  [ "$(pg_version_at "$data")" = "$OLD_MAJOR" ] || die "$data/PG_VERSION is '$(pg_version_at "$data")', expected $OLD_MAJOR"
  ok "$data is a PostgreSQL $OLD_MAJOR cluster"

  if [ -e "$data/postmaster.pid" ]; then
    die "$data/postmaster.pid exists: PostgreSQL is running on this data, or didn't stop cleanly.
  Stop it: docker compose stop db (and check: docker ps --filter volume=<the db volume>).
  If no container uses the volume, start the db again and stop it cleanly (a crash leaves this file)."
  fi
  local state
  state=$(controldata_field "$OLD_BINDIR" "$data" "Database cluster state")
  [ "$state" = "shut down" ] || die "the old cluster's state is '$state', not 'shut down': start the db and stop it cleanly"
  ok "the old cluster is stopped cleanly (no postmaster.pid, state 'shut down')"

  CHECKSUMS=$(controldata_field "$OLD_BINDIR" "$data" "Data page checksum version")
  WAL_SEG_MB=$(( $(controldata_field "$OLD_BINDIR" "$data" "Bytes per WAL segment") / 1024 / 1024 ))
  BLOCK_SIZE=$(controldata_field "$OLD_BINDIR" "$data" "Database block size")
  [ "$BLOCK_SIZE" = 8192 ] || die "block size $BLOCK_SIZE: the new server is built for 8192"
  ok "data checksums: $([ "$CHECKSUMS" = 0 ] && echo off || echo "on (version $CHECKSUMS)"), WAL segments: ${WAL_SEG_MB}MB, blocks: $BLOCK_SIZE"

  local auto
  auto=$(grep -vE '^[[:space:]]*(#|$)' "$data/postgresql.auto.conf" 2> /dev/null || true)
  [ -z "$auto" ] || die "postgresql.auto.conf has ALTER SYSTEM settings, they would not be carried over:
$auto
  Record them, then remove them: start the db, ALTER SYSTEM RESET ALL, stop it (see the runbook)."
  ok "no ALTER SYSTEM settings (postgresql.auto.conf)"

  grep -qE '^[[:space:]]*local[[:space:]]+all[[:space:]]+all[[:space:]]+trust' "$data/pg_hba.conf" \
    || die "$data/pg_hba.conf has no 'local all all trust' line: pg_upgrade connects through a local socket without a password"
  ok "pg_hba.conf: local connections are trusted (pg_upgrade needs it), host connections: $(grep -E '^[[:space:]]*host' "$data/pg_hba.conf" | tr -s ' \t' ' ' | paste -sd ';' -)"

  local free_kb need_kb size_kb
  free_kb=$(df -Pk "$VOL" | awk 'NR==2 {print $4}')
  need_kb=$MIN_FREE_KB
  if [ "$MODE" = copy ]; then
    size_kb=$(du -sk "$data" | cut -f1)
    need_kb=$((size_kb + size_kb / 10 + MIN_FREE_KB))
  fi
  log "Filesystem of $VOL: $(stat -f -c %T "$VOL"), $(df -Ph "$VOL" | awk 'NR==2 {print $2" total, "$4" free ("$5" used)"}')"
  [ "$free_kb" -ge "$need_kb" ] || die "$((free_kb / 1024)) MB free in $VOL, mode $MODE needs $((need_kb / 1024)) MB"
  ok "$((free_kb / 1024)) MB free, mode $MODE needs $((need_kb / 1024)) MB"

  # The rest needs the old server
  INSTALL_USER=$DB_USER
  start_old "$data"
  local u
  u=$(old_sql postgres "SELECT rolname FROM pg_authid WHERE oid = 10") || die "could not connect as $DB_USER (POSTGRES_USER)"
  [ "$u" = "$DB_USER" ] || die "the install user of the old cluster is $u, POSTGRES_USER is $DB_USER: set POSTGRES_USER=$u"
  ok "install user: $INSTALL_USER"

  read -r ENCODING OLD_COLLATE OLD_CTYPE < <(old_sql postgres "SELECT pg_encoding_to_char(encoding), datcollate, datctype FROM pg_database WHERE datname = 'template0'" | tr '\t' ' ')
  ok "template0: encoding $ENCODING, LC_COLLATE $OLD_COLLATE, LC_CTYPE $OLD_CTYPE (the new cluster gets the same)"
  local other
  other=$(old_sql postgres "SELECT datname || ': ' || pg_encoding_to_char(encoding) || ' ' || datcollate || ' ' || datctype FROM pg_database
    WHERE (pg_encoding_to_char(encoding), datcollate, datctype) <> ('$ENCODING', '$OLD_COLLATE', '$OLD_CTYPE')")
  [ -z "$other" ] || log "Databases with another locale (pg_upgrade keeps them): $other"

  log "Databases:"
  old_sql postgres "SELECT datname, pg_size_pretty(pg_database_size(oid)), CASE WHEN datallowconn THEN '' ELSE 'connections not allowed' END
    FROM pg_database ORDER BY pg_database_size(oid) DESC" | tr '\t' ' ' | sed 's/^/    /'
  local blocked
  blocked=$(old_sql postgres "SELECT string_agg(datname, ', ') FROM pg_database WHERE NOT datallowconn AND datname <> 'template0'")
  [ -z "$blocked" ] || die "databases that don't allow connections (pg_upgrade needs them): $blocked. ALTER DATABASE ... ALLOW_CONNECTIONS true"

  local spc
  spc=$(old_sql postgres "SELECT string_agg(spcname, ', ') FROM pg_tablespace WHERE spcname NOT IN ('pg_default', 'pg_global')")
  [ -z "$spc" ] || die "tablespaces outside the data directory ($spc): this tool doesn't handle them"

  local db exts
  log "Extensions (they are upgraded by: upgrade.sh post):"
  for db in $(old_sql postgres "SELECT datname FROM pg_database WHERE datallowconn ORDER BY 1"); do
    exts=$(old_sql "$db" "SELECT string_agg(extname || ' ' || extversion, ', ' ORDER BY extname) FROM pg_extension")
    echo "    $db: $exts"
    local prepared
    prepared=$(old_sql "$db" "SELECT count(*) FROM pg_prepared_xacts")
    [ "$prepared" = 0 ] || die "$db has $prepared prepared transactions: COMMIT PREPARED or ROLLBACK PREPARED them first"
  done
  local slots
  slots=$(old_sql postgres "SELECT string_agg(slot_name || ' (' || slot_type || ')', ', ') FROM pg_replication_slots")
  [ -z "$slots" ] || die "replication slots: $slots. Drop them first (SELECT pg_drop_replication_slot('...')), re-create them after"
  ok "no prepared transactions, no replication slots"

  log "Passwords (md5 hashes keep working: we keep the old pg_hba.conf; 'upgrade.sh post' re-hashes $DB_USER's with SCRAM):"
  old_sql postgres "SELECT rolname, CASE WHEN rolpassword IS NULL THEN 'no password' WHEN rolpassword LIKE 'md5%' THEN 'md5'
    WHEN rolpassword LIKE 'SCRAM-SHA-256%' THEN 'scram-sha-256' ELSE 'other' END FROM pg_authid WHERE rolcanlogin ORDER BY 1" \
    | tr '\t' ' ' | sed 's/^/    /'
  stop_old
}

pg_upgrade_cmd() {  # pg_upgrade_cmd OLD NEW [--check]
  local mode_flag
  case "$MODE" in link) mode_flag=--link;; clone) mode_flag=--clone;; copy) mode_flag=--copy;;
    *) die "UPGRADE_MODE must be link, clone or copy, not '$MODE'";; esac
  # cwd: pg_upgrade writes its scripts (delete_old_cluster.sh...) there
  (cd "$STATE_DIR" && "$NEW_BINDIR/pg_upgrade" -b "$OLD_BINDIR" -B "$NEW_BINDIR" -d "$1" -D "$2" -U "$INSTALL_USER" \
    -j "$JOBS" -s "$SOCKET_DIR" -p "$OLD_PORT" -P "$NEW_PORT" "$mode_flag" "${@:3}")
}

initdb_new() {  # initdb_new DIR: an empty cluster with the old one's settings
  local checksum_flag=--no-data-checksums
  [ "$CHECKSUMS" != 0 ] && checksum_flag=--data-checksums
  "$NEW_BINDIR/initdb" -D "$1" -U "$INSTALL_USER" --encoding="$ENCODING" --locale-provider=libc \
    --lc-collate="$OLD_COLLATE" --lc-ctype="$OLD_CTYPE" --locale="$OLD_CTYPE" \
    "$checksum_flag" --wal-segsize="$WAL_SEG_MB" --auth-local=trust --auth-host=scram-sha-256 > "$STATE_DIR/initdb.log" \
    || { cat "$STATE_DIR/initdb.log" >&2; die "initdb failed (above)"; }
  ok "initdb $1: $("$NEW_BINDIR/pg_controldata" -D "$1" | grep -E 'checksum version|Bytes per WAL' | tr -s ' ' | paste -sd ',' -)"
}

# --- commands ---
cmd_check() {
  local state; state=$(detect_state)
  [ "$state" = "old" ] || die "the volume's state is $state, check only works before the upgrade ('status' tells more)"
  mkdir -p "$STATE_DIR"
  preflight "$VOL"
  step "pg_upgrade --check ($MODE), on a throw-away empty PostgreSQL $NEW_MAJOR cluster"
  rm -rf "$CHECK_DIR"
  initdb_new "$CHECK_DIR"
  local r=0
  timed "pg_upgrade --check" pg_upgrade_cmd "$VOL" "$CHECK_DIR" --check || r=$?
  if [ "$r" = 0 ] && [ "$MODE" != clone ]; then
    step "Does the filesystem support --clone (copy-on-write)?"
    if (MODE=clone; pg_upgrade_cmd "$VOL" "$CHECK_DIR" --check) > "$STATE_DIR/check-clone.log" 2>&1; then
      log "clone: SUPPORTED. UPGRADE_MODE=clone keeps the old cluster intact (rollback possible even after 18 ran)"
    else
      log "clone: not supported ($(grep -m1 -i 'clone' "$STATE_DIR/check-clone.log" | tr -s ' ' || echo 'see check-clone.log')). Use link."
    fi
  fi
  rm -rf "$CHECK_DIR"
  [ "$r" = 0 ] || die "pg_upgrade --check failed (above). The data was not changed."
  step "check: everything is ready for: upgrade.sh run (mode $MODE). The data was not changed."
}

cmd_run() {
  local state; state=$(detect_state)
  log "State of $VOL: $state, mode: $MODE"
  case "$state" in
    old)
      mkdir -p "$STATE_DIR"
      preflight "$VOL"
      step "1. Moving the old cluster to $OLD_DIR (renames)"
      # 0700: PostgreSQL refuses to start on a data directory others can read
      mkdir -m 0700 "$OLD_DIR"
      echo "$MODE" > "$STATE_DIR/mode"
      timed "move" move_entries "$VOL" "$OLD_DIR" "${OLD_DIR##*/}" "${STATE_DIR##*/}"
      ;;
    moved)
      if [ -e "$STATE_DIR/new-controldata.txt" ] && [ -e "$NEW_DIR" ]; then
        MODE=$(cat "$STATE_DIR/mode" 2> /dev/null || echo "$MODE")
        log "pg_upgrade had finished (mode $MODE): resuming after it"
        finalize
        return
      fi
      # Starting over: the mode of the previous run, unless one is given now
      [ -n "$MODE_GIVEN" ] || MODE=$(cat "$STATE_DIR/mode" 2> /dev/null || echo "$MODE")
      echo "$MODE" > "$STATE_DIR/mode"
      log "A previous run stopped before the end of pg_upgrade: starting over from the old cluster"
      [ -e "$OLD_DIR/postmaster.pid" ] && die "$OLD_DIR/postmaster.pid exists: is a PostgreSQL running on it?"
      chmod 0700 "$OLD_DIR"
      restore_old_control
      rm -rf "$NEW_DIR"
      preflight "$OLD_DIR"
      ;;
    done) log "Already upgraded: PostgreSQL $NEW_MAJOR in $VOL, the old cluster in $OLD_DIR. Next: start the db with the $NEW_MAJOR image."; return;;
    new) log "Already PostgreSQL $NEW_MAJOR, nothing to do"; return;;
    *) show_layout; die "unexpected state: $state";;
  esac

  step "2. initdb $NEW_DIR (PostgreSQL $NEW_MAJOR, same settings as the old cluster)"
  initdb_new "$NEW_DIR"

  step "3. pg_upgrade --check ($MODE)"
  local rc=0
  timed "pg_upgrade --check" pg_upgrade_cmd "$OLD_DIR" "$NEW_DIR" --check || rc=$?
  [ "$rc" = 0 ] || die "pg_upgrade --check failed (above). The old cluster is intact: upgrade.sh rollback puts it back"

  step "4. pg_upgrade ($MODE)"
  timed "pg_upgrade" pg_upgrade_cmd "$OLD_DIR" "$NEW_DIR" || rc=$?
  [ "$rc" = 0 ] || die "pg_upgrade failed (above, and the logs in $NEW_DIR/pg_upgrade_output.d/). PostgreSQL $NEW_MAJOR never ran on the
  data: upgrade.sh rollback puts the old cluster back, upgrade.sh run starts over"
  # pg_upgrade deletes its output directory only when it succeeded
  [ ! -e "$NEW_DIR/pg_upgrade_output.d" ] || die "pg_upgrade left $NEW_DIR/pg_upgrade_output.d: it didn't finish. upgrade.sh rollback puts the old cluster back"
  # What pg_control looks like before PostgreSQL 18 runs on the data: rollback compares it (see cmd_rollback). Also
  # marks that pg_upgrade succeeded: finalize refuses to run without it.
  controldata "$NEW_BINDIR" "$NEW_DIR" > "$STATE_DIR/new-controldata.txt"
  finalize
}

finalize() {
  [ -s "$STATE_DIR/new-controldata.txt" ] && [ ! -e "$NEW_DIR/pg_upgrade_output.d" ] && [ "$(pg_version_at "$NEW_DIR")" = "$NEW_MAJOR" ] \
    || die "$NEW_DIR is not the result of a successful pg_upgrade: not moving it. upgrade.sh rollback, then run again"
  step "5. Configuration: the old pg_hba.conf and pg_ident.conf (same logins as before)"
  [ -e "$STATE_DIR/pg_hba.conf.initdb" ] || cp "$NEW_DIR/pg_hba.conf" "$STATE_DIR/pg_hba.conf.initdb"
  cp "$OLD_DIR/pg_hba.conf" "$OLD_DIR/pg_ident.conf" "$NEW_DIR/"
  grep -vE '^[[:space:]]*(#|$)' "$NEW_DIR/pg_hba.conf" | sed 's/^/    /'
  log "postgresql.conf is the new one (PostgreSQL $NEW_MAJOR's defaults). Settings of the old one, compare:"
  diff <(grep -vE '^[[:space:]]*(#|$)' "$OLD_DIR/postgresql.conf" | sed 's/[[:space:]]*#.*//') \
       <(grep -vE '^[[:space:]]*(#|$)' "$NEW_DIR/postgresql.conf" | sed 's/[[:space:]]*#.*//') | sed 's/^/    /' || true
  log "('<' old only, '>' new only. The include of qaboard.conf is added back by services/db/init.sh when the db starts)"

  step "6. Moving PostgreSQL $NEW_MAJOR to $VOL (renames)"
  timed "move" move_entries "$NEW_DIR" "$VOL"
  rmdir "$NEW_DIR"
  [ "$(detect_state)" = "done" ] || { show_layout; die "unexpected state after the move: $(detect_state)"; }
  show_layout
  step "Done in $((SECONDS - START))s: $VOL is PostgreSQL $NEW_MAJOR"
  cat <<EOF
Next, see the runbook:
  - start the db with the $NEW_MAJOR image (QABOARD_POSTGRES_IMAGE=postgres:$NEW_MAJOR-alpine)
  - then: docker compose exec -u postgres db bash /opt/upgrade/upgrade.sh post
Until PostgreSQL $NEW_MAJOR starts on it, 'upgrade.sh rollback' puts the old cluster back.$(
  [ "$MODE" = link ] && printf '\nOnce it started: link mode, the old cluster is no longer usable, the way back is a backup.')
EOF
}

restore_old_control() {
  # pg_upgrade --link renames the old cluster's pg_control, so that PostgreSQL 12 can't start on it by mistake
  if [ -e "$OLD_DIR/global/pg_control.old" ] && [ ! -e "$OLD_DIR/global/pg_control" ]; then
    mv "$OLD_DIR/global/pg_control.old" "$OLD_DIR/global/pg_control"
    log "Restored $OLD_DIR/global/pg_control"
  fi
}

cmd_rollback() {
  local state discard=$1
  state=$(detect_state)
  log "State of $VOL: $state"
  local mode; mode=$(cat "$STATE_DIR/mode" 2> /dev/null || echo link)
  case "$state" in
    moved) ;;
    done)
      [ -e "$VOL/postmaster.pid" ] && die "$VOL/postmaster.pid exists: stop the db first (docker compose stop db)"
      if ! diff -q <(controldata "$NEW_BINDIR" "$VOL") "$STATE_DIR/new-controldata.txt" > /dev/null; then
        log "pg_control changed since the upgrade: PostgreSQL $NEW_MAJOR ran on the data"
        diff <(controldata "$NEW_BINDIR" "$VOL") "$STATE_DIR/new-controldata.txt" | sed 's/^/    /' || true
        if [ "$mode" = link ]; then
          die "the upgrade used --link and PostgreSQL $NEW_MAJOR ran: the old cluster's files were changed by it, it is
  not usable anymore. The way back is a backup (runbook: rollback after the start)."
        fi
        [ "$discard" = 1 ] || die "mode $mode: the old cluster is intact, but going back loses what was written since the
  upgrade. If that's what you want: upgrade.sh rollback --discard-new"
      fi
      ;;
    old) log "Nothing to roll back: $VOL is PostgreSQL $OLD_MAJOR"; return;;
    *) show_layout; die "unexpected state: $state";;
  esac
  step "Rolling back to PostgreSQL $OLD_MAJOR"
  [ -e "$OLD_DIR/postmaster.pid" ] && die "$OLD_DIR/postmaster.pid exists: is a PostgreSQL running on it?"
  local discarded
  discarded=$STATE_DIR/discarded-pg$NEW_MAJOR-$(date +%Y%m%d-%H%M%S)
  mkdir -p "$discarded"
  if [ "$state" = "done" ]; then
    move_entries "$VOL" "$discarded" "${OLD_DIR##*/}" "${STATE_DIR##*/}"
  elif [ -e "$NEW_DIR" ]; then
    mv "$NEW_DIR" "$discarded/"
  fi
  restore_old_control
  move_entries "$OLD_DIR" "$VOL"
  rmdir "$OLD_DIR"
  rm -f "$STATE_DIR/new-controldata.txt"
  # The discarded PostgreSQL 18 files are hard links (or copies) of files of the old cluster: deleting them is safe
  rm -rf "$discarded"
  [ "$(detect_state)" = "old" ] || { show_layout; die "unexpected state after the rollback: $(detect_state)"; }
  show_layout
  step "Rolled back: $VOL is PostgreSQL $OLD_MAJOR again. Start the db with the $OLD_MAJOR image (QABOARD_POSTGRES_IMAGE unset)"
}

cmd_status() {
  local state; state=$(detect_state)
  log "State of $VOL: $state (mode: $(cat "$STATE_DIR/mode" 2> /dev/null || echo -))"
  show_layout
  if [ "$state" = "done" ] && [ -e "$STATE_DIR/new-controldata.txt" ]; then
    if diff -q <(controldata "$NEW_BINDIR" "$VOL") "$STATE_DIR/new-controldata.txt" > /dev/null; then
      log "PostgreSQL $NEW_MAJOR has not run on the data yet: rollback is possible"
    else
      log "PostgreSQL $NEW_MAJOR has run on the data"
    fi
  fi
}

# --- a copy of the files of the old cluster, to go back after PostgreSQL 18 ran (link mode) ---
cmd_backup_files() {  # backup_files DIR: DIR/pg12-cluster.tar, from the stopped old cluster
  local dir=${1:?usage: backup-files DIR (a directory mounted with docker compose run -v /host/dir:/backup)}
  local state; state=$(detect_state)
  [ "$state" = "old" ] || die "backup-files copies the old cluster before the upgrade, the state is $state"
  [ ! -e "$VOL/postmaster.pid" ] || die "$VOL/postmaster.pid exists: stop the db first"
  [ "$(controldata_field "$OLD_BINDIR" "$VOL" "Database cluster state")" = "shut down" ] || die "the cluster didn't stop cleanly"
  local size_kb free_kb tar=$dir/pg$OLD_MAJOR-cluster.tar
  size_kb=$(du -sk --exclude="${STATE_DIR##*/}" "$VOL" 2> /dev/null | cut -f1 || du -sk "$VOL" | cut -f1)
  free_kb=$(df -Pk "$dir" | awk 'NR==2 {print $4}')
  log "Copying the old cluster ($((size_kb / 1024)) MB) to $tar ($((free_kb / 1024)) MB free there)"
  [ "$free_kb" -gt $((size_kb + size_kb / 20)) ] || die "not enough space in $dir"
  [ ! -e "$tar" ] || die "$tar exists: move it away first"
  timed "copy" tar -C "$VOL" --exclude="./${STATE_DIR##*/}" -cf "$tar.tmp" .
  mv "$tar.tmp" "$tar"
  controldata "$OLD_BINDIR" "$VOL" > "$tar.controldata.txt"
  ls -l "$tar" | sed 's/^/    /'
  step "backup-files: done. The old cluster can be restored from it with: upgrade.sh restore-files $dir --yes"
}

cmd_restore_files() {  # restore_files DIR: replaces the data of the volume with DIR/pg12-cluster.tar
  local dir=${1:?usage: restore-files DIR --yes} yes=$2 tar=${1}/pg$OLD_MAJOR-cluster.tar state
  state=$(detect_state)
  [ -s "$tar" ] || die "no $tar"
  case "$state" in done|new) ;; *) die "restore-files replaces a PostgreSQL $NEW_MAJOR cluster; the state is $state (rollback?)";; esac
  [ ! -e "$VOL/postmaster.pid" ] || die "$VOL/postmaster.pid exists: stop the db first"
  [ "$yes" = 1 ] || die "this deletes the PostgreSQL $NEW_MAJOR data in $VOL and restores the copy of $(date -r "$tar" '+%F %T'): add --yes"
  step "Deleting the PostgreSQL $NEW_MAJOR data, restoring the copy of the old cluster"
  empty_volume
  timed "restore" tar -C "$VOL" -xf "$tar"
  [ "$(pg_version_at "$VOL")" = "$OLD_MAJOR" ] || die "the restored data is not PostgreSQL $OLD_MAJOR"
  diff <(controldata "$OLD_BINDIR" "$VOL") "$tar.controldata.txt" > /dev/null || die "pg_control of the restored data differs from the copy's"
  step "restore-files: $VOL is the copy of the old cluster. Start the db with the $OLD_MAJOR image (QABOARD_POSTGRES_IMAGE unset)"
}

empty_volume() {
  local entry
  for entry in "$VOL"/* "$VOL"/.[!.]* "$VOL"/..?*; do
    [ -e "$entry" ] && rm -rf "$entry"
  done
  return 0
}

cmd_empty_volume() {  # to restore a dump after PostgreSQL 18 ran: the db then starts with an empty, new cluster
  local yes=$1 state; state=$(detect_state)
  case "$state" in done|new) ;; *) die "empty-volume only deletes a PostgreSQL $NEW_MAJOR cluster; the state is $state";; esac
  [ ! -e "$VOL/postmaster.pid" ] || die "$VOL/postmaster.pid exists: stop the db first"
  [ "$yes" = 1 ] || die "this deletes everything in $VOL (PostgreSQL $NEW_MAJOR and the old cluster): add --yes"
  [ -e "$STATE_DIR/upgrade.log" ] && cp "$STATE_DIR/upgrade.log" "/tmp/upgrade.log" && log "(the upgrade's log was in $STATE_DIR/upgrade.log:)" && tail -5 /tmp/upgrade.log
  empty_volume
  step "empty-volume: $VOL is empty. Starting the db with the $OLD_MAJOR image creates an empty database, restore a dump into it"
}

# --- in the db container, PostgreSQL 18 running ---
psql_db() {  # psql_db DB [psql args]: as the superuser, through the local socket
  local db=$1; shift
  psql -X -q -v ON_ERROR_STOP=1 -U "$DB_USER" -d "$db" "$@"
}

cmd_post() {
  local v; v=$(psql_db postgres -Atc "SHOW server_version_num")
  [ "${v:0:2}" = "$NEW_MAJOR" ] || die "the server is $v, not PostgreSQL $NEW_MAJOR: run this in the db container once it runs $NEW_MAJOR"
  step "1. Extensions"
  local db ext
  for db in $(psql_db postgres -Atc "SELECT datname FROM pg_database WHERE datallowconn ORDER BY 1"); do
    for ext in $(psql_db "$db" -Atc "SELECT e.extname FROM pg_extension e JOIN pg_available_extensions a ON a.name = e.extname
                                     WHERE a.default_version IS DISTINCT FROM e.extversion"); do
      psql_db "$db" -c "ALTER EXTENSION \"$ext\" UPDATE"
      ok "$db: $ext updated to $(psql_db "$db" -Atc "SELECT extversion FROM pg_extension WHERE extname = '$ext'")"
    done
    log "$db: $(psql_db "$db" -Atc "SELECT coalesce(string_agg(extname || ' ' || extversion, ', ' ORDER BY extname), 'no extensions') FROM pg_extension")"
  done

  step "2. $DB_USER's password: md5 -> SCRAM"
  local hash
  hash=$(psql_db postgres -Atc "SELECT left(rolpassword, 3) FROM pg_authid WHERE rolname = '$DB_USER'")
  if [ "$hash" = md5 ]; then
    if [ -z "${POSTGRES_PASSWORD:-}" ]; then
      warn "POSTGRES_PASSWORD is not set: can't re-hash the password. Run: psql -U $DB_USER -c '\\password $DB_USER'"
    elif [ "$(psql_db postgres -v pw="$POSTGRES_PASSWORD" -v u="$DB_USER" -At <<< "SELECT rolpassword = 'md5' || md5(:'pw' || :'u') FROM pg_authid WHERE rolname = :'u'")" != t ]; then
      warn "POSTGRES_PASSWORD is not $DB_USER's current password: not changed. Set it with \\password $DB_USER (psql)"
    else
      # Same password, stored as SCRAM: the pg_hba.conf "md5" lines then authenticate with SCRAM
      psql_db postgres -v pw="$POSTGRES_PASSWORD" -v u="$DB_USER" <<< "SET password_encryption = 'scram-sha-256'; ALTER ROLE :\"u\" PASSWORD :'pw';"
      ok "$DB_USER's password is now stored as SCRAM-SHA-256 (same password)"
    fi
  else
    ok "$DB_USER's password hash: $(case "$hash" in SCR) echo SCRAM-SHA-256;; '') echo none;; *) echo "$hash...";; esac)"
  fi
  local md5_roles
  md5_roles=$(psql_db postgres -Atc "SELECT string_agg(rolname, ', ') FROM pg_authid WHERE rolpassword LIKE 'md5%'")
  [ -z "$md5_roles" ] || warn "roles with md5 passwords (they work, PostgreSQL $NEW_MAJOR warns): $md5_roles. Re-set them with \\password <role>"

  step "3. Planner statistics: analyze what pg_upgrade didn't carry over"
  timed "vacuumdb --analyze-in-stages --missing-stats-only" \
    vacuumdb -U "$DB_USER" --all --analyze-in-stages --missing-stats-only -j "$JOBS"

  step "post: done in $((SECONDS - START))s. $(psql_db postgres -Atc 'SELECT version()')"
}

cmd_fingerprint() {
  # Row counts of every table, and a checksum of samples: the first $FINGERPRINT_ROWS rows by primary key, and the
  # first $FINGERPRINT_ROWS values of every indexed text column in index order (it checks the sort order too).
  # Run before and after the upgrade, then diff the outputs: they must be identical.
  local db=${1:-$DB_NAME} rows=${FINGERPRINT_ROWS:-10000}
  local t0=$SECONDS
  psql_db "$db" -At <<EOF
SET statement_timeout = 0;
SET max_parallel_workers_per_gather = 4;
\o /tmp/fingerprint.sql
SELECT format('SELECT %L, ''rows'', count(*)::text FROM %I.%I;', c.relname, n.nspname, c.relname)
  FROM pg_class c JOIN pg_namespace n ON n.oid = c.relnamespace
  WHERE c.relkind IN ('r', 'p') AND n.nspname NOT IN ('pg_catalog', 'information_schema') AND n.nspname NOT LIKE 'pg\_%'
  ORDER BY 1;
SELECT format('SELECT %L, ''first rows by %s'', md5(string_agg(t::text, E''\n'')) FROM (SELECT * FROM %I.%I t ORDER BY %s LIMIT $rows) t;',
              c.relname, string_agg(a.attname, ',' ORDER BY k.ord), n.nspname, c.relname, string_agg(quote_ident(a.attname), ', ' ORDER BY k.ord))
  FROM pg_index i JOIN pg_class c ON c.oid = i.indrelid JOIN pg_namespace n ON n.oid = c.relnamespace
  CROSS JOIN LATERAL unnest(i.indkey) WITH ORDINALITY k(attnum, ord) JOIN pg_attribute a ON a.attrelid = c.oid AND a.attnum = k.attnum
  WHERE i.indisprimary AND n.nspname NOT IN ('pg_catalog', 'information_schema') AND n.nspname NOT LIKE 'pg\_%'
  GROUP BY c.relname, n.nspname ORDER BY 1;
SELECT DISTINCT format('SELECT %L, ''first values by %s'', md5(string_agg(v, E''\n'')) FROM (SELECT %I::text AS v FROM %I.%I WHERE %I IS NOT NULL ORDER BY %I LIMIT $rows) t;',
              c.relname, a.attname, a.attname, n.nspname, c.relname, a.attname, a.attname)
  FROM pg_index i JOIN pg_class c ON c.oid = i.indrelid JOIN pg_namespace n ON n.oid = c.relnamespace
  JOIN pg_attribute a ON a.attrelid = c.oid AND a.attnum = i.indkey[0]
  JOIN pg_am am ON am.oid = (SELECT relam FROM pg_class WHERE oid = i.indexrelid)
  WHERE am.amname = 'btree' AND a.atttypid IN ('text'::regtype, 'varchar'::regtype, 'bpchar'::regtype, 'name'::regtype)
    AND n.nspname NOT IN ('pg_catalog', 'information_schema') AND n.nspname NOT LIKE 'pg\_%'
  ORDER BY 1;
\o
\i /tmp/fingerprint.sql
EOF
  echo "# fingerprint of $db: $(psql_db "$db" -Atc 'SHOW server_version') in $((SECONDS - t0))s" >&2
}

cmd_amcheck() {
  # Verifies every btree index (structure, and with --heapallindexed that every row is in its indexes). Read-only,
  # AccessShareLock: can run while the app runs, but it reads all the indexes (and the tables, with --heapallindexed).
  local db=${DB_NAME} heap=$1 created=0
  psql_db "$db" -Atc "SELECT 1 FROM pg_extension WHERE extname = 'amcheck'" | grep -q 1 || {
    psql_db "$db" -c "CREATE EXTENSION amcheck"; created=1; }
  step "amcheck: bt_index_check(heapallindexed => $heap) on every btree index of $db, biggest last"
  local oid name size bytes total=0 t0=$SECONDS t errors=0 out
  while IFS=$'\t' read -r oid name size bytes; do
    t=$SECONDS
    if out=$(psql_db "$db" -Atc "SELECT bt_index_check($oid::regclass, $heap)" 2>&1); then
      printf '    %-60s %10s %5ss\n' "$name" "$size" "$((SECONDS - t))"
    else
      printf '    %-60s %10s FAILED: %s\n' "$name" "$size" "$out"; errors=$((errors + 1))
    fi
    total=$((total + bytes))
  done < <(psql_db "$db" -AtF $'\t' -c "SELECT c.oid, n.nspname || '.' || c.relname, pg_size_pretty(pg_relation_size(c.oid)), pg_relation_size(c.oid)
             FROM pg_index i JOIN pg_class c ON c.oid = i.indexrelid JOIN pg_namespace n ON n.oid = c.relnamespace
             JOIN pg_am am ON am.oid = c.relam
             WHERE am.amname = 'btree' AND i.indisvalid AND i.indisready AND c.relpersistence <> 't'
             ORDER BY pg_relation_size(c.oid)")
  local secs=$((SECONDS - t0)) gb
  gb=$(awk "BEGIN {printf \"%.2f\", $total / 1024 / 1024 / 1024}")
  [ "$created" = 1 ] && psql_db "$db" -c "DROP EXTENSION amcheck"
  step "amcheck: $errors errors, $gb GB of indexes in ${secs}s ($(awk "BEGIN {printf \"%.0f\", $total / 1048576 / ($secs > 0 ? $secs : 1)}") MB/s)"
  [ "$errors" = 0 ] || die "$errors indexes failed the check: REINDEX INDEX CONCURRENTLY them, and report it"
}

cmd_cleanup() {
  local yes=$1 state; state=$(detect_state)
  [ "$state" = "done" ] || die "cleanup deletes $OLD_DIR after an upgrade; the state is $state"
  [ "$(major_of "$(command -v postgres)")" = "$NEW_MAJOR" ] || die "run it in the db container running PostgreSQL $NEW_MAJOR"
  du -sh "$OLD_DIR" "$STATE_DIR" 2> /dev/null | sed 's/^/    /' || true
  [ "$yes" = 1 ] || die "this deletes the old cluster ($OLD_DIR) and the upgrade's logs ($STATE_DIR) for good: add --yes"
  rm -rf "$OLD_DIR" "$STATE_DIR"
  log "Deleted $OLD_DIR and $STATE_DIR. $(df -Ph "$VOL" | awk 'NR==2 {print $4" free"}')"
}

# --- main ---
COMMAND=${1:-help}; [ $# -gt 0 ] && shift
DISCARD=0; YES=0; HEAP=false; ARGS=()
while [ $# -gt 0 ]; do
  case "$1" in
    --mode) MODE=$2; MODE_GIVEN=1; shift;;
    --mode=*) MODE=${1#*=}; MODE_GIVEN=1;;
    --discard-new) DISCARD=1;;
    --yes) YES=1;;
    --heapallindexed) HEAP=true;;
    *) ARGS+=("$1");;
  esac
  shift
done

case "$COMMAND" in
  check|run|rollback|status|backup-files|restore-files|empty-volume)
    [ -x "$OLD_BINDIR/postgres" ] || die "no PostgreSQL $OLD_MAJOR in $OLD_BINDIR: run this in the db-upgrade container"
    ;;
esac
case "$COMMAND" in
  check) cmd_check;;
  run)
    # Everything also goes to pg-upgrade/upgrade.log, in the volume
    if mkdir -p "$STATE_DIR" 2> /dev/null; then exec > >(tee -a "$STATE_DIR/upgrade.log") 2>&1; fi
    cmd_run;;
  rollback) cmd_rollback "$DISCARD";;
  status) cmd_status;;
  post) cmd_post;;
  fingerprint) cmd_fingerprint "${ARGS[@]}";;
  amcheck) cmd_amcheck "$HEAP";;
  cleanup) cmd_cleanup "$YES";;
  backup-files) cmd_backup_files "${ARGS[0]:-}";;
  restore-files) cmd_restore_files "${ARGS[0]:-}" "$YES";;
  empty-volume) cmd_empty_volume "$YES";;
  help|-h|--help) usage;;
  *) usage; die "unknown command: $COMMAND";;
esac
