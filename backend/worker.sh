#!/bin/bash
# Runs the worker for the server's background tasks (backend/tasks.py), see backend/celery_app.py
# Like uwsgi, it runs as $UWSGI_UID/$UWSGI_GID if they are set (e.g. at SIRC, the LSF bridge uses their ssh keys).
set -e
cd /qaboard/backend

args=(
  --app backend.celery_app worker
  --queues "${QABOARD_TASKS_QUEUE:-qaboard-server}"
  # Each task can wait minutes on ssh/LSF, and uses threads to submit runs in parallel (QABOARD_REDO_CONCURRENCY)
  --concurrency "${QABOARD_WORKER_CONCURRENCY:-4}"
  --loglevel "${QABOARD_WORKER_LOGLEVEL:-INFO}"
  --without-gossip --without-mingle
)

if [ -n "${UWSGI_UID:-}" ]; then
  # Like init.sh: without $SECRET_KEY, a key is generated in this file, maybe by root (e.g. the migrations)
  secret_key="${QABOARD_DATA_DIR:-/var/qaboard}/secret_key"
  if [ -f "$secret_key" ]; then
    chown "$UWSGI_UID${UWSGI_GID:+:$UWSGI_GID}" "$secret_key"
  fi
  # The whole process runs as the user, not only the tasks (the code reads files that may be readable only by them)
  groups=--clear-groups
  id "$UWSGI_UID" > /dev/null 2>&1 && groups=--init-groups
  gid="${UWSGI_GID:-$(id -g "$UWSGI_UID" 2> /dev/null || echo "$UWSGI_UID")}"
  exec setpriv --reuid "$UWSGI_UID" --regid "$gid" $groups celery "${args[@]}"
fi
exec celery "${args[@]}"
