# syntax=docker/dockerfile:1
# Wraps a base image (python, node, debian...) so that it trusts an extra CA bundle.
# Only used behind TLS-intercepting proxies, see compose.ca.yml and README.md.
# The CA bundle is passed as a build secret (it ends up in the local image's trust store, never in the repo).
ARG BASE
FROM ${BASE}
RUN --mount=type=secret,id=ca,target=/tmp/extra-ca.crt \
    set -e; \
    for d in /usr/local/share/ca-certificates /etc/ssl/certs; do mkdir -p $d; done; \
    cp /tmp/extra-ca.crt /usr/local/share/ca-certificates/qaboard-demo-extra-ca.crt; \
    if command -v update-ca-certificates >/dev/null 2>&1; then \
      update-ca-certificates >/dev/null 2>&1 || true; \
    else \
      cat /tmp/extra-ca.crt >> /etc/ssl/certs/ca-certificates.crt; \
    fi; \
    if ! grep -q "$(sed -n 2p /tmp/extra-ca.crt)" /etc/ssl/certs/ca-certificates.crt 2>/dev/null; then \
      cat /tmp/extra-ca.crt >> /etc/ssl/certs/ca-certificates.crt; \
    fi
# python/requests/pip/uv/curl/git and node read those
ENV SSL_CERT_FILE=/etc/ssl/certs/ca-certificates.crt \
    REQUESTS_CA_BUNDLE=/etc/ssl/certs/ca-certificates.crt \
    PIP_CERT=/etc/ssl/certs/ca-certificates.crt \
    UV_NATIVE_TLS=1 \
    NODE_EXTRA_CA_CERTS=/usr/local/share/ca-certificates/qaboard-demo-extra-ca.crt
