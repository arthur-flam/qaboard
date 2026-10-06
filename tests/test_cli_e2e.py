"""
End-to-end tests of the `qa` command, run like users, CI and agents do: in a subprocess, on a copy of the sample project.
They also cover what in-process tests can't: "--" arguments, deprecated flags and environment variables are handled in main().
"""
import os
import re
import sys
import json
import shutil
import tempfile
import unittest
import subprocess
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parent.parent
ANSI = re.compile(r'\x1b\[')

# Variables that change how qa behaves (CI mode, commit...): the tests must not depend on where they run
CI_VARIABLES = ('CI', 'GIT_COMMIT', 'CI_COMMIT_SHA', 'CI_COMMIT_REF_NAME', 'CI_COMMIT_TAG', 'GITHUB_SHA', 'GITHUB_REF',
                'GIT_BRANCH', 'NO_COLOR', 'QA_OUTPUTS_COMMIT', 'QA_BATCH', 'QA_BATCH_COMMAND_ID', 'QA_BATCHES_FILES',
                # force colors in --help and errors (typer/rich), e.g. in GitHub Actions
                'GITHUB_ACTIONS', 'FORCE_COLOR', 'PY_COLORS')


def run_qa(cwd, *args, env=None, check=None):
  """Runs `qa ARGS` and returns the completed process (with .stdout and .stderr)."""
  base_env = {k: v for k, v in os.environ.items() if k not in CI_VARIABLES and not k.startswith('QA_')}
  full_env = {
    **base_env,
    'PYTHONPATH': str(ROOT),
    # `qa batch` runs `python -m qaboard` (QA_TESTING), it must be this python, with qaboard's dependencies
    'PATH': os.pathsep.join([str(Path(sys.executable).parent), os.environ.get('PATH', '')]),
    'QA_TESTING': '1',
    'QA_NO_CHECK_FOR_UPDATES': '1',
    # git must never wait for a password (qa init runs `git remote show`)
    'GIT_TERMINAL_PROMPT': '0',
    'QA_OFFLINE': 'true',
    'QABOARD_HOST': 'localhost:5151',
    'COLUMNS': '200', # help without line wraps
    **(env or {}),
  }
  process = subprocess.run([sys.executable, '-m', 'qaboard', *args], cwd=cwd, env=full_env, capture_output=True, text=True)
  if check is not None and process.returncode != check:
    raise AssertionError(f"qa {' '.join(args)} exited with {process.returncode}, not {check}\nSTDOUT:\n{process.stdout}\nSTDERR:\n{process.stderr}")
  return process


class QaProject(unittest.TestCase):
  """Each test class gets its own copy of the sample project, in a git repository, with inputs and storage."""
  @classmethod
  def setUpClass(cls):
    cls.tmp = tempfile.TemporaryDirectory()
    cls.project = Path(cls.tmp.name) / 'project'
    shutil.copytree(
      ROOT / 'qaboard' / 'sample_project', cls.project,
      ignore=shutil.ignore_patterns('output', 'cli_tests', '*.batches.yaml', '__pycache__', 'subproject'),
    )
    for name in ('a.jpg', 'b.jpg', 'dir/c.jpg'):
      (cls.project / 'inputs' / name).parent.mkdir(parents=True, exist_ok=True)
      (cls.project / 'inputs' / name).write_text(name)
    (cls.project / 'qa' / 'batches.yaml').write_text(yaml.dump({
      'images': {'inputs': ['inputs'], 'globs': ['*.jpg'], 'database': {'linux': str(cls.project), 'windows': str(cls.project)}},
      # inputs given as absolute paths: the database is the root of the path
      'absolute': {'inputs': [str(cls.project / 'inputs' / 'dir')], 'globs': ['*.jpg']},
    }))
    config = yaml.safe_load((cls.project / 'qaboard.yaml').read_text())
    config['storage'] = {'linux': str(Path(cls.tmp.name) / 'storage'), 'windows': str(Path(cls.tmp.name) / 'storage')}
    config['inputs']['database'] = {'linux': str(cls.project), 'windows': str(cls.project)}
    (cls.project / 'qaboard.yaml').write_text(yaml.dump(config))
    git = lambda *args: subprocess.run(['git', *args], cwd=cls.project, check=True, capture_output=True)
    git('init', '-q')
    git('-c', 'user.name=Test', '-c', 'user.email=test@example.com', 'commit', '-q', '--allow-empty', '-m', 'first commit')

  @classmethod
  def tearDownClass(cls):
    cls.tmp.cleanup()

  def qa(self, *args, **kwargs):
    return run_qa(self.project, *args, **kwargs)


class TestGlobal(QaProject):
  def test_version(self):
    from importlib.metadata import version, PackageNotFoundError
    try:
      expected = version('qaboard')
    except PackageNotFoundError:
      expected = 'unknown'
    self.assertEqual(self.qa('--version', check=0).stdout.strip(), expected)

  def test_version_is_not_read_from_the_environment(self):
    # QA_VERSION could be set for something else entirely...
    result = self.qa('get', 'batch_label', env={'QA_VERSION': '1'}, check=0)
    self.assertEqual(result.stdout.strip(), 'default')

  def test_help(self):
    result = self.qa('--help', check=0)
    for command in ('run', 'batch', 'postprocess', 'sync', 'wait', 'get', 'wizard', 'init', 'save-artifacts',
                    'check-bit-accuracy', 'check-bit-accuracy-manifest', 'optimize'):
      self.assertIn(command, result.stdout)
    # help doesn't complain about the configuration
    self.assertNotIn('WARNING', result.stderr)

  def test_help_shows_environment_variables(self):
    result = self.qa('batch', '--help', check=0)
    self.assertIn('QA_BATCH_RUNNER', result.stdout)
    self.assertIn('--lsf-queue', result.stdout)

  def test_every_command_has_help(self):
    for command in ('run', 'batch', 'postprocess', 'sync', 'wait', 'get', 'wizard', 'init', 'save-artifacts',
                    'check-bit-accuracy', 'check-bit-accuracy-manifest', 'optimize'):
      with self.subTest(command=command):
        result = self.qa(command, '--help', check=0)
        self.assertIn('Usage: qa', result.stdout)

  def test_unknown_command(self):
    result = self.qa('runn')
    self.assertEqual(result.returncode, 2)
    self.assertIn('runn', result.stderr)


class TestGet(QaProject):
  def test_summary_as_json(self):
    variables = json.loads(self.qa('get', check=0).stdout)
    self.assertEqual(variables['project'], 'user/sample_project')
    self.assertEqual(variables['batch_label'], 'default')
    self.assertEqual(len(variables['commit_id']), 40)
    self.assertIn('outputs_commit', variables)
    self.assertNotIn('output_dir', variables) # only with --input

  def test_variable(self):
    commit_id = self.qa('get', 'commit_id', check=0).stdout.strip()
    self.assertRegex(commit_id, r'^[0-9a-f]{40}$')

  def test_variable_as_json(self):
    configurations = json.loads(self.qa('-c', 'a', '-c', '{"b": 1}', 'get', 'configurations', '--json', check=0).stdout)
    self.assertEqual(configurations, ['a', {'b': 1}])

  def test_old_names(self):
    self.assertEqual(self.qa('get', 'commit_ci_dir', check=0).stdout, self.qa('get', 'outputs_commit', check=0).stdout)

  def test_run_variables(self):
    output_dir = self.qa('get', 'output_dir', '--input', 'inputs/a.jpg', check=0).stdout.strip()
    batch_conf_dir = self.qa('get', 'batch_conf_dir', check=0).stdout.strip()
    self.assertEqual(Path(output_dir), Path(batch_conf_dir) / 'inputs-a')

  def test_unknown_variable(self):
    result = self.qa('get', 'not_a_variable', check=1)
    self.assertEqual(result.stdout, '')
    self.assertIn('Could not find not_a_variable', result.stderr)

  def test_environment_variables(self):
    self.assertEqual(self.qa('get', 'batch_label', env={'QA_LABEL': 'from-env'}, check=0).stdout.strip(), 'from-env')
    # the command line wins
    self.assertEqual(self.qa('--label', 'from-cli', 'get', 'batch_label', env={'QA_LABEL': 'from-env'}, check=0).stdout.strip(), 'from-cli')

  def test_deprecated_flags(self):
    result = self.qa('--batch-label', 'old-flag', 'get', 'batch_label', check=0)
    self.assertEqual(result.stdout.strip().splitlines()[-1], 'old-flag')

  def test_tuning(self):
    tuning = ['--tuning', '{"a": 1, "nested": {"b": 2}}', '--tuning', '"config"', '--tuning', '["c1", "c2"]', '--tuning', '{"nested": {"c": 3}}']
    extra_parameters = json.loads(self.qa(*tuning, 'get', 'extra_parameters', '--json', check=0).stdout)
    self.assertEqual(extra_parameters, {'a': 1, 'nested': {'b': 2, 'c': 3}, '_configs': ['config', 'c1', 'c2']})


class TestRun(QaProject):
  def test_run(self):
    result = self.qa('run', '--input', 'inputs/a.jpg', 'echo "{input_path} => {output_dir}"', check=0)
    self.assertIn('"is_failed": false', result.stdout)
    self.assertIn('a.jpg =>', result.stdout)
    # diagnostics are on stderr
    self.assertIn('Outputs:', result.stderr)
    output_dir = Path(self.qa('get', 'output_dir', '-i', 'inputs/a.jpg', check=0).stdout.strip())
    metrics = json.loads((output_dir / 'metrics.json').read_text())
    self.assertFalse(metrics['is_failed'])
    self.assertIn('compute_time', metrics)
    for name in ('run.json', 'log.txt', 'manifest.inputs.json', 'manifest.outputs.json'):
      self.assertTrue((output_dir / name).exists(), name)

  def test_metrics_are_printed_as_json(self):
    # even with colors (the default), the metrics have none
    result = self.qa('run', '--input', 'inputs/a.jpg', 'true', check=0)
    metrics = json.loads(result.stdout.splitlines()[-1])
    self.assertFalse(metrics['is_failed'])
    self.assertIn('compute_time', metrics)

  def test_failed_run_exits_with_1(self):
    result = self.qa('run', '-i', 'inputs/a.jpg', 'false', env={'NO_COLOR': '1'}, check=1)
    self.assertIn('The run has failed', result.stderr)
    self.assertTrue(json.loads(result.stdout.splitlines()[-1])['is_failed'])

  def test_missing_input(self):
    result = self.qa('run', '-i', 'inputs/missing.jpg', 'true', check=1)
    self.assertIn('cannot be found', result.stderr)

  def test_input_is_required(self):
    result = self.qa('run', 'true', check=2)
    self.assertIn('--input', result.stderr)

  def test_forwarded_args(self):
    # after --, everything goes to the user's code, even flags qa knows
    result = self.qa('run', '-i', 'inputs/a.jpg', '--', 'echo', '--keep-previous', '--flag', check=0)
    self.assertIn('--keep-previous --flag', result.stdout)
    # without --, unknown flags too
    result = self.qa('run', '-i', 'inputs/a.jpg', 'echo', '--flag', check=0)
    self.assertIn('--flag', result.stdout)

  def test_configurations_and_params(self):
    result = self.qa('-c', 'base', '-c', '{"threshold": 3}', '--tuning', '{"gain": 2}', 'run', '-i', 'inputs/a.jpg', 'echo "params={params}"', check=0)
    self.assertIn("'threshold': 3", result.stdout)
    self.assertIn("'gain': 2", result.stdout)
    self.assertIn(".configs:  ['base', {'threshold': 3}", result.stdout)

  def test_postprocess_and_sync(self):
    self.qa('run', '-i', 'inputs/b.jpg', 'true', check=0)
    result = self.qa('postprocess', '-i', 'inputs/b.jpg', check=0)
    self.assertIn('"is_failed": false', result.stdout)
    result = self.qa('sync', '-i', 'inputs/b.jpg', check=0)
    self.assertIn('"is_failed": false', result.stdout)

  def test_colors(self):
    # Colors are kept by default (logs are shown with colors in QA-Board)
    self.assertRegex(self.qa('run', '-i', 'inputs/a.jpg', 'true', check=0).stdout, ANSI)
    # https://no-color.org
    result = self.qa('run', '-i', 'inputs/a.jpg', 'true', env={'NO_COLOR': '1'}, check=0)
    self.assertNotRegex(result.stdout, ANSI)
    self.assertNotRegex(result.stderr, ANSI)


class TestClickEntrypoint(QaProject):
  """Entrypoints written when qa used click keep working."""
  @classmethod
  def setUpClass(cls):
    super().setUpClass()
    (cls.project / 'qa' / 'main.py').write_text(
      "import click\n"
      "def run(context):\n"
      "  click.secho('from click', fg='green')\n"
      "  obj = click.get_current_context().obj\n"
      "  click.echo(f\"label={obj['batch_label']} same_obj={obj is context.obj}\")\n"
      "  return {'is_failed': False}\n"
      "def postprocess(metrics, context):\n"
      "  click.echo('postprocess label=' + click.get_current_context().obj['batch_label'])\n"
      "  return metrics\n"
    )
    # click imported inside the functions
    (cls.project / 'qa' / 'lazy.py').write_text(
      "def run(context):\n"
      "  import click\n"
      "  click.echo('lazy label=' + click.get_current_context().obj['batch_label'])\n"
      "  return {'is_failed': False}\n"
    )

  def test_click_entrypoint(self):
    result = self.qa('--label', 'my-label', 'run', '-i', 'inputs/a.jpg', check=0)
    self.assertIn('label=my-label same_obj=True', result.stdout)
    self.assertIn('postprocess label=my-label', result.stdout)
    # like before, colors are kept in logs, even if stdout is not a terminal
    self.assertIn('\x1b[32mfrom click', result.stdout)

  def test_click_imported_in_functions(self):
    config = yaml.safe_load((self.project / 'qaboard.yaml').read_text())
    config['project']['entrypoint'] = 'qa/lazy.py'
    (self.project / 'qaboard.yaml').write_text(yaml.dump(config))
    try:
      result = self.qa('--label', 'my-label', 'run', '-i', 'inputs/a.jpg', check=0)
      self.assertIn('lazy label=my-label', result.stdout)
    finally:
      config['project']['entrypoint'] = 'qa/main.py'
      (self.project / 'qaboard.yaml').write_text(yaml.dump(config))


class TestFailedPostprocess(QaProject):
  @classmethod
  def setUpClass(cls):
    super().setUpClass()
    (cls.project / 'qa' / 'main.py').write_text(
      "def run(context):\n"
      "  return {'is_failed': False}\n"
      "def postprocess(metrics, context):\n"
      "  return {**metrics, 'is_failed': True, 'reason': 'postprocess'}\n"
    )

  def test_postprocess_exits_with_1(self):
    self.qa('run', '-i', 'inputs/a.jpg', '--no-postprocess', check=0)
    result = self.qa('postprocess', '-i', 'inputs/a.jpg', env={'NO_COLOR': '1'}, check=1)
    self.assertEqual(json.loads(result.stdout.splitlines()[-1])['reason'], 'postprocess')
    self.qa('run', '-i', 'inputs/a.jpg', check=1)


class TestAssertExists(QaProject):
  def test_assert_exists(self):
    # nothing ran yet: all the missing runs are listed
    result = self.qa('batch', 'images', '--action-on-existing', 'assert-exists', check=1)
    self.assertIn("3 runs can't be found", result.stderr)
    for name in ('a.jpg', 'b.jpg', 'c.jpg'):
      self.assertIn(name, result.stderr)
    # once they exist it passes, and runs nothing
    self.qa('batch', 'images', '--runner', 'local', '--', 'true', check=0)
    result = self.qa('batch', 'images', '--action-on-existing', 'assert-exists', '--', 'false', check=0)
    self.assertIn('All the runs exist (3)', result.stderr)
    self.assertNotIn(' run --input', result.stderr)
    self.assertEqual(len(self.qa('batch', 'images', '--action-on-existing', 'assert-exists', '--list-output-dirs', check=0).stdout.splitlines()), 3)


class TestBatch(QaProject):
  def test_list(self):
    runs = json.loads(self.qa('batch', 'images', '--list', check=0).stdout)
    self.assertEqual(sorted(Path(r['rel_input_path']).name for r in runs), ['a.jpg', 'b.jpg', 'c.jpg'])
    self.assertEqual({r['batch'] for r in runs}, {'images'})
    for run in runs:
      self.assertIn('qa', run['command'])

  def test_list_inputs_and_output_dirs(self):
    inputs = self.qa('batch', 'images', '--list-inputs', check=0).stdout.splitlines()
    self.assertEqual(len(inputs), 3)
    output_dirs = self.qa('batch', '--batch', 'images', '--list-output-dirs', check=0).stdout.splitlines()
    self.assertEqual(len(output_dirs), 3)

  def test_absolute_input_paths(self):
    runs = json.loads(self.qa('batch', 'absolute', '--list', check=0).stdout)
    self.assertEqual(len(runs), 1)
    self.assertEqual(runs[0]['input_path'], str(self.project / 'inputs' / 'dir' / 'c.jpg'))
    self.assertEqual(runs[0]['database'], self.project.anchor)
    self.qa('run', '--input', str(self.project / 'inputs' / 'a.jpg'), 'true', check=0)

  def test_forwarded_args(self):
    for args in (['--', '--my-flag', 'value'], ['--my-flag', 'value']):
      with self.subTest(args=args):
        runs = json.loads(self.qa('batch', 'images', '--list', *args, check=0).stdout)
        self.assertEqual(runs[0]['extra_parameters']['forwarded_args'], ['--my-flag', 'value'])
        self.assertIn("--my-flag value", runs[0]['command'])

  def test_runner_options(self):
    run = json.loads(self.qa('batch', 'images', '--runner', 'lsf', '--lsf-queue', 'from-cli', '--list', check=0).stdout)[0]
    self.assertEqual(run['job_options']['type'], 'lsf')
    self.assertEqual(run['job_options']['queue'], 'from-cli')
    run = json.loads(self.qa('batch', 'images', '--list', env={'QA_BATCH_RUNNER': 'lsf', 'QA_BATCH_LSF_QUEUE': 'from-env'}, check=0).stdout)[0]
    self.assertEqual(run['job_options']['queue'], 'from-env')

  def test_configurations_in_commands(self):
    run = json.loads(self.qa('-c', '{"a": 1}', '--label', 'my label', 'batch', 'images', '--list', check=0).stdout)[0]
    self.assertIn('--configuration \'{"a": 1}\'', run['command'])
    self.assertIn('--label "my label"', run['command'])

  def test_tuning_search(self):
    runs = json.loads(self.qa('batch', 'images', '--tuning-search', '{"search_type": "grid", "parameter_search": {"gain": [1, 2]}}', '--list', check=0).stdout)
    self.assertEqual(len(runs), 6)
    self.assertEqual(sorted({r['extra_parameters']['gain'] for r in runs}), [1, 2])

  def test_invalid_choices(self):
    result = self.qa('batch', 'images', '--runner', 'not-a-runner', '--list', check=2)
    self.assertIn("'not-a-runner' is not one of", result.stderr)
    self.qa('batch', 'images', '--list', env={'QA_BATCH_ACTION_ON_EXISTING': 'nope'}, check=2)
    self.qa('batch', 'images', '--action-on-pending', 'sync', '--action-on-existing', 'assert-exists', '--list-inputs', check=0)
    self.qa('batch', 'images', '--runner', 'lsf', '--lsf-max-memory', '16G', '--list', check=2)

  @unittest.skipUnless((ROOT / 'backend' / 'backend' / 'shell_utils.py').exists(), 'needs the backend sources')
  def test_redo_command_from_the_server(self):
    """The command QA-Board's server runs to redo an output (backend/backend/models/Output.py) runs it, and only it."""
    import importlib.util
    spec = importlib.util.spec_from_file_location('shell_utils', ROOT / 'backend' / 'backend' / 'shell_utils.py')
    shell_utils = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(shell_utils)
    command = shell_utils.qa_redo_command(
      label='redo', configuration='base', database=self.project, output_type='default',
      extra_parameters='{"gain": 2}', input_path='inputs/a.jpg',
      job_options={'type': 'lsf', 'queue': 'q', 'max_memory': 1000, 'max_threads': 4},
    )
    assert command.startswith('qa ')
    command = f"{sys.executable} -m qaboard {command[3:]} --list"
    env = {k: v for k, v in os.environ.items() if k not in CI_VARIABLES and not k.startswith('QA_')}
    env.update({'PYTHONPATH': str(ROOT), 'QA_NO_CHECK_FOR_UPDATES': '1', 'QA_OFFLINE': 'true', 'QABOARD_HOST': 'localhost'})
    result = subprocess.run(['bash', '-c', command], cwd=self.project, env=env, capture_output=True, text=True)
    self.assertEqual(result.returncode, 0, result.stderr)
    runs = json.loads(result.stdout)
    self.assertEqual([r['rel_input_path'] for r in runs], ['inputs/a.jpg'])
    self.assertEqual(runs[0]['configurations'], ['base'])
    self.assertEqual(runs[0]['extra_parameters'], {'gain': 2})

  def test_batch_is_required(self):
    result = self.qa('batch', check=1)
    self.assertIn('you must provide a batch', result.stderr)

  def test_unknown_batch(self):
    # Only a warning by default...
    result = self.qa('batch', 'not-a-batch', '--list', check=0)
    self.assertIn('No inputs found for the batch "not-a-batch"', result.stderr)
    self.assertEqual(json.loads(result.stdout), [])
    self.qa('batch', 'not-a-batch', '--list', env={'QA_BATCH_FAIL_IF_EMPTY': '1'}, check=1)

  def test_local_runner(self):
    # Arguments for the code go after -- (otherwise they would be batch names)
    self.qa('batch', 'images', '--runner', 'local', '--', 'false', check=1)
    self.qa('batch', 'images', '--runner', 'local', '--', 'true', check=0)
    # successful runs are not run again with --action-on-existing=skip
    result = self.qa('batch', 'images', '--runner', 'local', '--action-on-existing', 'skip', '--', 'false', check=0)
    self.assertNotIn(' run ', result.stderr)


class TestInit(unittest.TestCase):
  def test_init(self):
    with tempfile.TemporaryDirectory() as tmp:
      subprocess.run(['git', 'init', '-q'], cwd=tmp, check=True)
      # a remote that can't be reached: qa init still finds the project's name in its URL
      subprocess.run(['git', 'remote', 'add', 'origin', 'https://example.invalid/someone/my-project.git'], cwd=tmp, check=True)
      run_qa(tmp, 'init', '--yes', check=0)
      self.assertTrue((Path(tmp) / 'qaboard.yaml').exists())
      self.assertTrue((Path(tmp) / 'qa' / 'main.py').exists())
      self.assertEqual(run_qa(tmp, 'get', 'project', check=0).stdout.strip(), 'someone/my-project')
      # Run again, the wizard checks the project's health
      result = run_qa(tmp, 'wizard', '--yes', check=0)
      self.assertIn('still the template', result.stderr)


class TestRunners(unittest.TestCase):
  def test_runner_choices(self):
    # --runner accepts exactly the available runners
    check = (
      "from typing import get_args\n"
      "from qaboard.cli.options import Runner\n"
      "from qaboard.runners import runners\n"
      "assert set(get_args(Runner)) == set(runners), (get_args(Runner), list(runners))\n"
    )
    env = {**os.environ, 'PYTHONPATH': str(ROOT), 'QA_NO_CHECK_FOR_UPDATES': '1', 'QABOARD_HOST': 'localhost'}
    out = subprocess.run([sys.executable, '-c', check], cwd=ROOT / 'qaboard' / 'sample_project', env=env, capture_output=True, text=True)
    self.assertEqual(out.returncode, 0, out.stderr)


# In a new process: importing qaboard here would load its configuration from the wrong directory for other tests
WAIT = """
import sys
from unittest import mock
from typer.testing import CliRunner
outputs = iter([{"is_pending": True}, {"is_pending": False, "is_failed": %s}])
with mock.patch('qaboard.api.get_output', lambda id: next(outputs)), mock.patch('time.sleep'):
  from qaboard.cli import app
  result = CliRunner().invoke(app, ['wait', '--output-id', '123'], obj={})
# a crash would also exit with 1
if result.exception and not isinstance(result.exception, SystemExit):
  raise result.exception
sys.exit(result.exit_code)
"""

class TestWait(unittest.TestCase):
  def wait(self, is_failed):
    env = {**os.environ, 'PYTHONPATH': str(ROOT), 'QA_NO_CHECK_FOR_UPDATES': '1', 'QABOARD_HOST': 'localhost'}
    return subprocess.run([sys.executable, '-c', WAIT % is_failed], cwd=ROOT / 'qaboard' / 'sample_project', env=env, capture_output=True, text=True)

  def test_exit_code_of_the_run(self):
    # `qa batch` runs "qa wait || qa run": failed runs are run again after waiting
    self.assertEqual(self.wait(False).returncode, 0)
    self.assertEqual(self.wait(True).returncode, 1)


if __name__ == '__main__':
  unittest.main()
