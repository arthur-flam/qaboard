import re
from pathlib import Path

from git import Repo
from git import RemoteProgress
from git.exc import NoSuchPathError, InvalidGitRepositoryError

from .fs_utils import rmtree


# Repository paths and URLs come from unauthenticated webhooks and API calls.
# Paths must stay under the clone directory: no absolute paths, no "." or ".." segments
safe_project_path = re.compile(r"[A-Za-z0-9_][A-Za-z0-9_.-]*(/[A-Za-z0-9_][A-Za-z0-9_.-]*)*")
def check_project_path(project_path):
  if not safe_project_path.fullmatch(str(project_path)):
    raise ValueError(f"Invalid repository path: {project_path!r}")


class Repos():
  """Local clones of the repositories, at $QABOARD_DATA_GIT_DIR/<project_path>"""

  def __init__(self, git_hosts, clone_directory):
    self.git_hosts = git_hosts # see git_hosts/__init__.py
    self.clone_directory = Path(clone_directory)

  def __getitem__(self, project_path):
    return self.get(project_path)

  def get(self, project_path, git=None, project_url=None):
    """
    Return a git-python Repo object representing a clone of the repository.

    project_path: the full git repository namespace, eg group/repo
    git: what we know about the repository: Project.data['git'], e.g. {"hosting_type": "github", "web_url": ...}
         It tells us which host to clone it from. By default: the default host (GITLAB_HOST).
    project_url: from qaboard.yaml, used if `git` doesn't tell us the host.
    """
    check_project_path(project_path)
    clone_location = self.clone_directory / project_path
    # Tokens are only sent to the host they belong to: we never clone from a URL we're given
    try:
      host = self.git_hosts.for_repo(git, project_url)
    except ValueError:
      host = None # we can still read existing clones
    try:
      repo = Repo(str(clone_location))
      if host:
        repo.git.update_environment(**host.git_env()) # to fetch
      return repo
    except InvalidGitRepositoryError:
      rmtree(clone_location) # likely a failed clone, we try again
    except NoSuchPathError:
      pass
    if not host:
      host = self.git_hosts.for_repo(git, project_url) # raises why
    print(f'Cloning <{project_path}> from {host} to {self.clone_directory}')
    try:
      # https://gitpython.readthedocs.io/en/stable/reference.html#git.repo.base.Repo.clone_from
      repo = Repo.clone_from(host.clone_url(project_path), str(clone_location), env=host.git_env())
    except Exception as e:
      print(f'[ERROR] Could not clone {project_path} from {host}. Please set $QABOARD_DATA_DIR to a writable location and verify your network settings')
      raise RuntimeError(host.redact(str(e))) from None
    repo.git.update_environment(**host.git_env())
    return repo


def git_pull(repo):
  """Updates the repo and warms the cache listing the latests commits.."""
  class MyProgressPrinter(RemoteProgress):
    def update(self, op_code, cur_count, max_count=100.0, message="[No message]"):
      # print('...')
      # print(op_code, cur_count, max_count, (cur_count or 0)/max_count, message)
      pass
  try:
    for fetch_info in repo.remotes.origin.fetch(progress=MyProgressPrinter()):
      # print(f"Updated {fetch_info.ref} to {fetch_info.commit}")
      pass
  except Exception as e:
    print(e)

def find_branch(commit_hash, repo):
  """Tries to get from which branch a commit comes from. It's a *guess*."""
  std_out = repo.git.branch(contains=commit_hash, remotes=True)
  branches = [l.split(' ')[-1] for l in std_out.splitlines()]
  important_branches = ['origin/release', 'origin/master', 'origin/develop']
  for b in important_branches:
    if b in branches:
      return b
  if branches:
    return branches[0]
  return 'unknown'