"""
Namespaces own projects (docs/design/user-management.md §6, Appendix G): `alice` (a user's) or `camera-algo` (an
organization's). A project's namespace is the first segment of its id. Members are role grants on the namespace's
path (backend/access/): there is no second permission system.
Repository links say which git repositories a project's commits come from: webhooks find projects through them.
"""
from backend import clock

from sqlalchemy import Column, Integer, String, Boolean, DateTime, ForeignKey, UniqueConstraint
from sqlalchemy.dialects.postgresql import JSONB

from backend.models import Base


class Namespace(Base):
  __tablename__ = 'namespaces'

  id = Column(Integer, primary_key=True)
  # the first segment of its projects' ids. Users and organizations share one path space.
  path = Column(String(), nullable=False, unique=True)
  # user (a user's personal namespace: path is their user name), org
  kind = Column(String(), nullable=False, default='org', server_default='org')
  display_name = Column(String(), nullable=True)
  # for kind=user: whose
  user_id = Column(Integer, ForeignKey('users.id', ondelete='SET NULL'), nullable=True)
  created_by_id = Column(Integer, ForeignKey('users.id', ondelete='SET NULL'), nullable=True)
  created_at = Column(DateTime, nullable=False, default=clock.utcnow)
  # created by the upgrade for the first segment of existing projects: no members, prefix grants work as before
  legacy = Column(Boolean, nullable=False, default=False, server_default='false')
  # later: entitlements, quotas (hosted versions)
  data = Column(JSONB(), nullable=False, default=dict, server_default='{}')


class ProjectRepo(Base):
  """A git repository a project's commits come from, with the project's folder in it (subdir, '' at the root)"""
  __tablename__ = 'project_repos'
  __table_args__ = (UniqueConstraint('project_id', 'git_host', 'repo_path', name='uq_project_repos_project_repo'),)

  id = Column(Integer, primary_key=True)
  project_id = Column(String(), ForeignKey('projects.id', ondelete='CASCADE'), nullable=False, index=True)
  # the git host's hostname (gitlab.example.com), '' when we don't know it
  git_host = Column(String(), nullable=False, default='', server_default='')
  # group/repo on the git host
  repo_path = Column(String(), nullable=False, index=True)
  subdir = Column(String(), nullable=False, default='', server_default='')
  created_at = Column(DateTime, nullable=False, default=clock.utcnow)
