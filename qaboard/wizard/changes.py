"""
The wizard never writes files as it goes: every change is staged in a ChangeSet, shown as a diff,
and applied only once the user agrees. Applying is atomic per file, and rolled back if anything fails.

The same rules protect the user's repository whether the change comes from a template or from an AI agent:
- paths stay inside the project, outside of .git, and don't follow symlinks out of it,
- only qaboard.yaml and files under qa/ can be written,
- files that look like they hold secrets are never read.
"""
import os
import re
import difflib
import fnmatch
import tempfile
from pathlib import Path, PurePath, PurePosixPath, PureWindowsPath
from dataclasses import dataclass, field
from typing import Dict, List, Optional


# What the wizard may create or change, relative to the project's root
WRITABLE_FILES = ('qaboard.yaml',)
WRITABLE_DIRS = ('qa',)

# Never read, never sent to an LLM. Matched against each file name, case-insensitively.
SECRET_PATTERNS = (
  '.env', '.env.*', '*.env', '.envrc', '.netrc', '.npmrc', '.pypirc', '.git-credentials', '*.htpasswd',
  '*.pem', '*.key', '*.p12', '*.pfx', '*.jks', '*.keystore', '*.kdbx', '*.gpg', '*.asc',
  'id_rsa*', 'id_dsa*', 'id_ecdsa*', 'id_ed25519*',
  'secret', 'secrets', 'secret.*', 'secrets.*', '.secrets*', '*.secret', '*.secrets', '*_secret*', '*-secret*', '*_secrets*', '*-secrets*',
  'credentials', 'credentials.*', '*credentials.json', '*.credentials', 'service-account*.json',
  'token', 'token.*', '*.token', '*_token.txt', '*_token.json', 'passwords*',
  'kubeconfig', '*.tfstate', '*.tfvars', '.pgpass', '*.ppk', '.s3cfg', '.boto', 'pip.conf', '.yarnrc.yml', '.dockercfg',
)
# Folders whose files are never read
SECRET_DIRS = ('.ssh', '.aws', '.kube', '.docker', '.gnupg', '.azure', '.gcloud', 'secret', 'secrets', '.secrets', 'credentials', '.git')
# Templates that document which variables are needed are fine to read
SECRET_EXCEPTIONS = ('.env.example', '.env.sample', '.env.template', '*.example', '*.sample', '*.template')

MAX_FILE_BYTES = 256 * 1024

# Terminal escapes could hide lines when the user reviews diffs, bidi controls could make code read differently
CONTROL_CHARS = re.compile('[\x00-\x08\x0b\x0c\x0e-\x1f\x7f\u202a-\u202e\u2066-\u2069]')


def visible(text: str) -> str:
  """Text safe to print in a terminal: control characters are shown as escapes."""
  return CONTROL_CHARS.sub(lambda m: repr(m.group(0))[1:-1], text.replace('\r\n', '\n'))


class UnsafePath(ValueError):
  """A path the wizard refuses to read or write. The message is meant for the user, or the agent."""


def is_secret(rel: PurePath) -> bool:
  """Whether a path, relative to the project's root, looks like it holds secrets."""
  if any(part.lower() in SECRET_DIRS for part in rel.parts[:-1]):
    return True
  name = rel.name.lower()
  if any(fnmatch.fnmatch(name, p) for p in SECRET_EXCEPTIONS):
    return False
  return any(fnmatch.fnmatch(name, p) for p in SECRET_PATTERNS)


def is_writable(rel: PurePosixPath) -> bool:
  return rel.as_posix() in WRITABLE_FILES or (len(rel.parts) > 1 and rel.parts[0] in WRITABLE_DIRS)


def safe_path(root: Path, path: str, for_write: bool = False) -> Path:
  """Resolves a path given relative to the project's root, or raises UnsafePath."""
  if not path or '\0' in path:
    raise UnsafePath("Empty or invalid path.")
  if PurePosixPath(path).is_absolute() or PureWindowsPath(path).is_absolute() or PureWindowsPath(path).drive:
    raise UnsafePath(f"{path}: use paths relative to the project's root.")
  root = root.resolve()
  resolved = (root / path).resolve()
  try:
    rel = PurePosixPath(resolved.relative_to(root).as_posix())
  except ValueError:
    raise UnsafePath(f"{path}: outside of the project.") from None
  # .GIT is .git on case-insensitive filesystems
  if '.git' in (part.lower() for part in rel.parts):
    raise UnsafePath(f"{path}: the wizard doesn't touch .git/.")
  if is_secret(rel):
    raise UnsafePath(f"{path}: looks like it holds secrets, the wizard doesn't read or write it.")
  if for_write and not is_writable(rel):
    allowed = ', '.join([*WRITABLE_FILES, *(f'{d}/' for d in WRITABLE_DIRS)])
    raise UnsafePath(f"{path}: the wizard only writes {allowed}.")
  return resolved


def read_text(path: Path) -> Optional[str]:
  """A text file's content, None if it doesn't exist. Raises UnsafePath for huge or binary files."""
  if not path.is_file():
    return None
  if path.stat().st_size > MAX_FILE_BYTES:
    raise UnsafePath(f"{path.name} is too big ({path.stat().st_size // 1024} kB).")
  data = path.read_bytes()
  if b'\0' in data[:8192]:
    raise UnsafePath(f"{path.name} is a binary file.")
  return data.decode('utf-8', errors='replace')


@dataclass
class Change:
  path: Path                # absolute
  rel: str                  # relative to the root, posix
  content: str              # what we want to write
  original: Optional[str]   # what was on disk when staged, None for new files


@dataclass
class ChangeSet:
  root: Path
  changes: Dict[str, Change] = field(default_factory=dict)

  def __post_init__(self):
    self.root = self.root.resolve()

  def stage(self, path: str, content: str) -> Change:
    resolved = safe_path(self.root, path, for_write=True)
    rel = resolved.relative_to(self.root).as_posix()
    if resolved.is_dir():
      raise UnsafePath(f"{rel} is a directory.")
    if not content.endswith('\n'):
      content += '\n'
    original = self.changes[rel].original if rel in self.changes else read_text(resolved)
    self.changes[rel] = Change(resolved, rel, content, original)
    return self.changes[rel]

  def discard(self, rel: str):
    self.changes.pop(rel, None)

  def read(self, path: str) -> Optional[str]:
    """What a file will contain once the changes are applied."""
    resolved = safe_path(self.root, path)
    rel = resolved.relative_to(self.root).as_posix()
    if rel in self.changes:
      return self.changes[rel].content
    return read_text(resolved)

  def pending(self) -> List[Change]:
    """Changes that actually change something, in a stable order."""
    return [c for _, c in sorted(self.changes.items()) if c.content != c.original]

  def diff(self, change: Change) -> str:
    return diff_text(change.rel, change.original, change.content)

  def stats(self, change: Change):
    added = removed = 0
    for line in self.diff(change).splitlines():
      if line.startswith('+') and not line.startswith('+++'):
        added += 1
      elif line.startswith('-') and not line.startswith('---'):
        removed += 1
    return added, removed

  def apply(self) -> List[Change]:
    """
    Writes all pending changes, or none: if a write fails, files already written are restored.
    Refuses to overwrite a file that changed on disk since the change was staged.
    """
    pending = self.pending()
    for change in pending:
      # The path could have become a symlink pointing elsewhere since it was staged
      safe_path(self.root, change.rel, for_write=True)
      if read_text(change.path) != change.original:
        raise RuntimeError(f"{change.rel} changed while the wizard was running, nothing was written. Run `qa init` again.")
    done: List[Change] = []
    created_dirs: List[Path] = []
    try:
      for change in pending:
        missing = [p for p in [change.path.parent, *change.path.parent.parents] if not p.exists()]
        change.path.parent.mkdir(parents=True, exist_ok=True)
        created_dirs.extend(reversed(missing))
        atomic_write(change.path, change.content)
        done.append(change)
    except BaseException:
      for change in reversed(done):
        if change.original is None:
          change.path.unlink(missing_ok=True)
        else:
          atomic_write(change.path, change.original)
      for d in reversed(created_dirs):
        try:
          d.rmdir()
        except OSError:
          pass
      raise
    return done


def diff_text(rel: str, before: Optional[str], after: str) -> str:
  fromfile = f'a/{rel}' if before is not None else '/dev/null'
  return ''.join(difflib.unified_diff((before or '').splitlines(keepends=True), after.splitlines(keepends=True), fromfile=fromfile, tofile=f'b/{rel}'))


def atomic_write(path: Path, content: str):
  mode = path.stat().st_mode & 0o777 if path.exists() else None
  fd, tmp = tempfile.mkstemp(dir=path.parent, prefix=f'.{path.name}.', suffix='.tmp')
  try:
    with os.fdopen(fd, 'w', encoding='utf-8', newline='') as f:
      f.write(content)
    if mode is not None:
      os.chmod(tmp, mode)
    else:
      # mkstemp creates files readable only by us, use the usual permissions instead
      umask = os.umask(0)
      os.umask(umask)
      os.chmod(tmp, 0o666 & ~umask)
    os.replace(tmp, path)
  except BaseException:
    Path(tmp).unlink(missing_ok=True)
    raise
