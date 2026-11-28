"""
Projects' lifecycle (backend/projects.py): who creates them, explicitly or with their first results, their namespace
and owners, archiving. docs/design/user-management.md, Appendix G.
"""
import pytest

from .conftest import new_token, bearer, login


def commit(project, sha='c' * 40):
  return {
    "project": project, "commit_sha": sha, "commit_branch": "main",
    "commit_authored_datetime": "2026-11-01T12:00:00+00:00", "commit_message": "a commit",
    "commit_committer_name": "Alice", "commit_parents": [],
    "qaboard_config": {"project": {"name": project, "url": "git@gitlab.example.com:camera/isp.git"}},
  }


def policy(monkeypatch, projects_create=None, namespaces_create=None):
  for name, value in (('QABOARD_PROJECTS_CREATE', projects_create), ('QABOARD_NAMESPACES_CREATE', namespaces_create)):
    if value is None:
      monkeypatch.delenv(name, raising=False)
    else:
      monkeypatch.setenv(name, value)


def project_row(project_id):
  from backend.database import db_session
  from backend.models import Project
  db_session.remove()
  return db_session.get(Project, project_id)


def role(user_name, path):
  from backend.database import db_session
  from backend.models import RoleGrant, User
  db_session.remove()
  user = User.query.filter(User.user_name == user_name).one()
  grant = RoleGrant.query.filter(RoleGrant.subject_type == 'user', RoleGrant.subject_id == user.id,
                                 RoleGrant.resource_type == 'project', RoleGrant.resource_id == path).one_or_none()
  return grant.role if grant else None


def actions():
  from backend.database import db_session
  from backend.models import AuditEvent
  db_session.remove()
  return [e.action for e in AuditEvent.query.order_by(AuditEvent.id).all()]


def test_default_policy_is_todays(client, logged_in):
  """anywhere/anyone: anonymous results create projects as before; signed-in creators own them and their new namespace"""
  response = client.post('/api/v1/commit/', json=commit('legacy/repo'))
  assert response.status_code == 200, response.get_json()
  project = project_row('legacy/repo')
  assert project.created_via == 'ingest' and project.created_by_id is None
  from backend.models import Namespace, ProjectRepo
  namespace = Namespace.query.filter(Namespace.path == 'legacy').one()
  assert namespace.kind == 'org'
  link = ProjectRepo.query.filter(ProjectRepo.project_id == 'legacy/repo').one()
  assert (link.git_host, link.repo_path, link.subdir) == ('gitlab.example.com', 'camera/isp', '')

  alice = logged_in('alice')
  token = new_token(alice)["token"]
  response = client.post('/api/v1/commit/', json=commit('newteam/isp'), headers=bearer(token))
  assert response.status_code == 200, response.get_json()
  assert role('alice', 'newteam') == 'owner'
  # owner through the namespace: no grant on the project itself
  assert role('alice', 'newteam/isp') is None
  # in a namespace that exists, without being a member: today's behaviour, and they own what they created
  response = client.post('/api/v1/commit/', json=commit('legacy/other'), headers=bearer(token))
  assert response.status_code == 200, response.get_json()
  assert role('alice', 'legacy/other') == 'owner'
  assert actions().count('project.create') == 3
  assert actions().count('namespace.create') == 2


def test_namespace_members(client, logged_in, make_user, monkeypatch):
  policy(monkeypatch, 'namespace-members')
  alice = logged_in('alice')
  token = new_token(alice)["token"]
  # anonymous: refused, even with ingest.auth off
  response = client.post('/api/v1/commit/', json=commit('alice/isp'))
  assert response.status_code == 401 and response.get_json()["reason"] == 'sign_in_required'
  # their own namespace
  response = client.post('/api/v1/commit/', json=commit('alice/isp'), headers=bearer(token))
  assert response.status_code == 200, response.get_json()
  from backend.models import Namespace
  assert Namespace.query.filter(Namespace.path == 'alice').one().kind == 'user'
  assert role('alice', 'alice') == 'owner'
  # a new namespace: they claim it
  assert client.post('/api/v1/commit/', json=commit('camera/isp'), headers=bearer(token)).status_code == 200
  # someone else's namespace: refused, with whom to ask and where to go instead
  bob = logged_in('bob')
  bob_token = new_token(bob)["token"]
  response = client.post('/api/v1/commit/', json=commit('camera/nr'), headers=bearer(bob_token))
  body = response.get_json()
  assert response.status_code == 403 and body["reason"] == 'project_create_denied', body
  assert 'alice' in body["hint"] and body["suggestion"] == 'bob/nr'
  assert project_row('camera/nr') is None
  # a developer of the namespace can, and becomes the project's owner
  alice.post('/api/v1/project/access/grants', json={"project": "camera", "subject": {"type": "user", "id": 2}, "role": "developer"},
             headers={"Origin": "http://localhost:5151"})
  response = client.post('/api/v1/commit/', json=commit('camera/nr'), headers=bearer(bob_token))
  assert response.status_code == 200, response.get_json()
  assert role('bob', 'camera/nr') == 'owner'
  # single-segment names: refused under a namespace policy
  response = client.post('/api/v1/commit/', json=commit('isp'), headers=bearer(bob_token))
  assert response.status_code == 403 and response.get_json()["suggestion"] == 'bob/isp'
  # existing projects are not affected
  assert client.post('/api/v1/commit/', json=commit('camera/nr', sha='d' * 40), headers=bearer(bob_token)).status_code == 200


def test_namespaces_by_admins(client, logged_in, monkeypatch):
  policy(monkeypatch, 'anywhere', 'admins')
  alice = logged_in('alice')
  token = new_token(alice)["token"]
  response = client.post('/api/v1/commit/', json=commit('newteam/isp'), headers=bearer(token))
  body = response.get_json()
  assert response.status_code == 403 and body["reason"] == 'project_create_denied'
  assert body["suggestion"] == 'alice/isp'
  assert client.post('/api/v1/commit/', json=commit('alice/isp'), headers=bearer(token)).status_code == 200


def test_admins_only(client, logged_in, monkeypatch):
  policy(monkeypatch, 'admins')
  alice = logged_in('alice')
  response = alice.post('/api/v1/projects', json={"project": "alice/isp"}, headers={"Origin": "http://localhost:5151"})
  assert response.status_code == 403 and response.get_json()["reason"] == 'project_create_denied'
  admin = logged_in('root', is_admin=True)
  response = admin.post('/api/v1/projects', json={"project": "camera/isp", "visibility": "private",
                                                   "members": [{"subject": {"type": "user", "id": 1}, "role": "developer"}],
                                                   "repository": {"url": "https://github.com/camera/isp"}},
                        headers={"Origin": "http://localhost:5151"})
  assert response.status_code == 201, response.get_json()
  body = response.get_json()
  assert body["repositories"] == [{"git_host": "github.com", "repo_path": "camera/isp", "subdir": ""}]
  assert body["namespace"]["path"] == 'camera'
  assert role('root', 'camera') == 'owner'
  assert role('alice', 'camera/isp') == 'developer'
  # listed before its first commit, to those who can see it
  assert 'camera/isp' in admin.get('/api/v1/projects').get_json()
  assert 'camera/isp' in alice.get('/api/v1/projects').get_json()
  # results from a developer
  token = new_token(alice)["token"]
  assert alice.post('/api/v1/commit/', json=commit('camera/isp'), headers=bearer(token)).status_code == 200
  response = alice.post('/api/v1/projects', json={"project": "camera/isp"}, headers={"Origin": "http://localhost:5151"})
  assert response.status_code == 409


def test_check(client, logged_in, monkeypatch):
  policy(monkeypatch, 'namespace-members')
  alice = logged_in('alice')
  answer = alice.get('/api/v1/projects/check?project=alice/isp').get_json()
  assert answer["allowed"] and answer["namespace_kind"] == 'user' and not answer["exists"]
  answer = client.get('/api/v1/projects/check?project=alice/isp').get_json()
  assert not answer["allowed"] and answer["reason"] == 'sign_in_required'


def test_archive(client, logged_in):
  alice = logged_in('alice')
  token = new_token(alice)["token"]
  assert client.post('/api/v1/commit/', json=commit('alice/isp'), headers=bearer(token)).status_code == 200
  bob = logged_in('bob')
  response = bob.post('/api/v1/project/archive', json={"project": "alice/isp", "archived": True}, headers={"Origin": "http://localhost:5151"})
  assert response.status_code == 403
  response = alice.post('/api/v1/project/archive', json={"project": "alice/isp", "archived": True}, headers={"Origin": "http://localhost:5151"})
  assert response.status_code == 200 and response.get_json()["archived"]
  response = client.post('/api/v1/commit/', json=commit('alice/isp', sha='d' * 40), headers=bearer(token))
  assert response.status_code == 403 and response.get_json()["reason"] == 'project_archived'
  assert 'alice/isp' not in alice.get('/api/v1/projects').get_json()
  assert alice.get('/api/v1/projects?archived=true').get_json()['alice/isp']['archived'] is True
  response = alice.post('/api/v1/project/archive', json={"project": "alice/isp", "archived": False}, headers={"Origin": "http://localhost:5151"})
  assert response.status_code == 200
  assert client.post('/api/v1/commit/', json=commit('alice/isp', sha='d' * 40), headers=bearer(token)).status_code == 200
  assert actions().count('project.archive') == 1 and actions().count('project.unarchive') == 1


def test_webhooks_dont_create_projects_under_a_strict_policy(monkeypatch):
  from backend.database import db_session
  from backend.models.Project import update_project
  from backend import projects
  import sys
  project_module = sys.modules['backend.models.Project']
  monkeypatch.setattr(project_module.repos, 'get', lambda *a, **k: (_ for _ in ()).throw(RuntimeError("no clone in tests")))
  push = {"ref": "refs/heads/main", "checkout_sha": "e" * 40, "project": {"path_with_namespace": "camera/isp", "host": "gitlab.example.com"}}
  policy(monkeypatch, 'namespace-members')
  update_project(push, db_session)
  assert project_row('camera/isp') is None
  policy(monkeypatch, 'anywhere')
  update_project(push, db_session)
  assert project_row('camera/isp').created_via == 'webhook'
  assert projects.projects_for_repo('gitlab.example.com', 'camera/isp') == ['camera/isp']
