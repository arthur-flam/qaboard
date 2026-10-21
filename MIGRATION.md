# Migration Guide: Branch Unification

This document covers breaking changes from the branch unification
(merging master-sirc, master-korea into a single master).

## For SIRC and DSK Users
Each site's configuration (compose overlay, nginx, CLI site package, docs for its users, CI) is now its own
repository, cloned into `deployments/<site>/`. Its README has the commands, including the new `pip install` URL
of the site package.

Breaking changes for both:
- `--lsf-threads` renamed to `--lsf-max-threads`
- DSK: DB migration required: `is_ldap`/`is_sso` booleans -> `login_type` string field. Run: `alembic upgrade head`

## For Open-Source Users

No breaking changes. The default behavior is unchanged.
New features available: LDAP/SAML auth, LSF/celery runners, multiple
image servers -- all opt-in via ENV vars.

Optional site settings for the backend:
- `GITLAB_HOST`: also the fallback for git links in the UI (default: https://gitlab.com)
- `QABOARD_QUOTA_URL_TEMPLATE`: shows a "Quota" link, with `{user_name}` and `{project}` placeholders
- `QABOARD_SUPPORT_URL`: where users report bugs, can be a `mailto:` (default: GitHub issues)

Optional site settings for the CLI (env vars, or defaults from a site package):
- `QABOARD_UPGRADE_COMMAND`, `QABOARD_LATEST_VERSION_URL`: how we check for and suggest updates (default: PyPI)
- `QABOARD_IDB_BACKLOG_DIR` (SIRC only)

All Dockerfiles now build cleanly with no build args (proxy/cert blocks
are skipped when args are empty).
