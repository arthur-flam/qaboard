"""
`qa project`: create projects on the server, before their first results, and archive them.
The server decides who may create what (projects.create, namespaces.create: backend/backend/projects.py); when it
refuses, it says whom to ask, and we print it. `qa init` uses `check` to say what the first `qa run` will do.
"""
import logging
from pathlib import Path
from typing import Any

import click

logger = logging.getLogger(__name__)


def current_project() -> str | None:
  from .config import project
  return Path(project).as_posix() if project else None


def current_repository() -> dict[str, Any] | None:
  """The repository of the current qaboard.yaml, and the project's folder in it"""
  from .config import root_qatools_config, subproject
  url = ((root_qatools_config or {}).get('project') or {}).get('url')
  if not isinstance(url, str) or not url:
    return None
  return {"url": url, "subdir": Path(subproject).as_posix() if subproject and str(subproject) != '.' else ''}


def server_url(server: str | None = None) -> str:
  from .credentials import default_server
  return (server or default_server()[0]).rstrip('/')


def check(project_id: str, server: str | None = None, token: str | None = None) -> dict[str, Any]:
  """What the server says about creating the project: {allowed, exists, reason, error, hint, suggestion...}"""
  from .results import _request
  return _request(server_url(server), 'projects/check', {"project": project_id}, token)


def describe_check(answer: dict[str, Any], project_id: str, server: str) -> tuple[str, str | None, str]:
  """(sentence, hint, color) for people"""
  if answer.get('exists'):
    return f"{project_id} exists on {server}.", None, 'green'
  if answer.get('allowed'):
    namespace = answer.get('namespace')
    claims = f", and claims the namespace {namespace} for you" if answer.get('new_namespace') and answer.get('namespace_kind') != 'user' else ''
    return (f"{project_id} doesn't exist on {server} yet: your first `qa run` creates it, with you as its owner{claims}.",
            f"Or create it now: `qa project create {project_id}`.", 'green')
  return answer.get('error') or f"You can't create {project_id} on {server}.", answer.get('hint'), 'yellow'


def explain_failure(e, server: str) -> str:
  from .http_client import ResultsError
  if isinstance(e, ResultsError):
    if e.status is None:
      from .credentials import describe_network_error
      return f"Could not reach QA-Board at {server}: {describe_network_error(e.__cause__) if isinstance(e.__cause__, Exception) else e}"
    body = e.body if isinstance(e.body, dict) else {}
    if body.get('error'):
      return str(body['error'])
  return str(e)


@click.group(name='project')
def project():
  """Create projects on QA-Board before their first results, and archive them."""


@project.command(name='create')
@click.argument('project_id', required=False, metavar='[PROJECT]')
@click.option('--display-name', default=None, help="A name for people, e.g. \"Image pipeline\".")
@click.option('--visibility', type=click.Choice(['public', 'internal', 'private']), default=None, help="Who sees it. Default: the server's default visibility.")
@click.option('--repository', default=None, help="The project's git repository (clone or web URL). Default: project.url in qaboard.yaml.")
@click.option('--server', default=None, help="The QA-Board server. Default: the one qa sends results to.")
@click.option('--json', 'as_json', is_flag=True, help="Print the created project as JSON.")
def create(project_id, display_name, visibility, repository, server, as_json):
  """
  Creates PROJECT (default: the project of the current qaboard.yaml), with you as its owner.

  \b
  Examples:
    qa project create                    # the project of ./qaboard.yaml
    qa project create camera-algo/isp --visibility private
  """
  from .results import _request
  from .http_client import ResultsError
  from .ui import fail
  project_id = project_id or current_project()
  if not project_id:
    fail("Which project? Give its id, e.g. `qa project create camera-algo/isp`, or run from the folder of its qaboard.yaml.")
  server = server_url(server)
  body: dict[str, Any] = {"project": project_id}
  if display_name:
    body["display_name"] = display_name
  if visibility:
    body["visibility"] = visibility
  repo = {"url": repository} if repository else (current_repository() if project_id == current_project() else None)
  if repo:
    body["repository"] = repo
  try:
    created = _request(server, 'projects', method='POST', json=body)
  except ResultsError as e:
    answer = e.body if isinstance(e.body, dict) else {}
    if e.reason == 'exists':
      click.secho(f"{project_id} already exists on {server}.", fg='green', err=True)
      return
    hint = answer.get('hint')
    if e.reason in ('sign_in_required', 'login_required'):
      hint = hint or f"Sign in: `qa auth login --server {server}`."
    fail(explain_failure(e, server), hint=hint)
  if as_json:
    import json
    click.echo(json.dumps(created, indent=2))
    return
  click.secho(f"Created {created.get('id', project_id)} on {server}, with you as its owner: {server}/{created.get('id', project_id)}", fg='green', err=True)


@project.command(name='check')
@click.argument('project_id', required=False, metavar='[PROJECT]')
@click.option('--server', default=None, help="The QA-Board server. Default: the one qa sends results to.")
def check_command(project_id, server):
  """Says whether PROJECT exists, else whether you can create it, and whom to ask if you can't."""
  from .http_client import ResultsError
  from .ui import fail
  project_id = project_id or current_project()
  if not project_id:
    fail("Which project? Give its id, e.g. `qa project check camera-algo/isp`, or run from the folder of its qaboard.yaml.")
  server = server_url(server)
  try:
    answer = check(project_id, server)
  except ResultsError as e:
    fail(explain_failure(e, server))
  sentence, hint, color = describe_check(answer, project_id, server)
  click.secho(sentence, fg=color, err=True)
  if hint:
    click.secho(hint, dim=True, err=True)
  if not answer.get('exists') and not answer.get('allowed'):
    raise SystemExit(1)


def set_archived(project_id, server, archived: bool):
  from .results import _request
  from .http_client import ResultsError
  from .ui import fail
  project_id = project_id or current_project()
  if not project_id:
    fail("Which project? Give its id, or run from the folder of its qaboard.yaml.")
  server = server_url(server)
  try:
    _request(server, 'project/archive', method='POST', json={"project": project_id, "archived": archived})
  except ResultsError as e:
    fail(explain_failure(e, server))
  click.secho(f"{'Archived' if archived else 'Unarchived'} {project_id}" + (": it's hidden from lists and refuses new results." if archived else "."), fg='green', err=True)


@project.command(name='archive')
@click.argument('project_id', required=False, metavar='[PROJECT]')
@click.option('--server', default=None, help="The QA-Board server. Default: the one qa sends results to.")
def archive(project_id, server):
  """Hides PROJECT from lists, and refuses its new results (owners). `qa project unarchive` undoes it."""
  set_archived(project_id, server, True)


@project.command(name='unarchive')
@click.argument('project_id', required=False, metavar='[PROJECT]')
@click.option('--server', default=None, help="The QA-Board server. Default: the one qa sends results to.")
def unarchive(project_id, server):
  """Shows PROJECT again, and takes its results (owners)."""
  set_archived(project_id, server, False)


def init_hint(project_id: str) -> None:
  """
  For `qa init`: when signed in to a server, one line on what the first `qa run` will do with the project. Quiet
  when the server is unreachable or doesn't know this endpoint (older servers): init never fails for it.
  """
  from .credentials import default_server, resolve_token
  server, source = default_server()
  if source is None:
    return
  token, _ = resolve_token(server)
  if not token:
    return
  try:
    from .http_client import request_json
    answer = request_json(server.rstrip('/'), 'projects/check', params={"project": project_id}, timeout=(3.0, 5.0))
  except Exception as e:
    logger.debug("Could not check %s on %s: %s", project_id, server, e)
    return
  sentence, hint, color = describe_check(answer, project_id, server.rstrip('/'))
  click.secho(sentence, fg=color)
  if hint:
    click.secho(hint, dim=True)
