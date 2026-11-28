#!/bin/sh
# QABOARD_DEPLOY_PRE_HOOK in production.env: deploy.py runs it before each deploy, in the qaboard repository, with
# QABOARD_SITE_DIR set. E.g. list the autofs folders the overlay binds in containers, so that they are mounted.
set -e
echo "Deploying with the site directory $QABOARD_SITE_DIR"
