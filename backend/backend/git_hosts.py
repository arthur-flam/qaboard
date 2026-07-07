"""
Multi-git-host support: GitLab, GitHub, and generic git hosts are all first-class citizens.

We normalize the different webhook payloads and project metadata into a single schema,
stored at Project.data['git']:

    {
      "host": "gitlab" | "github" | "git",       # host type, used to pick API/URL conventions
      "path_with_namespace": "group/repo",       # the repository identifier
      "name": "repo",
      "web_url": "https://github.com/group/repo",
      "clone_url": "https://github.com/group/repo.git",  # http(s) clone URL
      "default_branch": "main",
      "avatar_url": "https://...",               # optional
    }

For backward compatibility we keep the historical GitLab keys (path_with_namespace...),
so old clients and old database rows keep working. Use
backend/scripts/migrate_to_multi_git_hosts.py to normalize existing rows.
"""
import os
import json
from urllib.parse import urlparse


def guess_host_type(url: str) -> str:
  """Guess the host type from a web/clone URL."""
  if not url:
    return 'git'
  hostname = urlparse(url).hostname or ''
  if 'github' in hostname:
    return 'github'
  if 'gitlab' in hostname:
    return 'gitlab'
  if 'bitbucket' in hostname:
    return 'bitbucket'
  return 'git'


def normalize_git_project(git_data: dict) -> dict:
  """
  Takes a project's raw "git" metadata - as sent by a GitLab/GitHub webhook
  or already normalized - and returns the normalized schema described above.
  """
  if not git_data:
    return {}
  # GitHub repository payload: https://docs.github.com/webhooks/webhook-events-and-payloads#push
  if 'full_name' in git_data:
    owner = git_data.get('owner') or {}
    return {
      'host': 'github',
      'path_with_namespace': git_data['full_name'],
      'name': git_data.get('name', git_data['full_name'].split('/')[-1]),
      'web_url': git_data.get('html_url'),
      'clone_url': git_data.get('clone_url'),
      'default_branch': git_data.get('default_branch', 'main'),
      'avatar_url': owner.get('avatar_url') or git_data.get('avatar_url'),
    }
  # GitLab project payload: https://docs.gitlab.com/ee/user/project/integrations/webhook_events.html#push-events
  if 'path_with_namespace' in git_data and 'host' not in git_data:
    web_url = git_data.get('web_url') or git_data.get('homepage')
    return {
      'host': guess_host_type(web_url) if web_url else 'gitlab',
      'path_with_namespace': git_data['path_with_namespace'],
      'name': git_data.get('name', git_data['path_with_namespace'].split('/')[-1]),
      'web_url': web_url,
      'clone_url': git_data.get('git_http_url') or git_data.get('http_url'),
      'default_branch': git_data.get('default_branch', 'master'),
      'avatar_url': git_data.get('avatar_url'),
    }
  # Already normalized
  return git_data


def normalize_gitlab_push(payload: dict) -> dict:
  """GitLab push event -> normalized {ref, checkout_sha, project}."""
  return {
    'ref': payload.get('ref', ''),
    'checkout_sha': payload.get('checkout_sha'),
    'project': normalize_git_project(payload.get('project', {})),
  }


def normalize_github_push(payload: dict) -> dict:
  """GitHub push event -> normalized {ref, checkout_sha, project}."""
  return {
    'ref': payload.get('ref', ''),
    # `after` is all-zeros on branch deletion: treat like GitLab's null checkout_sha
    'checkout_sha': None if set(payload.get('after', '')) == {'0'} else payload.get('after'),
    'project': normalize_git_project(payload.get('repository', {})),
  }


def normalize_push_event(payload: dict, host: str = None) -> dict:
  """Normalize a push webhook payload from any supported host."""
  if host == 'github' or (host is None and 'repository' in payload and 'full_name' in payload.get('repository', {})):
    return normalize_github_push(payload)
  if host == 'gitlab' or (host is None and 'project' in payload):
    return normalize_gitlab_push(payload)
  # Generic: expect an already-normalized payload
  return {
    'ref': payload.get('ref', ''),
    'checkout_sha': payload.get('checkout_sha'),
    'project': normalize_git_project(payload.get('project', {})),
  }


def commit_url(git_data: dict, hexsha: str) -> str:
  """Link to a commit on the project's git host."""
  git_data = normalize_git_project(git_data or {})
  web_url = git_data.get('web_url')
  if not web_url or not hexsha:
    return None
  if git_data.get('host') == 'gitlab':
    return f"{web_url}/-/commit/{hexsha}"
  # github, bitbucket ("/commits/"), gitea... all accept /commit/<sha> except bitbucket
  if git_data.get('host') == 'bitbucket':
    return f"{web_url}/commits/{hexsha}"
  return f"{web_url}/commit/{hexsha}"


def branch_url(git_data: dict, branch: str) -> str:
  """Link to a branch on the project's git host."""
  git_data = normalize_git_project(git_data or {})
  web_url = git_data.get('web_url')
  if not web_url or not branch:
    return None
  if git_data.get('host') == 'gitlab':
    return f"{web_url}/-/tree/{branch}"
  if git_data.get('host') == 'bitbucket':
    return f"{web_url}/src/{branch}"
  return f"{web_url}/tree/{branch}"


def git_credentials(hostname: str):
  """
  Returns (username, token) used to clone repositories over http(s), or None.
  Configure with:
  - $GIT_HOSTS, a JSON object: {"github.com": {"token": "...", "username": "x-access-token"}}
  - $GITHUB_ACCESS_TOKEN for GitHub hosts
  - $GITLAB_ACCESS_TOKEN for GitLab hosts (backward-compatible default)
  """
  try:
    git_hosts = json.loads(os.environ.get('GIT_HOSTS', '{}'))
  except json.decoder.JSONDecodeError:
    git_hosts = {}
  if hostname in git_hosts and git_hosts[hostname].get('token'):
    host_config = git_hosts[hostname]
    return host_config.get('username', 'oauth2'), host_config['token']
  if 'github' in hostname and os.environ.get('GITHUB_ACCESS_TOKEN'):
    return 'x-access-token', os.environ['GITHUB_ACCESS_TOKEN']
  if os.environ.get('GITLAB_ACCESS_TOKEN'):
    return 'oauth2', os.environ['GITLAB_ACCESS_TOKEN']
  return None


def with_credentials(clone_url: str) -> str:
  """Inject credentials in an http(s) clone URL, if we have any for its host."""
  url_info = urlparse(clone_url)
  if url_info.scheme not in ('http', 'https') or url_info.username:
    return clone_url
  credentials = git_credentials(url_info.hostname)
  if not credentials:
    return clone_url
  username, token = credentials
  return clone_url.replace('://', f'://{username}:{token}@', 1)
