"""
Git hosts: where the projects' repositories live (GitLab, GitHub, Gitea/Forgejo, Bitbucket, or any git server).
We clone the repositories, receive their push webhooks, link to them, show avatars and talk to their CI.

Configuration
-------------
QABOARD_GIT_HOSTS is a JSON list, or the path to a JSON/YAML file with the list:
  [
    {"type": "github", "url": "https://github.com", "token": "ghp_..."},
    {"type": "gitlab", "url": "https://gitlab.example.com", "token_env": "GITLAB_ACCESS_TOKEN", "webhook_secret": "..."}
  ]
  - type: gitlab, github, gitea (or forgejo), bitbucket, generic
  - url: the host's web URL
  - token (or token_env, the name of a variable with the token): to clone and call the API.
    Use "user:password" if the host needs a user name (e.g. Bitbucket app passwords).
  - webhook_secret (or webhook_secret_env): the secret its webhooks send. Default: QABOARD_WEBHOOK_SECRET
  - api_url: if it's not the usual one for this type of host (e.g. https://api.github.com)
  - hostnames: other names of the host, to recognize repository URLs, e.g. an ssh alias
  - name: shown to users
  - user_avatar_url: users' avatars, with {url} and {username} (default for GitHub: {url}/{username}.png).
    For GitLab, only for users who didn't upload an avatar. Default: QABOARD_AVATAR_URL (with {user_name})

Backward compatible with the variables we used before:
  - GITLAB_HOST (default https://gitlab.com) and GITLAB_ACCESS_TOKEN.
    QABOARD_GITLAB_HOSTS: other GitLab hosts that may receive GITLAB_ACCESS_TOKEN (comma-separated).
  - github.com, with GITHUB_ACCESS_TOKEN.
    QABOARD_GITHUB_HOSTS: GitHub Enterprise hosts that may receive GITHUB_ACCESS_TOKEN (comma-separated).
  If QABOARD_GIT_HOSTS lists those hosts without a token, they get those tokens.
The default host, for projects we know nothing about, is GITLAB_HOST if set, else the first in QABOARD_GIT_HOSTS.

Repository data
---------------
Push webhooks store the repository in Project.data['git'], with the same keys for all hosts:
  {
    "hosting_type": "github",           # gitlab, github, gitea, bitbucket, generic
    "host": "https://github.com",       # the host's url, as listed in /api/v1/config's `git_hosts`
    "path_with_namespace": "org/repo",  # the repository, also the id of the root QA-Board project
    "web_url": "https://github.com/org/repo",
    "name": "repo",
    "description": "...",
    "avatar_url": "https://...",        # the project's (or GitHub owner's) avatar
    "default_branch": "main",
    "namespace": "org",
    "git_http_url": "...", "git_ssh_url": "...",
  }
GitLab projects keep all the other fields of GitLab's push events (id, visibility_level...).
Projects created by the CLI, or before hosts were configurable, can lack some of those keys:
use `git_hosts.for_repo(git)` to know their host.
"""
import json
from pathlib import Path

import yaml

from .base import GitHost, default_avatar_url
from .gitlab import GitLabHost
from .github import GitHubHost
from .gitea import GiteaHost
from .bitbucket import BitbucketHost
from . import committers


HOST_TYPES = {
  'gitlab': GitLabHost,
  'github': GitHubHost,
  'gitea': GiteaHost,
  'forgejo': GiteaHost,
  'bitbucket': BitbucketHost,
  'generic': GitHost,
}


class UntrustedHost(ValueError):
  pass


class GitHosts:
  """The git hosts we know about."""

  def __init__(self, hosts, default, webhook_secret=''):
    self.hosts = list(hosts)
    self.default = default
    # for webhooks from hosts we don't know (we reject them, but first check they're authentic)
    self.webhook_secret = webhook_secret

  def __iter__(self):
    return iter(self.hosts)

  def __repr__(self):
    return f"<GitHosts {self.hosts}>"

  def public(self):
    return [h.public() for h in self.hosts]

  def find(self, url, type=None):
    """The host of a URL or hostname."""
    if type:
      type = HOST_TYPES[type].type if type in HOST_TYPES else type
    return next((h for h in self.hosts if h.matches(url) and (not type or h.type == type)), None)

  def of_type(self, type):
    if self.default.type == type:
      return self.default
    return next((h for h in self.hosts if h.type == type), None)

  def for_repo(self, git=None, project_url=None):
    """
    The host of a repository, from its Project.data['git'] (see above).
    project_url: from qaboard.yaml, for projects that have no git data.
    """
    git = git or {}
    hosting_type = git.get('hosting_type')
    for url in (git.get('host'), git.get('web_url'), project_url):
      host = self.find(url, hosting_type) if url else None
      if host:
        return host
    if git.get('web_url') and hosting_type not in (None, 'gitlab'):
      raise UntrustedHost(f"Unknown {hosting_type} host: {git['web_url']}. Add it to QABOARD_GIT_HOSTS.")
    # Before hosts were configurable, all repositories were on GITLAB_HOST, except GitHub's.
    host = self.of_type(hosting_type) if hosting_type else self.default
    if not host:
      raise UntrustedHost(f"No {hosting_type} host is configured. Add one to QABOARD_GIT_HOSTS.")
    return host

  def describe(self, git, project_url=None):
    """Adds hosting_type and host to a repository's data (if we know its host)."""
    try:
      return self.for_repo(git, project_url).describe(git or {})
    except UntrustedHost:
      return git


def read_hosts_config(value):
  """QABOARD_GIT_HOSTS: a JSON list, or the path to a JSON/YAML file"""
  if not value:
    return []
  if isinstance(value, list):
    return value
  value = value.strip()
  try:
    if value.startswith(('[', '{')):
      hosts = json.loads(value)
    else:
      with Path(value).expanduser().open() as f:
        hosts = yaml.safe_load(f) or []
  except (OSError, yaml.YAMLError) as e:
    raise ValueError(f"Could not read QABOARD_GIT_HOSTS: {e}")
  if not isinstance(hosts, list) or not all(isinstance(h, dict) for h in hosts):
    raise ValueError("QABOARD_GIT_HOSTS should be a list of {type, url, ...}")
  return hosts


def make_host(entry, get):
  entry = dict(entry)
  type = entry.pop('type', None)
  if type not in HOST_TYPES:
    raise ValueError(f"QABOARD_GIT_HOSTS: unknown type {type!r}, expected one of {', '.join(HOST_TYPES)}")
  for key in ('token', 'webhook_secret'):
    variable = entry.pop(f'{key}_env', None)
    if variable and not entry.get(key):
      entry[key] = get(variable, '')
  try:
    return HOST_TYPES[type](**entry)
  except TypeError as e:
    raise ValueError(f"QABOARD_GIT_HOSTS: invalid entry for {entry.get('url')}: {e}")


def load_git_hosts(get=None):
  """
  get(name, default): reads the configuration.
  Default: environment variables > shared secrets > site package, like the CLI.
  """
  if get is None:
    from qaboard.site_config import site_config as get
  hostnames = lambda name: [h if '://' in h else f"https://{h}" for h in (get(name, '') or '').split(',') if h.strip()]

  configured = [make_host(entry, get) for entry in read_hosts_config(get('QABOARD_GIT_HOSTS'))]
  gitlab_host = get('GITLAB_HOST')
  gitlab_token, github_token = get('GITLAB_ACCESS_TOKEN', '') or '', get('GITHUB_ACCESS_TOKEN', '') or ''
  legacy = [
    *([GitLabHost(gitlab_host or 'https://gitlab.com', token=gitlab_token)] if gitlab_host or not configured else []),
    *[GitLabHost(url, token=gitlab_token) for url in hostnames('QABOARD_GITLAB_HOSTS')],
    GitHubHost('https://github.com', token=github_token),
    *[GitHubHost(url, token=github_token) for url in hostnames('QABOARD_GITHUB_HOSTS')],
  ]
  hosts = list(configured)
  for legacy_host in legacy:
    same = next((h for h in configured if h.type == legacy_host.type and h.hostname == legacy_host.hostname), None)
    if same:
      same.token = same.token or legacy_host.token
    elif not any(h.hostname == legacy_host.hostname for h in hosts):
      hosts.append(legacy_host)

  webhook_secret = get('QABOARD_WEBHOOK_SECRET', '') or ''
  # The site's avatars for users, e.g. from a company directory, for GitLab users who didn't upload one
  avatar_url = get('QABOARD_AVATAR_URL')
  for host in hosts:
    host.webhook_secret = host.webhook_secret or webhook_secret
    if avatar_url and host.type == 'gitlab' and not host.user_avatar_url:
      host.user_avatar_url = avatar_url.replace('{user_name}', '{username}')

  if gitlab_host:
    default = next(h for h in hosts if h.matches(gitlab_host))
  else:
    default = hosts[0]
  return GitHosts(hosts, default, webhook_secret)


git_hosts = load_git_hosts()


def committer_avatar_url(name, git=None, project_url=None):
  """The avatar of a commit's author, in a repository described by Project.data['git']. No network calls."""
  if not name:
    return ''
  committer = committers.lookup(name)
  try:
    host = git_hosts.for_repo(git, project_url)
  except UntrustedHost:
    return default_avatar_url(committer)
  return host.avatar_url(name, committer)
