# PostgreSQL 18 with the binaries of PostgreSQL 12 in /usr/local/pg12: what pg_upgrade needs to upgrade a cluster in
# place (both versions' binaries in one container). Used by upgrade.sh, see its header. Built by:
#   docker compose --profile db-upgrade build db-upgrade
#
# Both are the official alpine images: same libc (musl), so text sorts the same before and after the upgrade
# (a Debian/glibc image would change the collation order and silently corrupt the indexes on text columns).
# The old binaries keep their own lib/ and share/ (PostgreSQL finds them relative to bin/). The shared libraries they
# need that the new image doesn't have (other sonames, e.g. an older ICU) go to /usr/local/pg12/compat, which the
# loader searches last: the new binaries never load them. See build-old-binaries.sh.
ARG OLD_IMAGE=postgres:12-alpine
ARG NEW_IMAGE=postgres:18-alpine

FROM ${OLD_IMAGE} AS old

FROM ${NEW_IMAGE} AS build
ARG OLD_MAJOR=12
COPY --from=old /usr/local/bin /opt/old/bin
COPY --from=old /usr/local/lib /opt/old/lib
COPY --from=old /usr/local/share/postgresql /opt/old/share/postgresql
COPY --from=old /lib /tmp/old-root/lib
COPY --from=old /usr/lib /tmp/old-root/usr/lib
COPY build-old-binaries.sh /tmp/
RUN OLD_MAJOR=$OLD_MAJOR sh /tmp/build-old-binaries.sh

FROM ${NEW_IMAGE}
ARG OLD_MAJOR=12
ENV OLD_BINDIR=/usr/local/pg${OLD_MAJOR}/bin
COPY --from=build /usr/local/pg${OLD_MAJOR} /usr/local/pg${OLD_MAJOR}
COPY --from=build /etc/ld-musl-*.path /etc/
RUN "$OLD_BINDIR/postgres" --version && "$OLD_BINDIR/pg_ctl" --version && pg_upgrade --version
