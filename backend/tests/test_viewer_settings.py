"""
Tests for what a project's developers save for everyone, e.g. the sensor of its raw images: backend/viewer_settings.py
and POST /api/v1/project/settings in backend/api/api.py, with the database mocked (see tests/TESTING.md).
Who may save them, on a real database: integration_tests/test_access.py.
"""
import sys
import types
import importlib
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

from backend.viewer_settings import clean_sensor, save_setting
from backend.access.policy import allows, required_scope


class TestCleanSensor:
  def test_keeps_what_is_set(self):
    sensor = {'kind': 'mosaic', 'pattern': 'grbg', 'block': 2, 'offset': {'x': 1, 'y': 0}, 'black_level': 64.0, 'grouping': 'sub'}
    assert clean_sensor(sensor) == ({'kind': 'mosaic', 'pattern': 'GRBG', 'block': 2, 'offset': {'x': 1, 'y': 0}, 'black_level': 64}, None)
    assert clean_sensor({'black_level': 63.5}) == ({'black_level': 63.5}, None)
    assert clean_sensor({'offset': {'x': 3}}) == ({'offset': {'x': 3, 'y': 0}}, None)

  def test_nothing_set_forgets_it(self):
    assert clean_sensor(None) == (None, None)
    assert clean_sensor({}) == (None, None)
    # automatic is the default: not saved
    assert clean_sensor({'kind': 'auto', 'pattern': None, 'black_level': None}) == (None, None)

  @pytest.mark.parametrize('sensor, error', [
    ([], 'must be an object'),
    ({'kind': 'rgb'}, 'sensor.kind'),
    ({'pattern': 'RGBG'}, 'sensor.pattern'),
    ({'pattern': 4}, 'sensor.pattern'),
    ({'block': 0}, 'sensor.block'),
    ({'block': 10}, 'sensor.block'),
    ({'block': 1.5}, 'sensor.block'),
    ({'block': True}, 'sensor.block'),
    ({'block': '2'}, 'sensor.block'),
    ({'block': 2, 'offset': {'x': 4, 'y': 0}}, 'from 0 to 3'),
    ({'offset': {'x': -1}}, 'sensor.offset'),
    ({'offset': [0, 0]}, 'sensor.offset'),
    ({'black_level': -1}, 'sensor.black_level'),
    ({'black_level': '64'}, 'sensor.black_level'),
    ({'black_level': float('nan')}, 'sensor.black_level'),
    ({'black_level': False}, 'sensor.black_level'),
  ])
  def test_refuses_what_the_viewer_cant_use(self, sensor, error):
    cleaned, message = clean_sensor(sensor)
    assert cleaned is None
    assert error in message


def project_with(data):
  return SimpleNamespace(id='group/repo', data=data)


BOB = {'user_name': 'bob', 'full_name': 'Bob B', 'user_id': 2, 'is_authenticated': True}


class TestSaveSetting:
  def test_keeps_the_rest_of_the_project_data(self):
    session = MagicMock()
    project = project_with({'qatools_config': {'project': {'name': 'repo'}}, 'git': {'path_with_namespace': 'group/repo'}})
    settings = save_setting(session, project, 'sensor', {'black_level': 64}, BOB)
    # locked, then saved
    session.refresh.assert_called_once_with(project, with_for_update=True)
    session.commit.assert_called_once()
    assert settings == project.data['settings']
    assert settings['sensor']['black_level'] == 64
    assert settings['sensor']['updated_by'] == {'user_name': 'bob', 'full_name': 'Bob B'}
    assert settings['sensor']['updated_at'].endswith('Z')
    assert project.data['qatools_config'] == {'project': {'name': 'repo'}} and project.data['git'] == {'path_with_namespace': 'group/repo'}

  def test_replaces_the_previous_value(self):
    project = project_with({'settings': {'sensor': {'pattern': 'RGGB', 'black_level': 64}, 'other': {'a': 1}}})
    settings = save_setting(MagicMock(), project, 'sensor', {'pattern': 'GRBG'}, BOB)
    assert 'black_level' not in settings['sensor'] and settings['sensor']['pattern'] == 'GRBG'
    assert settings['other'] == {'a': 1}

  def test_forgets(self):
    project = project_with({'settings': {'sensor': {'black_level': 64}}, 'git': {}})
    assert save_setting(MagicMock(), project, 'sensor', None, BOB) == {}
    assert project.data == {'git': {}}
    # projects without data, e.g. created by a push webhook
    project = project_with(None)
    assert save_setting(MagicMock(), project, 'sensor', None, BOB) == {}
    assert project.data == {}


def test_developers_and_owners_save_them():
  assert allows('developer', 'viewer_settings.write') and allows('owner', 'viewer_settings.write')
  assert not allows('viewer', 'viewer_settings.write') and not allows(None, 'viewer_settings.write')
  assert required_scope('viewer_settings.write') == 'act'


# ==========================================
# API
# ==========================================

@pytest.fixture
def api(monkeypatch):
  """Imports backend/api/api.py with SQLAlchemy mocked (conftest.py replaces it with an empty module)"""
  for name in ('func', 'and_', 'asc', 'or_', 'not_', 'text'):
    monkeypatch.setattr(sys.modules['sqlalchemy'], name, MagicMock(), raising=False)
  sa_sql = types.ModuleType('sqlalchemy.sql')
  sa_sql.label = MagicMock()
  monkeypatch.setitem(sys.modules, 'sqlalchemy.sql', sa_sql)
  sys.modules.pop('backend.api.api', None)
  api = importlib.import_module('backend.api.api')
  # the database session is shared by the tests
  api.db_session.reset_mock(return_value=True, side_effect=True)
  # who may save: the role check is backend/access (test_developers_and_owners_save_them), here we see it's asked
  api.checked = []
  def require_project_access(user_info, project_id, permission='runs.act'):
    api.checked.append((project_id, permission))
    if project_id == 'group/viewed':
      from flask import jsonify
      return jsonify({"error": "You need the developer role to save the viewers' settings of group/viewed: you're a viewer.", "reason": "missing_role"}), 403
    return None
  monkeypatch.setattr(api, 'require_project_access', require_project_access)
  monkeypatch.setattr(api, 'get_current_user', MagicMock(return_value=BOB))
  api.project = project_with({'qatools_config': {}})
  api.Project.query.filter.return_value.one_or_none.return_value = api.project
  yield api
  api.db_session.reset_mock(return_value=True, side_effect=True)
  api.Project.reset_mock(return_value=True, side_effect=True)
  sys.modules.pop('backend.api.api', None)


def post(dummy_app, api, json):
  with dummy_app.test_request_context('/api/v1/project/settings', method='POST', json=json):
    response = api.post_project_settings()
    if isinstance(response, tuple):
      response, status = response
      response.status_code = status
    return response


def test_saves_the_sensor_for_the_project(dummy_app, api):
  response = post(dummy_app, api, {'project': 'group/repo', 'sensor': {'pattern': 'GRBG', 'block': 2, 'black_level': 64}})
  assert response.status_code == 200
  sensor = response.get_json()['settings']['sensor']
  assert (sensor['pattern'], sensor['block'], sensor['black_level'], sensor['updated_by']['user_name']) == ('GRBG', 2, 64, 'bob')
  assert api.project.data['settings']['sensor'] == sensor
  assert api.checked == [('group/repo', 'viewer_settings.write')]
  api.db_session.commit.assert_called_once()


def test_forgets_the_sensor(dummy_app, api):
  api.project.data = {'settings': {'sensor': {'black_level': 64}}}
  response = post(dummy_app, api, {'project': 'group/repo', 'sensor': None})
  assert (response.status_code, response.get_json()) == (200, {'settings': {}})
  assert 'settings' not in api.project.data


def test_needs_the_role(dummy_app, api):
  response = post(dummy_app, api, {'project': 'group/viewed', 'sensor': {'black_level': 64}})
  assert response.status_code == 403
  assert response.get_json()['reason'] == 'missing_role'
  api.db_session.commit.assert_not_called()


@pytest.mark.parametrize('body, status, error', [
  (None, 400, 'Expected a JSON object'),
  ({'sensor': {}}, 400, 'Which project?'),
  ({'project': 'group/repo'}, 400, 'Nothing to save'),
  ({'project': 'group/repo', 'sensor': {'block': 12}}, 400, 'sensor.block'),
])
def test_bad_requests(dummy_app, api, body, status, error):
  response = post(dummy_app, api, body)
  assert response.status_code == status
  assert error in response.get_json()['error']
  api.db_session.commit.assert_not_called()


def test_unknown_projects(dummy_app, api):
  api.Project.query.filter.return_value.one_or_none.return_value = None
  response = post(dummy_app, api, {'project': 'group/gone', 'sensor': {'black_level': 64}})
  assert response.status_code == 404
