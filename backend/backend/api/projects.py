"""
Creating and archiving projects (backend/projects.py; docs/design/user-management.md, Appendix G).
The web app's New project dialog and `qa project create` call these. The first results `qa` sends also create
projects, with the same policy (api/auth.py ingest_access).
"""
from flask import request, jsonify, g

from backend import app, db_session
from .. import projects
from ..access import authorize, denied, owners
from ..access.grants import AccessError, clean_path
from ..access.policy import VISIBILITIES, ROLES
from ..actor import error
from ..tokens import iso
from .auth import authenticated, login_required


def serialize(project) -> dict:
  from ..models import Namespace, ProjectRepo, User
  namespace = db_session.get(Namespace, project.namespace_id) if project.namespace_id else None
  created_by = db_session.get(User, project.created_by_id) if project.created_by_id else None
  archived_by = db_session.get(User, project.archived_by_id) if project.archived_by_id else None
  repos = ProjectRepo.query.filter(ProjectRepo.project_id == project.id).order_by(ProjectRepo.id).all()
  return {
    "id": project.id,
    "display_name": (project.data or {}).get('display_name'),
    "namespace": {"path": namespace.path, "kind": namespace.kind, "legacy": namespace.legacy} if namespace else None,
    "created_at": iso(project.created_at),
    "created_by": created_by.user_name if created_by else None,
    "created_via": project.created_via,
    "archived": project.archived_at is not None,
    "archived_at": iso(project.archived_at),
    "archived_by": archived_by.user_name if archived_by else None,
    "repositories": [{"git_host": r.git_host or None, "repo_path": r.repo_path, "subdir": r.subdir} for r in repos],
    "owners": owners(project.id),
  }


def signed_in_with(*scopes):
  """None if a person (or a token with one of these scopes) is calling, else the response"""
  actor = g.actor
  if actor.user is None:
    return error(401, 'sign_in_required', "Sign in to create projects: in the web app, or `qa auth login`.")
  if not any(actor.has_scope(scope) for scope in scopes):
    return error(403, 'missing_scope', f"This token can't create projects: it needs the \"{scopes[0]}\" scope.", required=scopes[0])
  return None


def parse_repository(value):
  """{url} or {git_host, repo_path, subdir}, or a URL"""
  if value in (None, '', {}):
    return None
  if isinstance(value, str):
    value = {"url": value}
  if not isinstance(value, dict):
    raise AccessError("repository is {url} (a clone or web URL), or {git_host, repo_path, subdir}.")
  subdir = value.get('subdir') or ''
  if value.get('url'):
    repo = projects.repo_from_url(value['url'])
    if repo is None:
      raise AccessError("repository.url isn't a repository's URL, e.g. https://gitlab.example.com/group/repo or git@github.com:org/repo.git")
  elif isinstance(value.get('repo_path'), str) and '/' in value['repo_path'].strip('/'):
    repo = {"git_host": str(value.get('git_host') or '').lower(), "repo_path": value['repo_path'].strip('/').removesuffix('.git')}
  else:
    raise AccessError("repository is {url} (a clone or web URL), or {git_host, repo_path, subdir}.")
  if not isinstance(subdir, str) or '..' in subdir.split('/'):
    raise AccessError("repository.subdir is the project's folder in the repository, e.g. libs/isp.")
  repo['subdir'] = subdir.strip('/')
  return repo


@app.route('/api/v1/projects/check', methods=['GET'])
@app.route('/api/v1/projects/check/', methods=['GET'])
def check_new_project():
  """?project=ns/name: whether the caller may create it, else why and what to do (the New project dialog, qa init)"""
  project_id = (request.args.get('project') or '').strip()
  if not project_id:
    return error(400, 'invalid_project', "Which project? Send ?project=namespace/name.")
  check = projects.check_create(g.actor, project_id)
  body = check.body()
  body["exists"] = check.reason == 'exists'
  if body["exists"]:
    # nothing about projects they can't see
    read = authorize(g.actor, 'project.read', project_id)
    body["can_read"] = read.allowed
  body["personal_namespace"] = projects.personal_namespace(g.actor.user)
  return jsonify(body)


@app.route('/api/v1/projects', methods=['POST'])
@app.route('/api/v1/projects/', methods=['POST'])
@authenticated
def create_project():
  """
  {project: ns/name, display_name?, visibility?: public|internal|private, members?: [{subject: {type, id}, role}],
  repository?: {url} or {git_host, repo_path, subdir}} => 201 with the project. The creator becomes its owner.
  """
  if refused := signed_in_with('ingest', 'act'):
    return refused
  body = request.get_json(silent=True) or {}
  project_id = body.get('project') or body.get('id')
  if not isinstance(project_id, str) or not project_id.strip():
    return error(400, 'invalid_project', "Which project? Send `project`, e.g. namespace/name.")
  project_id = project_id.strip()
  visibility = body.get('visibility')
  if visibility not in (None, *VISIBILITIES):
    return error(400, 'invalid', f"visibility is one of {', '.join(VISIBILITIES)}, or null for the default.", field='visibility')
  display_name = body.get('display_name')
  if display_name is not None and (not isinstance(display_name, str) or len(display_name) > 200):
    return error(400, 'invalid', "display_name is a text of 200 characters at most.", field='display_name')
  members = body.get('members') or []
  if not isinstance(members, list) or not all(isinstance(m, dict) and m.get('role') in ROLES for m in members):
    return error(400, 'invalid', f"members is a list of {{subject: {{type: user or group, id}}, role: {' | '.join(ROLES)}}}.", field='members')
  try:
    repository = parse_repository(body.get('repository'))
  except AccessError as e:
    return error(400, 'invalid', e.message, field='repository')
  check = projects.check_create(g.actor, project_id)
  if not check.allowed:
    return projects.refusal(check)
  try:
    project = projects.create(g.actor, project_id, via='api', display_name=(display_name or '').strip() or None,
                              visibility=visibility, members=members, repository=repository, check=check)
  except AccessError as e:
    db_session.rollback()
    return error(e.status, 'invalid', e.message, field='members')
  db_session.commit()
  from ..access.grants import changed
  changed()
  return jsonify(serialize(project)), 201


@app.route('/api/v1/project/lifecycle', methods=['GET'])
@app.route('/api/v1/project/lifecycle/', methods=['GET'])
def project_lifecycle():
  """?project=: who created it, when, its namespace and repositories, whether it's archived"""
  from ..models import Project
  try:
    project_id = clean_path(request.args.get('project'))
  except AccessError as e:
    return error(400, 'invalid_project', e.message)
  read = authorize(g.actor, 'project.read', project_id)
  if not read.allowed:
    return denied(read)
  project = db_session.get(Project, project_id)
  if project is None:
    return error(404, 'not_found', f"There is no project {project_id}.")
  return jsonify({**serialize(project), "can_archive": authorize(g.actor, 'project.access', project_id).allowed})


@app.route('/api/v1/project/archive', methods=['POST'])
@app.route('/api/v1/project/archive/', methods=['POST'])
@login_required
def archive_project():
  """{project, archived: true|false}: archived projects are hidden from lists and refuse new results. Owners."""
  from ..models import Project
  body = request.get_json(silent=True) or {}
  try:
    project_id = clean_path(body.get('project'))
  except AccessError as e:
    return error(400, 'invalid_project', e.message)
  if not isinstance(body.get('archived'), bool):
    return error(400, 'invalid', "Send `archived`: true or false.", field='archived')
  decision = authorize(g.actor, 'project.access', project_id)
  if not decision.allowed:
    return denied(decision)
  project = db_session.get(Project, project_id)
  if project is None:
    return error(404, 'not_found', f"There is no project {project_id}.")
  projects.set_archived(g.actor, project, body['archived'])
  db_session.commit()
  return jsonify(serialize(project))
