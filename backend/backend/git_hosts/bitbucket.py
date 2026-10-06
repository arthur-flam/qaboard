"""
Bitbucket Cloud (bitbucket.org). Bitbucket Data Center can be used as a "generic" host, without webhooks.
"""
import re

from .base import GitHost, hmac_sha256, same_secret


class BitbucketHost(GitHost):
  type = 'bitbucket'
  webhook = 'bitbucket'
  # for repository, project and workspace access tokens. Use "user:app_password" for app passwords
  token_user = 'x-token-auth'

  def default_api_url(self):
    if self.hostname == 'bitbucket.org':
      return 'https://api.bitbucket.org/2.0'
    return f"{self.url}/rest/api/1.0"

  @classmethod
  def check_webhook_secret(cls, headers, body, secret):
    # https://support.atlassian.com/bitbucket-cloud/docs/manage-webhooks/#Secure-webhooks
    return same_secret(headers.get('X-Hub-Signature'), 'sha256=' + hmac_sha256(secret, body))

  @classmethod
  def repository_url(cls, payload):
    return (((payload.get('repository') or {}).get('links') or {}).get('html') or {}).get('href')

  @classmethod
  def parse_push(cls, payload, headers):
    # https://support.atlassian.com/bitbucket-cloud/docs/event-payloads/#Push
    if headers.get('X-Event-Key', 'repo:push') != 'repo:push':
      return None
    repo = payload['repository']
    links = repo.get('links') or {}
    # A push can update several branches, we look at the first one that wasn't deleted
    changes = payload['push']['changes']
    new = next((c['new'] for c in changes if c.get('new')), None)
    if new:
      ref = f"refs/{'tags' if new['type'] == 'tag' else 'heads'}/{new['name']}"
    else:
      old = changes[0]['old'] if changes else {}
      ref = f"refs/heads/{old.get('name', '')}"
    committers = []
    for change in changes:
      for commit in change.get('commits') or []:
        committers.append(bitbucket_user(commit.get('author') or {}))
    actor = payload.get('actor')
    if actor:
      committers.append(bitbucket_user({"user": actor}))
    return {
      "ref": ref,
      "checkout_sha": new['target']['hash'] if new else None,
      "project": {
        "path_with_namespace": repo['full_name'],
        "web_url": links['html']['href'],
        "name": repo['name'],
        "description": repo.get('description'),
        "avatar_url": (links.get('avatar') or {}).get('href'),
        "default_branch": (repo.get('mainbranch') or {}).get('name'),
        "namespace": repo['full_name'].split('/')[0],
      },
      "committers": committers,
    }


def bitbucket_user(author):
  """Commit authors are {raw: "Name <email>", user: {display_name, nickname, links}}"""
  match = re.match(r'\s*(.*?)\s*<([^>]*)>', author.get('raw') or '')
  user = author.get('user') or {}
  return {
    "name": match.group(1) if match else user.get('display_name'),
    "email": match.group(2) if match else None,
    "username": user.get('nickname'),
    "avatar_url": ((user.get('links') or {}).get('avatar') or {}).get('href'),
  }
