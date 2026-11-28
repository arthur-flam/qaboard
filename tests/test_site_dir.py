"""
Site directories (website/docs/backend-admin/site-directory.mdx): the example site, deployments/example/, uses every
hook point a site has, and works from outside this repository, like a site's own repository cloned next to it.
With QABOARD_SITE_DIR set (e.g. in a site repository's CI), its sites' folders are checked too.
"""
import importlib.util
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import tomllib
import unittest
from pathlib import Path
from unittest import mock

from tests import site_dir_under_test

root_dir = Path(__file__).resolve().parent.parent
example = root_dir / 'deployments' / 'example'


def load_deploy():
  spec = importlib.util.spec_from_file_location('deploy', root_dir / 'deployments' / 'deploy.py')
  module = importlib.util.module_from_spec(spec)
  spec.loader.exec_module(module)
  return module


deploy = load_deploy()


def has_docker_compose():
  try:
    return subprocess.run(['docker', 'compose', 'version'], capture_output=True).returncode == 0
  except OSError:
    return False


def site_folders():
  """The sites' folders to check: the example, and those of QABOARD_SITE_DIR"""
  folders = [example]
  if site_dir_under_test:
    site_dir = (root_dir / site_dir_under_test).resolve()
    folders += sorted(p.parent for p in site_dir.glob('*/*.env') if p.parent != example)
  return sorted(set(folders))


class TestSiteFolders(unittest.TestCase):
  def test_cli_package_entry_points(self):
    for folder in site_folders():
      pyproject_path = folder / 'cli' / 'pyproject.toml'
      if not pyproject_path.exists():
        continue
      pyproject = tomllib.loads(pyproject_path.read_text())
      # The backend image installs qaboard-site-$QABOARD_EXTRA from cli/
      self.assertEqual(pyproject['project']['name'], f'qaboard-site-{folder.name}')
      for group, entry_points in pyproject['project'].get('entry-points', {}).items():
        for name, target in entry_points.items():
          with self.subTest(folder=folder.name, entry_point=f'{group}:{name}'):
            module, _, attribute = target.partition(':')
            self.assertTrue((folder / 'cli' / Path(*module.split('.'))).with_suffix('.py').exists()
                            or (folder / 'cli' / Path(*module.split('.')) / '__init__.py').exists(), target)

  def test_example_site_package(self):
    sys.path.insert(0, str(example / 'cli'))
    self.addCleanup(sys.path.remove, str(example / 'cli'))
    import qaboard_site_example
    from qaboard_site_example.hooks import fix_permissions
    self.assertTrue(Path(qaboard_site_example.defaults['QABOARD_SITE_CONFIG']).is_file())
    json.loads(qaboard_site_example.defaults['QABOARD_PATH_MAPPINGS'])
    with tempfile.TemporaryDirectory() as d:
      fix_permissions(Path(d))

  def test_site_docs(self):
    for folder in site_folders():
      if (folder / 'site-docs').exists():
        with self.subTest(folder.name):
          meta = json.loads((folder / 'site-docs' / '_site.json').read_text())
          self.assertTrue(meta['name'] and meta['label'])
          self.assertTrue(list((folder / 'site-docs').glob('*.md*')))

  def test_overlay_files_exist(self):
    # The site's own files, in the overlay: ${QABOARD_SITE_DIR:-./deployments}/<site>/<path>
    for folder in site_folders():
      for overlay in folder.glob('*.yml'):
        code = '\n'.join(line for line in overlay.read_text().splitlines() if not line.lstrip().startswith('#'))
        for path in re.findall(r'\$\{QABOARD_SITE_DIR[^}]*\}/' + re.escape(folder.name) + r'/([^\s:"\']+)', code):
          if path == '.env':  # the secrets: on the hosts only
            continue
          with self.subTest(overlay=f'{folder.name}/{overlay.name}', path=path):
            self.assertTrue((folder / path).exists(), f'{folder.name}/{path}')

  def test_secrets_stay_out_of_builds(self):
    # The folder is the images' site-config build context
    for folder in site_folders():
      with self.subTest(folder.name):
        self.assertIn('.env', (folder / '.dockerignore').read_text().split())


class TestDeploy(unittest.TestCase):
  def setUp(self):
    patcher = mock.patch.dict(os.environ)
    patcher.start()
    self.addCleanup(patcher.stop)
    for name in ('QABOARD_SITE_DIR', 'QABOARD_DOTENV'):
      os.environ.pop(name, None)
    tmp = tempfile.TemporaryDirectory()
    self.addCleanup(tmp.cleanup)
    self.site_dir = Path(tmp.name).resolve() / 'qaboard-site'
    shutil.copytree(example, self.site_dir / 'example')
    self.env_file = self.site_dir / 'example' / 'production.env'

  def test_site_dir(self):
    settings = deploy.read_env_file(self.env_file)
    # outside the repository: the folder above the env file's, wherever it's cloned
    self.assertEqual(deploy.site_dir_of(self.env_file, settings), self.site_dir)
    # in the repository: the env file's QABOARD_SITE_DIR default, relative to the repository
    in_repo = example / 'production.env'
    self.assertEqual(deploy.site_dir_of(in_repo, deploy.read_env_file(in_repo)), root_dir / 'deployments')
    self.assertEqual(deploy.site_dir_of(in_repo, {}), root_dir / 'deployments')
    # explicit
    self.assertEqual(deploy.site_dir_of(in_repo, {}, site_dir=str(self.site_dir)), self.site_dir)
    self.assertEqual(deploy.site_dir_of(in_repo, {}, site_dir='../x'), (root_dir / '../x').resolve())
    os.environ['QABOARD_SITE_DIR'] = str(self.site_dir)
    self.assertEqual(deploy.site_dir_of(in_repo, {}), self.site_dir)

  def test_dotenv_and_state_next_to_the_env_file(self):
    (self.env_file.parent / '.env').write_text('QABOARD_DB_PASSWORD=secret\n')
    legacy_state = root_dir / '.deploy-state.qaboard_example.json'
    self.assertFalse(legacy_state.exists())
    deployment = deploy.Deployment(str(self.env_file))
    self.assertEqual(deployment.site_dir, self.site_dir)
    self.assertEqual(deployment.env['QABOARD_SITE_DIR'], str(self.site_dir))
    self.assertEqual(deployment.dotenv, self.env_file.parent / '.env')
    self.assertEqual(deployment.env['QABOARD_DOTENV'], str(self.env_file.parent / '.env'))
    self.assertEqual(deployment.env['QABOARD_DB_PASSWORD'], 'secret')
    self.assertEqual(deployment.state_path, self.env_file.parent / '.deploy-state.qaboard_example.json')

  def test_dotenv_of_env_files_in_the_repository(self):
    # The repository's .env first, like before site directories
    with mock.patch.object(Path, 'is_file', lambda p: p in (root_dir / '.env', example / '.env')):
      self.assertEqual(deploy.dotenv_of(example / 'production.env', {}), root_dir / '.env')
      self.assertEqual(deploy.dotenv_of(self.env_file, {}), root_dir / '.env')  # no .env next to it
    os.environ['QABOARD_DOTENV'] = '/run/secrets/qaboard.env'
    self.assertEqual(deploy.dotenv_of(example / 'production.env', {}), Path('/run/secrets/qaboard.env'))

  def test_legacy_state(self):
    deployment = deploy.Deployment(str(self.env_file))
    legacy = {"current": "abc", "previous": "def", "history": []}
    exists = Path.exists
    for legacy_path in (root_dir / deployment.state_path.name, root_dir / 'deployments' / 'example' / deployment.state_path.name):
      with self.subTest(str(legacy_path.relative_to(root_dir))):
        with mock.patch.object(Path, 'exists', lambda p, legacy_path=legacy_path: p == legacy_path or (p != deployment.state_path and exists(p) and p.name != legacy_path.name)), \
             mock.patch.object(Path, 'read_text', lambda p, legacy_path=legacy_path: json.dumps(legacy) if p == legacy_path else ''):
          self.assertEqual(deployment.read_state(), legacy)

  def test_missing_site_dir(self):
    with self.assertRaises(SystemExit):
      deploy.Deployment(str(example / 'production.env'), site_dir='/no/such/site/dir')

  def test_rabbitmq_upgrades(self):
    self.assertEqual(deploy.major_minor('3.13.7\n'), (3, 13))
    self.assertEqual(deploy.major_minor('rabbitmq:4.2-management'), (4, 2))
    self.assertEqual(deploy.major_minor('registry:5000/rabbitmq:4-management'), (4, 0))
    self.assertIsNone(deploy.major_minor('rabbitmq:management'))
    self.assertIsNone(deploy.rabbitmq_upgrade_error('3.13.7', 'rabbitmq:4.2-management'))
    self.assertIsNone(deploy.rabbitmq_upgrade_error('4.2.1', 'rabbitmq:4.4-management'))
    self.assertIsNone(deploy.rabbitmq_upgrade_error('3.9.13', 'rabbitmq:3.13-management'))  # 3.x hops: RabbitMQ's call
    self.assertIsNone(deploy.rabbitmq_upgrade_error('', 'rabbitmq:4.2-management'))  # unknown: try, flags enabled
    self.assertIn('3.13 first', deploy.rabbitmq_upgrade_error('3.12.1', 'rabbitmq:4.2-management'))

  def fake_docker(self, deployment, running_image, version='3.13.7', volumes=('qaboard_example_db',), flags_fail=False):
    calls = []

    def run(cmd, **kwargs):
      calls.append(cmd)
      fail = (cmd[:3] == ['docker', 'volume', 'inspect'] and cmd[3] not in volumes) or \
             (flags_fail and 'enable_feature_flag' in cmd)
      return subprocess.CompletedProcess(cmd, int(fail), stdout='', stderr='error' if fail else '')

    def docker(*args, check=True):
      calls.append(['docker', *args])
      if args[0] == 'inspect':
        return running_image
      if args[:3] == ('exec', 'c1', 'rabbitmqctl'):
        return version
      return ''

    mock.patch.object(deployment, 'containers', lambda service, all_states=True: ['c1']).start()
    mock.patch.object(deployment, 'docker', docker).start()
    mock.patch.object(deploy.subprocess, 'run', run).start()
    self.addCleanup(mock.patch.stopall)
    return calls

  def test_prepare_rabbitmq(self):
    config = {'services': {'rabbitmq': {'image': 'rabbitmq:4.2-management'}}}
    deployment = deploy.Deployment(str(self.env_file))
    calls = self.fake_docker(deployment, 'rabbitmq:3-management')
    deployment.prepare_rabbitmq(config)
    self.assertIn(['docker', 'exec', 'c1', 'rabbitmqctl', 'enable_feature_flag', 'all'], calls)

    with self.subTest('same image: nothing to do'):
      calls.clear()
      mock.patch.stopall()
      calls = self.fake_docker(deployment, 'rabbitmq:4.2-management')
      deployment.prepare_rabbitmq(config)
      self.assertFalse([c for c in calls if 'enable_feature_flag' in c])
    for version, flags_fail in (('3.12.1', False), ('3.13.7', True)):
      with self.subTest(version=version, flags_fail=flags_fail):
        mock.patch.stopall()
        self.fake_docker(deployment, 'rabbitmq:3-management', version=version, flags_fail=flags_fail)
        with self.assertRaises(SystemExit):
          deployment.prepare_rabbitmq(config)

  def test_check_db_volumes(self):
    config = {
      'services': {'db': {'volumes': [
        {'type': 'volume', 'source': 'db18', 'target': '/var/lib/postgresql'},
        {'type': 'bind', 'source': '/srv/db', 'target': '/opt'},
      ]}},
      'volumes': {'db18': {'name': 'qaboard_example_db18'}},
    }
    deployment = deploy.Deployment(str(self.env_file))
    self.fake_docker(deployment, '', volumes=('qaboard_example_db18',))
    deployment.check_db_volumes(config)  # exists
    mock.patch.stopall()
    self.fake_docker(deployment, '', volumes=('qaboard_example_db',))
    with self.assertRaises(SystemExit):
      deployment.check_db_volumes(config)  # the new version's volume isn't there yet: it would start empty


@unittest.skipUnless(has_docker_compose(), "needs docker compose")
class TestComposeConfig(unittest.TestCase):
  """The example site's configuration, rendered by docker compose from outside the repository"""
  @classmethod
  def setUpClass(cls):
    cls.tmp = tempfile.TemporaryDirectory()
    cls.site_dir = Path(cls.tmp.name).resolve() / 'qaboard-site'
    shutil.copytree(example, cls.site_dir / 'example')
    env = {k: v for k, v in os.environ.items() if k not in ('QABOARD_SITE_DIR', 'QABOARD_DOTENV')}
    # through deploy.py: it finds the site directory and passes it on
    out = subprocess.run([sys.executable, str(root_dir / 'deployments' / 'deploy.py'), str(cls.site_dir / 'example' / 'production.env'),
                          'compose', 'config', '--format', 'json'], cwd=cls.tmp.name, env=env, capture_output=True, text=True)
    assert out.returncode == 0, out.stderr
    cls.config = json.loads(out.stdout[out.stdout.index('{'):])

  @classmethod
  def tearDownClass(cls):
    cls.tmp.cleanup()

  def test_paths_in_the_site_dir(self):
    services = self.config['services']
    folder = str(self.site_dir / 'example')
    self.assertEqual(services['backend']['build']['additional_contexts'], {'site-config': folder})
    self.assertEqual(services['backend']['build']['args']['QABOARD_EXTRA'], 'example')
    self.assertEqual(services['website']['build']['additional_contexts'], {'site-config': folder})
    self.assertEqual(services['website']['build']['args']['QABOARD_SITE'], 'example')
    self.assertIn(f'{folder}/nginx', [v['source'] for v in services['proxy']['volumes']])
    self.assertIn(f'{folder}/backend', [v['source'] for v in services['backend']['volumes']])
    # the repository's files stay relative to it
    self.assertEqual(services['backend']['build']['context'], str(root_dir))
    self.assertEqual(self.config['name'], 'qaboard_example')

  def test_plain_docker_compose(self):
    # Without deploy.py: compose reads QABOARD_SITE_DIR from the env file, here its default, in this repository
    env = {k: v for k, v in os.environ.items() if k not in ('QABOARD_SITE_DIR', 'QABOARD_DOTENV')}
    out = subprocess.run(['docker', 'compose', '--env-file', str(example / 'production.env'), 'config', '--format', 'json'],
                         cwd=root_dir, env=env, capture_output=True, text=True)
    self.assertEqual(out.returncode, 0, out.stderr)
    config = json.loads(out.stdout)
    self.assertEqual(config['services']['backend']['build']['additional_contexts'], {'site-config': str(example)})


if __name__ == '__main__':
  unittest.main()
