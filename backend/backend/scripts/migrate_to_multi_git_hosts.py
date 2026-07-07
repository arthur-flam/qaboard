#!/usr/bin/env python
"""
Normalizes the git metadata stored in Project.data['git'] and
CiCommit.data['git'] to the multi-git-host schema described in
backend/git_hosts.py.

Historically QA-Board stored the raw GitLab webhook "project" payload.
After this migration every project has a normalized:
    {"host", "path_with_namespace", "name", "web_url", "clone_url",
     "default_branch", "avatar_url"}

Idempotent: running it twice is a no-op.

Usage:
    # from the backend/ directory, with the same env as the server:
    python -m backend.scripts.migrate_to_multi_git_hosts [--dry-run]
"""
import argparse

from sqlalchemy.orm.attributes import flag_modified

from backend.database import db_session
from backend.models import Project
from backend.git_hosts import normalize_git_project


def main():
  parser = argparse.ArgumentParser(description=__doc__)
  parser.add_argument('--dry-run', action='store_true', help="Only print what would change")
  args = parser.parse_args()

  projects = db_session.query(Project).all()
  nb_migrated = 0
  for project in projects:
    git_data = (project.data or {}).get('git')
    if not git_data:
      print(f"  - {project.id}: no git metadata, skipping")
      continue
    normalized = normalize_git_project(git_data)
    if normalized == git_data:
      continue
    nb_migrated += 1
    print(f"  * {project.id}: {git_data.get('web_url') or git_data.get('homepage')} -> host={normalized.get('host')}")
    if args.dry_run:
      continue
    project.data['git'] = normalized
    flag_modified(project, "data")
    db_session.add(project)
  if not args.dry_run:
    db_session.commit()
  print(f"Done: normalized {nb_migrated}/{len(projects)} projects{' (dry-run)' if args.dry_run else ''}")


if __name__ == '__main__':
  main()
