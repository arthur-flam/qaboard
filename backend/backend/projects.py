"""
The lifecycle of projects (docs/design/user-management.md §6, Appendix G): who creates them, in which namespace, with
which owners; archiving them.

- A project's namespace is the first segment of its id: `camera-algo` for camera-algo/isp. Namespaces are a user's
  (path = their user name) or an organization's. Their members are role grants on that path (backend/access/), the
  same grants as before namespaces: developers there create projects, owners manage them.
- `check_create(actor, id)` is the only policy. Explicit creation (POST /api/v1/projects, `qa project create`) and
  implicit creation (the first results `qa` sends, api/auth.py `ingest_access`) both use it, then `create`.
- Settings: projects.create (anywhere | namespace-members | admins), namespaces.create (anyone | admins).
  "anywhere" and "anyone", the defaults, are what servers did before: any project is created by its first results.
- The person who creates a project becomes its owner (unless they already own it through its namespace), and the
  person who claims a new namespace becomes the namespace's owner. Service accounts and anonymous senders don't.
- Push webhooks create projects only with projects.create "anywhere" (no one to check, api/webhooks.py).
"""
import logging
from dataclasses import dataclass, field
from typing import Optional

from backend import clock

logger = logging.getLogger(__name__)


CREATE_POLICIES = ('anywhere', 'namespace-members', 'admins')

from qaboard.conventions import Reason

# Refusal codes, shared with qa
CREATE_DENIED = Reason.PROJECT_CREATE_DENIED
ARCHIVED = Reason.PROJECT_ARCHIVED


@dataclass
class CreateCheck:
  allowed: bool
  project_id: str
  # exists, invalid_project, sign_in_required, token_project, project_create_denied
  reason: Optional[str] = None
  error: Optional[str] = None
  # what to do, one line
  hint: Optional[str] = None
  namespace: Optional[str] = None
  # the namespace doesn't exist: creating the project claims it
  new_namespace: bool = False
  # user (the creator's own namespace) or org
  namespace_kind: Optional[str] = None
  # a project id they could create instead, e.g. in their own namespace
  suggestion: Optional[str] = None
  # whom to ask: [{type, id, name}]
  ask: list = field(default_factory=list)

  def body(self) -> dict:
    return {
      "project": self.project_id, "allowed": self.allowed, "reason": self.reason, "error": self.error, "hint": self.hint,
      "namespace": self.namespace, "new_namespace": self.new_namespace, "namespace_kind": self.namespace_kind,
      "suggestion": self.suggestion, "ask": self.ask, "policy": policy(),
    }


def policy() -> dict:
  from .settings import setting
  return {"projects_create": setting('projects.create'), "namespaces_create": setting('namespaces.create')}


def namespace_path(project_id: str) -> str:
  return (project_id or '').strip('/').split('/')[0]


def find_namespace(path: str):
  from .models import Namespace
  return Namespace.query.filter(Namespace.path == path).one_or_none()


def is_human(user) -> bool:
  return user is not None and getattr(user, 'kind', 'human') == 'human'


def personal_namespace(user) -> Optional[str]:
  """A user's own namespace: their user name, if it can be the first part of project ids"""
  from .access.policy import is_valid_project_id
  if not is_human(user) or not user.user_name or '/' in user.user_name or not is_valid_project_id(user.user_name, new=True):
    return None
  return user.user_name


def member_role(user, project_id: str) -> Optional[str]:
  """
  The highest role a user has on a project id from grants (theirs and their groups', on all projects or on a prefix):
  what makes them a member of its namespace. Unlike access.role_of, visibility doesn't count: on a public project
  every signed-in user is a developer (projects.signed_in_role), not a member.
  """
  from .access import context_of
  from .access.policy import highest, applies_to
  if user is None:
    return None
  context = context_of(user)
  if context.is_admin:
    return 'owner'
  role = None
  for resource_type, resource_id, grant_role, _ in context.grants:
    if resource_type == 'instance' or (resource_type == 'project' and applies_to(resource_id, project_id)):
      role = highest(role, grant_role)
  return role


def admins_to_ask(limit=3) -> list:
  from .sign_in.access import reviewers
  return [{"type": "user", "id": u.id, "name": u.user_name, "full_name": u.full_name} for u in reviewers()[:limit]]


def _names(subjects) -> str:
  names = [s.get('name') for s in subjects if s.get('name')]
  return f" ({', '.join(names)})" if names else ''


def _in_own_namespace(user, project_id: str) -> Optional[str]:
  """alice/isp for camera-algo/isp, when alice can create it"""
  own = personal_namespace(user)
  if own is None:
    return None
  parts = project_id.strip('/').split('/')
  rest = '/'.join(parts[1:]) if len(parts) > 1 else parts[0]
  return f"{own}/{rest}"


def check_create(actor, project_id: str, root: Optional[str] = None) -> CreateCheck:
  """
  Whether the actor (None: anonymous) may create the project, else why not and what to do.
  `root`: the project.name of its qaboard.yaml, for sub-projects (group/repo for group/repo/sub): what hints suggest to change.
  """
  from .access.policy import is_valid_project_id
  from .access import owners
  from .models import Project
  from .settings import setting
  check = CreateCheck(allowed=False, project_id=project_id)
  if not is_valid_project_id(project_id, new=True):
    check.reason, check.error = 'invalid_project', "This isn't a valid project: name it like namespace/name, without . or .. or empty parts."
    return check
  from backend import db_session
  if db_session.get(Project, project_id) is not None:
    check.reason, check.error = 'exists', f"{project_id} already exists."
    return check
  user = actor.user if actor is not None else None
  if actor is not None and actor.token is not None and not actor.allows_project(project_id):
    check.reason, check.error = 'token_project', f"This token can't create {project_id}."
    return check
  mode = setting('projects.create')
  ns_path = namespace_path(project_id)
  namespace = find_namespace(ns_path)
  check.namespace = ns_path
  check.new_namespace = namespace is None
  own = personal_namespace(user)
  is_own = own is not None and ns_path == own and (namespace is None or (namespace.kind == 'user' and namespace.user_id == user.id))
  check.namespace_kind = namespace.kind if namespace is not None else ('user' if is_own else 'org')
  suggestion = _in_own_namespace(user, project_id)
  if suggestion == project_id:
    suggestion = None
  # what to write in qaboard.yaml: sub-projects are named after their root's project.name
  is_root = isinstance(root, str) and root.strip('/') and (project_id == root.strip('/') or project_id.startswith(root.strip('/') + '/'))
  name_suggestion = _in_own_namespace(user, root.strip('/')) if is_root else suggestion

  def deny(reason, error, hint, ask=(), suggest=True):
    check.reason, check.error, check.hint, check.ask = reason, error, hint, list(ask)
    check.suggestion = suggestion if suggest else None
    return check

  if actor is not None and actor.is_admin:
    check.allowed = True
    return check
  if mode == 'admins':
    admins = admins_to_ask()
    return deny(CREATE_DENIED, f"Only QA-Board admins create projects on this server, and {project_id} doesn't exist yet.",
                f"Ask an admin{_names(admins)} to create {project_id} (`qa project create {project_id}`, or New project on QA-Board's home page), then send your results again.",
                admins, suggest=False)
  single = '/' not in project_id.strip('/')
  if mode != 'anywhere':
    if user is None:
      return deny('sign_in_required', f"Sign in to create {project_id}: this server only lets members of a namespace create its projects.",
                  "Run `qa auth login` (in CI, set QABOARD_TOKEN), then send your results again.", suggest=False)
    if single:
      fix = f"Set project.name to {name_suggestion} in qaboard.yaml (or project.namespace: {own})" if name_suggestion else "Set project.name to namespace/name in qaboard.yaml"
      return deny(CREATE_DENIED, f"Projects are named namespace/name on this server: {project_id} has no namespace.", f"{fix}, then send your results again.")
  if is_own:
    check.allowed = True
    return check
  if namespace is None:
    if setting('namespaces.create') == 'admins':
      admins = admins_to_ask()
      own_fix = f"Use your own namespace: set project.name to {name_suggestion} in qaboard.yaml, or ask" if name_suggestion else "Ask"
      return deny(CREATE_DENIED, f"{ns_path} isn't a namespace on this server, and only admins create namespaces.",
                  f"{own_fix} an admin{_names(admins)} to create the namespace {ns_path}.", admins)
    if mode == 'anywhere' or is_human(user):
      check.allowed = True
      return check
    return deny(CREATE_DENIED, f"{ns_path} isn't a namespace on this server, and service accounts don't create namespaces.",
                f"Have a person create {project_id} (`qa project create {project_id}`), or create the service account's projects in a namespace it's a developer of.", suggest=False)
  if mode == 'anywhere':
    check.allowed = True
    return check
  from .access.policy import allows
  if allows(member_role(user, project_id), 'runs.ingest'):
    check.allowed = True
    return check
  namespace_owners = owners(ns_path)
  own_fix = f", or use your own namespace: set project.name to {name_suggestion} in qaboard.yaml" if name_suggestion else ''
  return deny(CREATE_DENIED, f"Only the developers and owners of the namespace {ns_path} create its projects, and {project_id} doesn't exist yet.",
              f"Ask an owner of {ns_path}{_names(namespace_owners) if namespace_owners else _names(admins_to_ask())} for the developer role on {ns_path}, or to create {project_id}{own_fix}.",
              namespace_owners or admins_to_ask())


def lock(path: str):
  """Two requests creating the same namespace or project at once (a batch's first runs): one waits for the other"""
  from backend import db_session
  from sqlalchemy import text
  db_session.execute(text("SELECT pg_advisory_xact_lock(hashtext(:key))"), {"key": f"qaboard-namespace:{path}"})


def ensure_namespace(actor, path: str, kind: str = 'org', display_name: Optional[str] = None):
  """The namespace, created if needed: with its creator as owner (people only). Returns (namespace, created)."""
  from backend import db_session
  from .models import Namespace
  from .audit import audit
  namespace = find_namespace(path)
  if namespace is not None:
    return namespace, False
  user = actor.user if actor is not None else None
  namespace = Namespace(path=path, kind=kind, display_name=display_name or None, created_at=clock.utcnow(),
                        user_id=user.id if kind == 'user' and user is not None else None,
                        created_by_id=user.id if user is not None else None, data={})
  db_session.add(namespace)
  db_session.flush()
  owner = None
  if is_human(user):
    from .access.grants import set_grant
    set_grant(actor, {"type": "user", "id": user.id}, 'owner', 'project', path, source='create')
    owner = user.user_name
  audit('namespace.create', 'namespace', namespace.id, path, actor=actor, meta={"kind": kind, "owner": owner})
  return namespace, True


def repo_from_url(url) -> Optional[dict]:
  """{git_host, repo_path} of a clone or web URL: git@gitlab.example.com:group/repo.git, https://github.com/org/repo"""
  from .git_hosts.base import hostname_of
  if not isinstance(url, str) or not url.strip():
    return None
  url = url.strip()
  host = hostname_of(url) or ''
  if '://' in url:
    path = url.split('://', 1)[1].split('/', 1)[1] if '/' in url.split('://', 1)[1] else ''
  elif ':' in url:
    path = url.split(':', 1)[1]
  else:
    path = url.split('/', 1)[1] if '/' in url else ''
  path = path.strip('/').removesuffix('.git').strip('/')
  if not path or '/' not in path:
    return None
  return {"git_host": host, "repo_path": path}


def link_repo(project_id: str, git_host: str, repo_path: str, subdir: str = ''):
  from backend import db_session
  from .models import ProjectRepo
  existing = ProjectRepo.query.filter(ProjectRepo.project_id == project_id, ProjectRepo.git_host == (git_host or ''),
                                      ProjectRepo.repo_path == repo_path).one_or_none()
  if existing is not None:
    return existing
  link = ProjectRepo(project_id=project_id, git_host=(git_host or '').lower(), repo_path=repo_path.strip('/'),
                     subdir=(subdir or '').strip('/'), created_at=clock.utcnow())
  db_session.add(link)
  return link


def default_repo(project_id: str) -> Optional[dict]:
  """Without a repository: group/repo/sub is sub in the repository group/repo, as before"""
  parts = project_id.strip('/').split('/')
  if len(parts) < 2:
    return None
  return {"git_host": '', "repo_path": '/'.join(parts[:2]), "subdir": '/'.join(parts[2:])}


def projects_for_repo(git_host: Optional[str], repo_path: str) -> list:
  """The ids of the projects linked to a repository (any host when it's unknown, or the link doesn't say)"""
  from sqlalchemy import or_
  from .models import ProjectRepo
  query = ProjectRepo.query.filter(ProjectRepo.repo_path == repo_path.strip('/'))
  if git_host:
    query = query.filter(or_(ProjectRepo.git_host == git_host.lower(), ProjectRepo.git_host == ''))
  return sorted({link.project_id for link in query.all()})


def create(actor, project_id: str, via: str, display_name: Optional[str] = None, visibility: Optional[str] = None,
           members=(), repository: Optional[dict] = None, check: Optional[CreateCheck] = None):
  """
  Creates the project (the caller checked `check_create`), its namespace if needed, the creator's owner grant, its
  members and its repository link, in the current session: the caller commits. Returns the project, or the one
  another request created meanwhile.
  """
  from backend import db_session
  from .models import Project
  from .audit import audit
  from .access.grants import set_grant, set_visibility
  user = actor.user if actor is not None else None
  ns_path = namespace_path(project_id)
  lock(ns_path)
  existing = db_session.get(Project, project_id)
  if existing is not None:
    return existing
  own = personal_namespace(user)
  kind = 'user' if own is not None and ns_path == own and (check is None or check.namespace_kind == 'user') else 'org'
  namespace, new_namespace = ensure_namespace(actor, ns_path, kind=kind)
  data = {}
  if display_name:
    data['display_name'] = display_name
  project = Project(id=project_id, data=data, namespace_id=namespace.id, created_at=clock.utcnow(),
                    created_by_id=user.id if user is not None else None, created_via=via)
  db_session.add(project)
  db_session.flush()
  owner = None
  if is_human(user):
    db_session.flush()
    _clear_access_cache()
    if member_role(user, project_id) != 'owner':
      set_grant(actor, {"type": "user", "id": user.id}, 'owner', 'project', project_id, source='create')
    owner = user.user_name
  if visibility is not None:
    set_visibility(actor, project_id, visibility)
  added = []
  for member in members or []:
    grant, _ = set_grant(actor, member.get('subject'), member.get('role'), 'project', project_id)
    added.append({"subject": member.get('subject'), "role": grant.role})
  link = repository if repository else default_repo(project_id)
  if link and link.get('repo_path'):
    link_repo(project_id, link.get('git_host') or '', link['repo_path'], link.get('subdir') or '')
  audit('project.create', 'project', project_id, project_id, project_id=project_id, actor=actor, meta={
    "via": via, "namespace": ns_path, "new_namespace": new_namespace, "owner": owner, "visibility": visibility,
    "members": added, "repository": link,
  })
  db_session.flush()
  _clear_access_cache()
  logger.info("Created the project %s (%s)", project_id, via, extra={"project": project_id, "via": via})
  return project


def _clear_access_cache():
  from flask import g, has_request_context
  if has_request_context() and hasattr(g, '_access'):
    g._access.clear()


def is_archived(project_id: str) -> bool:
  from backend import db_session
  from .models import Project
  project = db_session.get(Project, project_id) if project_id else None
  return project is not None and project.archived_at is not None


def set_archived(actor, project, archived: bool) -> bool:
  """Archives or unarchives the project: returns whether it changed. The caller commits."""
  from .audit import audit
  if bool(project.archived_at) == bool(archived):
    return False
  user = actor.user if actor is not None else None
  if archived:
    project.archived_at, project.archived_by_id = clock.utcnow(), (user.id if user is not None else None)
  else:
    project.archived_at, project.archived_by_id = None, None
  audit('project.archive' if archived else 'project.unarchive', 'project', project.id, project.id, project_id=project.id, actor=actor)
  return True


def archived_refusal(project_id: str):
  from .actor import error
  return error(403, ARCHIVED, f"{project_id} is archived: it doesn't take new results.",
               hint=f"Ask an owner of {project_id} to unarchive it (its Access page in QA-Board, or `qa project unarchive {project_id}`).",
               project=project_id)


def refusal(check: CreateCheck):
  """The response of a refused creation"""
  from .actor import error
  status = 401 if check.reason == 'sign_in_required' else 400 if check.reason == 'invalid_project' else 409 if check.reason == 'exists' else 403
  return error(status, check.reason, check.error, hint=check.hint, project=check.project_id, namespace=check.namespace,
               suggestion=check.suggestion, ask=check.ask, owners=check.ask)
