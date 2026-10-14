#!/bin/bash
# Entrypoint of the db service in deployments/sirc/sirc.yml: the postgres image's entrypoint, with our settings.
# The settings of /postgres.host.conf (services/db/postgres.conf) are copied in $PGDATA (postgres wants files it
# owns, and we cannot chown on our shared storage), and included at the end of postgresql.conf so that they win.
set -e
PGDATA=${PGDATA:-/var/lib/postgresql/data}

apply_config() {
    cp /postgres.host.conf "$PGDATA/postgres.conf"
    if ! grep -qxF "include_if_exists = 'postgres.conf'" "$PGDATA/postgresql.conf"; then
        printf "\n# QA-Board's settings, see services/db/init.sh\ninclude_if_exists = 'postgres.conf'\n" >> "$PGDATA/postgresql.conf"
    fi
}

if [[ ! -f $PGDATA/PG_VERSION ]]; then
    # fresh cluster: the postgres entrypoint runs initdb, then the scripts in /docker-entrypoint-initdb.d (as postgres),
    # then starts the server: with our settings.
    { declare -f apply_config; echo apply_config; } > /docker-entrypoint-initdb.d/zz-qaboard-config.sh
    # $@ is empty on `docker compose up`; default to "postgres" so it starts.
    if [[ $# -eq 0 ]]; then
        exec docker-entrypoint.sh postgres
    fi
    exec docker-entrypoint.sh "$@"
fi
# already initialized
apply_config
chown postgres "$PGDATA/postgres.conf"
exec docker-entrypoint.sh postgres "$@"
