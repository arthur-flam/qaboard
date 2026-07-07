"""
Update commit statuses on the project's git host.
GitLab and GitHub are both first-class citizens; other hosts can be added here.

The host is picked from, in order:
- `project.remote_type: gitlab | github` in qaboard.yaml
- the hostname in `project.url`
- $GITHUB_REPOSITORY (set by GitHub Actions)
- gitlab as a backward-compatible default
"""
import os
import re

import click
import requests
from urllib.parse import urlparse

from .config import config, root_qatools_config, commit_id as default_commit_id
from .config import secrets


github_token = os.environ.get('GITHUB_ACCESS_TOKEN', os.environ.get('GITHUB_TOKEN', secrets.get('GITHUB_ACCESS_TOKEN')))
# GitHub Actions sets $GITHUB_API_URL, GitHub Enterprise users can override it
github_api = os.environ.get('GITHUB_API_URL', 'https://api.github.com')


def parse_remote(url):
  """'git@github.com:group/repo.git' or 'https://github.com/group/repo' -> (hostname, 'group/repo')"""
  if not url:
    return None, None
  match = re.match(r'(?:git\+)?ssh://git@([^/]+)/(.*)', url) or re.match(r'git@([^:]+):(.*)', url)
  if match:
    hostname, path = match.groups()
  else:
    url_info = urlparse(url)
    hostname, path = url_info.hostname, (url_info.path or '').lstrip('/')
  if path and path.endswith('.git'):
    path = path[:-len('.git')]
  return hostname, path


def remote_type():
  project = root_qatools_config.get('project', {})
  if project.get('remote_type'):
    return project['remote_type']
  hostname, _ = parse_remote(project.get('url'))
  if hostname:
    if 'github' in hostname:
      return 'github'
    return 'gitlab'
  if os.environ.get('GITHUB_REPOSITORY'):
    return 'github'
  return 'gitlab'


def github_repo():
  _, path = parse_remote(root_qatools_config.get('project', {}).get('url'))
  return path or os.environ.get('GITHUB_REPOSITORY')


def update_github_status(state, name, target_url, description, commit_id=default_commit_id):
  # https://docs.github.com/rest/commits/statuses
  repo = github_repo()
  if not github_token or not repo:
    if not github_token:
      click.secho("WARNING: GITHUB_ACCESS_TOKEN is not defined, cannot update the commit status.", fg='yellow', err=True)
    return
  github_states = {'failed': 'failure', 'success': 'success', 'pending': 'pending', 'running': 'pending', 'canceled': 'error'}
  url = f"{github_api}/repos/{repo}/statuses/{commit_id}"
  try:
    r = requests.post(
      url,
      headers={'Authorization': f"token {github_token}", 'Accept': 'application/vnd.github+json'},
      json={
        "state": github_states.get(state, 'error'),
        "context": name,
        "target_url": target_url,
        "description": description,
      },
    )
    r.raise_for_status()
  except Exception as e:
    click.secho(f"WARNING: Could not update the GitHub commit status at {url}: {e}", fg='yellow', err=True)


def can_update_status():
  """Do we have credentials to update commit statuses on the project's git host?"""
  if remote_type() == 'github':
    return bool(github_token)
  from .gitlab import gitlab_token
  return bool(gitlab_token)


def update_commit_status(state, name, target_url, description, commit_id=default_commit_id):
  """Update the commit's status on whatever git host the project uses."""
  if remote_type() == 'github':
    update_github_status(state, name, target_url, description, commit_id=commit_id)
  else:
    from .gitlab import update_gitlab_status
    update_gitlab_status(state, name, target_url, description, commit_id=commit_id)
