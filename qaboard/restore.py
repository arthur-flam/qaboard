"""
`qa restore <run>`: the code a local run ran, in a branch of this repository (docs/rfcs/local-changes.md).

The run recorded its commit and its local changes (qaboard/local_changes.py): `qa restore` makes a branch
qa-restore/<run id> at the commit, and applies the uncommitted changes in the working tree. When the commit was
never pushed, it starts from the commit the run's unpushed commits are based on, and commits them first.

    qa restore 1234
    qa restore "https://qaboard/my/project/commit/abc123?filter=id:1234"
    qa restore 1234 --download     # only saves the patches here

It is `wandb restore`. It never touches a working tree with changes.
"""
import re
import subprocess
import sys
import tempfile
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple
from urllib.parse import unquote, urlsplit

import click


def parse_run(run: str, server: Optional[str]) -> Tuple[int, Optional[str]]:
  """(run id, server) from a run id, or a web app URL that names one run (filter=id:<n>, or /output/<n>)"""
  if re.fullmatch(r'\d+', run.strip()):
    return int(run), server
  parts = urlsplit(run.strip())
  if not parts.scheme or not parts.netloc:
    raise click.UsageError(f"{run!r}: give a run's id, or the address of its page in the web app.")
  found = re.search(r'(?:^|[?&])filter=id(?::|%3A)(\d+)', parts.query) or re.search(r'/output/(\d+)', unquote(parts.path))
  if not found:
    raise click.UsageError(f"{run} doesn't name one run: open the run in the web app (its menu has \"Copy link\"), or give its id.")
  return int(found.group(1)), server or f"{parts.scheme}://{parts.netloc}"


def _git(args: List[str], cwd: Path, check: bool = True) -> subprocess.CompletedProcess:
  return subprocess.run(['git', *args], cwd=cwd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, encoding='utf-8', errors='replace', check=check)


def _has_commit(repo: Path, sha: Optional[str]) -> bool:
  return bool(sha) and _git(['cat-file', '-e', f'{sha}^{{commit}}'], repo, check=False).returncode == 0


def plan(changes: Dict[str, Any], has_commit) -> Tuple[Optional[str], bool, Optional[str]]:
  """
  (where to start, whether to apply the unpushed commits' patch, why we can't) for a run's local changes.
  `has_commit(sha)` says whether this repository has a commit.
  """
  head, base = changes.get('head'), changes.get('base')
  unpushed_saved = bool((changes.get('unpushed') or {}).get('path'))
  if head and has_commit(head):
    return head, False, None
  if base and unpushed_saved and has_commit(base):
    return base, True, None
  if not head:
    return None, False, "The run didn't record its commit."
  if base and unpushed_saved:
    return None, False, f"This repository has neither the run's commit {head[:10]} nor {base[:10]}: `git fetch`, then try again."
  return None, False, f"This repository doesn't have the run's commit {head[:10]}{', which was never pushed' if changes.get('pushed') is False else ''}: `git fetch`, then try again."


def binary_files(changes: Dict[str, Any]) -> List[str]:
  """Binary files are in the patch as "Binary files differ", without their content: git apply can't apply them"""
  return [f['path'] for f in changes.get('files') or [] if isinstance(f, dict) and f.get('binary') and isinstance(f.get('path'), str)]


@click.command()
@click.argument('run')
@click.option('--server', help="The QA-Board server, when the run's address doesn't say (e.g. served under a path prefix).")
@click.option('--branch', help="The branch to create. Default: qa-restore/<run id>.")
@click.option('--download', is_flag=True, help="Only save the run's patches in this folder: don't change the repository.")
def restore(run, server, branch, download):
  """
  Make a branch with the code a local run ran: its commit, and its uncommitted changes.

  \b
      qa restore 1234
      qa restore "https://qaboard/my/project/commit/abc123?filter=id:1234"

  The run must have saved its local changes (outputs.local_changes: patch, the default). Run it in a clone of the
  run's repository, without uncommitted changes.
  """
  from .credentials import default_server
  from .http_client import ResultsError
  from .results import _request, _token_for
  from .run_files import fetch_file
  run_id, server = parse_run(run, server)
  server = (server or default_server()[0]).rstrip('/')
  token = _token_for(server, None)
  try:
    output = _request(server, f"output/{run_id}", token=token)
  except ResultsError as e:
    click.secho(f"ERROR: can't get run {run_id} from {server}: {e}", fg='red', bold=True, err=True)
    sys.exit(1)
  changes = (output.get('data') or {}).get('local_changes')
  if not isinstance(changes, dict):
    ci = (output.get('data') or {}).get('ci')
    why = "it ran in CI, on its commit" if ci else "it ran before QA-Board recorded local changes, or with outputs.local_changes: off"
    click.secho(f"Run {run_id} didn't record its local changes: {why}.", fg='yellow', err=True)
    sys.exit(1)

  saved = {key: (changes.get(key) or {}).get('path') for key in ('patch', 'unpushed')}
  if changes.get('dirty') and not saved['patch']:
    omitted = (changes.get('patch') or {}).get('omitted')
    why = {'too_large': "it was too large", 'level': "the server keeps only a summary of runs' local changes", 'timeout': "git took too long"}.get(omitted, "it couldn't be saved")
    click.secho(f"Run {run_id} changed {changes.get('files_total', '?')} files but didn't save its patch: {why}.", fg='red', bold=True, err=True)
    sys.exit(1)

  with tempfile.TemporaryDirectory(prefix='qa-restore-') as tmp:
    patches: Dict[str, Path] = {}
    for key, name in saved.items():
      if not name:
        continue
      target = Path(tmp) / Path(name).name
      try:
        if not fetch_file(server, output, name, target, token=token):
          click.secho(f"ERROR: the server doesn't have {name} of run {run_id}: its files were deleted?", fg='red', bold=True, err=True)
          sys.exit(1)
      except (ResultsError, ValueError) as e:
        click.secho(f"ERROR: can't download {name}: {e}", fg='red', bold=True, err=True)
        sys.exit(1)
      patches[key] = target

    if download:
      for key, path in patches.items():
        destination = Path(f"run-{run_id}-{path.name}")
        destination.write_bytes(path.read_bytes())
        click.echo(f"Saved {destination}")
      head = changes.get('head') or '<commit>'
      if 'unpushed' in patches:
        click.echo(f"To apply them: git checkout {(changes.get('base') or '<base>')[:10]} && git apply run-{run_id}-unpushed.patch && git apply run-{run_id}-local-changes.patch")
      elif patches:
        click.echo(f"To apply it: git checkout {head[:10]} && git apply run-{run_id}-local-changes.patch")
      else:
        click.echo(f"Run {run_id} had no local changes: it ran {head[:10]}.")
      return

    found = _git(['rev-parse', '--show-toplevel'], Path.cwd(), check=False)
    if found.returncode != 0:
      click.secho("ERROR: run qa restore in a clone of the run's repository (or use --download).", fg='red', bold=True, err=True)
      sys.exit(1)
    repo = Path(found.stdout.strip())
    status = _git(['status', '--porcelain', '--untracked-files=no'], repo).stdout
    if status.strip():
      click.secho("ERROR: this working tree has uncommitted changes: commit or stash them first (git stash -u).", fg='red', bold=True, err=True)
      sys.exit(1)

    start, apply_unpushed, problem = plan(changes, lambda sha: _has_commit(repo, sha))
    if problem or start is None:
      click.secho(f"ERROR: {problem}", fg='red', bold=True, err=True)
      sys.exit(1)
    branch = branch or f"qa-restore/{run_id}"
    created = _git(['switch', '-q', '-c', branch, start], repo, check=False)
    if created.returncode != 0:
      click.secho(f"ERROR: can't create the branch {branch}: {created.stderr.strip()}\nGive another name with --branch.", fg='red', bold=True, err=True)
      sys.exit(1)

    excluded = [f'--exclude={path}' for path in binary_files(changes)]
    if apply_unpushed:
      applied = _git(['apply', '--index', *excluded, str(patches['unpushed'])], repo, check=False)
      if applied.returncode != 0:
        click.secho(f"ERROR: the run's unpushed commits don't apply on {start[:10]}: {applied.stderr.strip()}", fg='red', bold=True, err=True)
        sys.exit(1)
      _git(['commit', '-q', '--no-verify', '-m', f"The unpushed commits of run {run_id}, up to {changes.get('head', '')[:10]}"], repo)
    if 'patch' in patches:
      applied = _git(['apply', *excluded, str(patches['patch'])], repo, check=False)
      if applied.returncode != 0:
        click.secho(f"ERROR: the run's local changes don't apply: {applied.stderr.strip()}", fg='red', bold=True, err=True)
        click.secho(f"The branch {branch} is at {start[:10]}{' with the unpushed commits' if apply_unpushed else ''}.", fg='yellow', err=True)
        sys.exit(1)

  click.secho(f"On the branch {branch}: the code of run {run_id}", fg='green', bold=True)
  details = [f"commit {(changes.get('head') or start)[:10]}"]
  if apply_unpushed:
    details.append(f"{changes.get('unpushed_commits') or 'its'} unpushed commit(s), committed as one")
  if 'patch' in patches:
    details.append(f"{changes.get('files_total', '?')} changed files, not committed (git diff shows them)")
  click.echo("  " + ", ".join(details))
  skipped = binary_files(changes)
  if skipped:
    click.secho(f"  Binary files aren't saved with runs, they are as in the commit: {', '.join(skipped[:5])}{'...' if len(skipped) > 5 else ''}", fg='yellow')
  if changes.get('excluded'):
    click.secho(f"  Left out when the run started (they look like secrets, or outputs.local_changes.exclude): {', '.join(changes['excluded'][:5])}", fg='yellow')
  if changes.get('untracked_contents') == 'too_many':
    click.secho("  The run had too many untracked files to save their contents: they are missing.", fg='yellow')
  inputs = output.get('test_input_path')
  if inputs:
    configurations = output.get('configurations') or []
    click.echo(f"  It ran on {inputs}" + (f", with {configurations}" if configurations else '') + (f" and {output['extra_parameters']}" if output.get('extra_parameters') else ''))
