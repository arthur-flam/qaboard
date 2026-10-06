"""
What the wizard can find out about a project by itself, without network access or questions.
"""
import os
import re
import subprocess
import time
from collections import Counter
from dataclasses import dataclass, field
from pathlib import Path
from typing import Dict, List, Optional, Tuple


# Folders that never hold the code we wrap, or that are huge
SKIPPED_DIRS = {
  '.git', '.hg', '.svn', 'node_modules', '__pycache__', '.venv', 'venv', '.tox', '.nox', '.mypy_cache',
  '.pytest_cache', '.ruff_cache', '.idea', '.vscode', 'build', 'dist', 'out', 'output', 'outputs',
  'site-packages', '.eggs', 'target', 'cmake-build-debug', 'cmake-build-release',
}

LANGUAGES = {
  '.py': 'Python', '.ipynb': 'Python',
  '.c': 'C', '.h': 'C/C++',
  '.cpp': 'C++', '.cc': 'C++', '.cxx': 'C++', '.hpp': 'C++', '.cu': 'CUDA',
  '.m': 'MATLAB', '.mlx': 'MATLAB',
  '.rs': 'Rust', '.go': 'Go', '.java': 'Java', '.kt': 'Kotlin', '.scala': 'Scala',
  '.js': 'JavaScript', '.ts': 'TypeScript', '.jl': 'Julia', '.r': 'R', '.cs': 'C#',
  '.sh': 'Shell', '.ps1': 'PowerShell',
}

# Files that tell how the project is built or run
BUILD_FILES = {
  'pyproject.toml': 'pyproject.toml', 'setup.py': 'setup.py', 'requirements.txt': 'requirements.txt',
  'environment.yml': 'conda', 'uv.lock': 'uv', 'poetry.lock': 'Poetry', 'Pipfile': 'Pipenv',
  'CMakeLists.txt': 'CMake', 'Makefile': 'Make', 'meson.build': 'Meson', 'BUILD.bazel': 'Bazel', 'WORKSPACE': 'Bazel',
  'conanfile.txt': 'Conan', 'conanfile.py': 'Conan', 'vcpkg.json': 'vcpkg',
  'Cargo.toml': 'Cargo', 'go.mod': 'Go modules', 'package.json': 'npm', 'pom.xml': 'Maven', 'build.gradle': 'Gradle',
  'Dockerfile': 'Docker', 'docker-compose.yml': 'Docker Compose',
}

CI_FILES = {
  '.gitlab-ci.yml': 'GitLab CI', 'Jenkinsfile': 'Jenkins', '.github/workflows': 'GitHub Actions',
  'azure-pipelines.yml': 'Azure Pipelines', '.circleci': 'CircleCI', 'bitbucket-pipelines.yml': 'Bitbucket Pipelines',
}

MAX_FILES = 20_000


@dataclass
class ProjectFacts:
  root: Path
  is_git: bool = False
  remote: Optional[str] = None
  remote_url: Optional[str] = None
  project_name: Optional[str] = None
  reference_branch: str = 'master'
  dirty_files: List[str] = field(default_factory=list)
  languages: List[str] = field(default_factory=list)
  build_systems: List[str] = field(default_factory=list)
  ci: List[str] = field(default_factory=list)
  files: List[str] = field(default_factory=list)   # relative posix paths, capped
  has_qa_dir: bool = False


def git(root: Path, *args: str) -> Optional[str]:
  """Output of a git command, None if it fails. Never prompts for credentials, never uses the network."""
  env = {**os.environ, 'GIT_TERMINAL_PROMPT': '0', 'GIT_ASKPASS': 'echo', 'SSH_ASKPASS': 'echo'}
  try:
    out = subprocess.run(['git', *args], cwd=root, env=env, capture_output=True, encoding='utf-8', errors='replace', timeout=10, check=True)
  except (OSError, subprocess.SubprocessError):
    return None
  return out.stdout.strip()


def project_name_from_url(url: str) -> Optional[str]:
  """
  group/project from a git remote URL:
  git@server:group/project.git, ssh://git@server:2222/group/project, https://server/group/sub/project.git
  """
  url = url.strip().rstrip('/')
  match = re.match(r'^(?:[a-z][a-z0-9+.-]*://)?(?:[^@/]+@)?[^/:]+(?::\d+)?[:/](.+)$', url, re.IGNORECASE)
  if not match:
    return None
  path = match.group(1).lstrip('/')
  if path.endswith('.git'):
    path = path[:-4]
  return path or None


def reference_branch(root: Path, remote: Optional[str]) -> str:
  """The default branch, without network access: from the remote's HEAD if we know it, else main or master."""
  if remote:
    head = git(root, 'symbolic-ref', '--short', f'refs/remotes/{remote}/HEAD')
    if head and '/' in head:
      return head.split('/', 1)[1]
  branches = set((git(root, 'for-each-ref', '--format=%(refname:short)', 'refs/heads', f'refs/remotes/{remote or "origin"}') or '').split())
  for name in ('main', 'master', 'develop'):
    if name in branches or f'{remote or "origin"}/{name}' in branches:
      return name
  current = git(root, 'branch', '--show-current')
  return current or 'master'


def list_files(root: Path, is_git: bool) -> List[str]:
  """Project files, respecting .gitignore when we can."""
  if is_git:
    out = git(root, 'ls-files', '--cached', '--others', '--exclude-standard')
    if out is not None:
      files = [f for f in out.splitlines() if f and not set(Path(f).parts) & SKIPPED_DIRS]
      return files[:MAX_FILES]
  files = []
  for dirpath, dirnames, filenames in os.walk(root):
    dirnames[:] = sorted(d for d in dirnames if d not in SKIPPED_DIRS and not d.startswith('.'))
    rel = Path(dirpath).relative_to(root)
    files.extend((rel / f).as_posix() for f in sorted(filenames))
    if len(files) >= MAX_FILES:
      break
  return files[:MAX_FILES]


def detect(root: Path) -> ProjectFacts:
  root = root.resolve()
  facts = ProjectFacts(root=root)
  facts.is_git = git(root, 'rev-parse', '--is-inside-work-tree') == 'true'
  if facts.is_git:
    remotes = (git(root, 'remote') or '').split()
    facts.remote = 'origin' if 'origin' in remotes else (remotes[0] if remotes else None)
    if facts.remote:
      facts.remote_url = git(root, 'remote', 'get-url', facts.remote)
      if facts.remote_url:
        facts.project_name = project_name_from_url(facts.remote_url)
    facts.reference_branch = reference_branch(root, facts.remote)
    status = git(root, 'status', '--porcelain')
    facts.dirty_files = [line[3:] for line in (status or '').splitlines() if line.strip()]

  facts.files = list_files(root, facts.is_git)
  counts = Counter(LANGUAGES[Path(f).suffix.lower()] for f in facts.files if Path(f).suffix.lower() in LANGUAGES)
  # headers belong to C++ or C, whichever there is
  headers = counts.pop('C/C++', 0)
  if headers:
    counts['C++' if counts['C++'] or not counts['C'] else 'C'] += headers
  facts.languages = [language for language, _ in counts.most_common(4)]
  names = {Path(f).name for f in facts.files if '/' not in f}
  facts.build_systems = sorted({label for name, label in BUILD_FILES.items() if name in names})
  facts.ci = [label for path, label in CI_FILES.items() if (root / path).exists()]
  facts.has_qa_dir = (root / 'qa').exists()
  return facts


def guess_inputs(database: Path, limit: int = 3000, seconds: float = 3) -> Tuple[Optional[str], List[str]]:
  """
  Looks at the files in a folder of test inputs: returns a glob matching the most common kind of file,
  and a few example inputs relative to the folder. Stops early on huge (network) folders.
  """
  counts: Dict[str, List[str]] = {}
  seen = 0
  deadline = time.monotonic() + seconds
  for dirpath, dirnames, filenames in os.walk(database):
    if time.monotonic() > deadline:
      break
    dirnames[:] = sorted(d for d in dirnames if not d.startswith('.'))
    for name in sorted(filenames):
      suffix = Path(name).suffix.lower()
      if not suffix or name.startswith('.') or suffix in ('.txt', '.md', '.json', '.yaml', '.yml', '.csv', '.log'):
        continue
      counts.setdefault(suffix, []).append((Path(dirpath) / name).relative_to(database).as_posix())
      seen += 1
    if seen >= limit:
      break
  if not counts:
    return None, []
  suffix, examples = max(counts.items(), key=lambda kv: len(kv[1]))
  return f'*{suffix}', examples[:3]
