"""
Tests for `qa init`'s wizard: the change harness, project detection, settings, the AI agent (with a scripted model) and the whole flow.
"""
import io
import os
import stat
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest import mock

import yaml
from rich.console import Console

from qaboard.wizard import run_wizard, Wizard, safe_split, try_run_input
from qaboard.wizard.changes import ChangeSet, UnsafePath, safe_path, is_secret, visible
from qaboard.wizard.detect import detect, guess_inputs, project_name_from_url, without_credentials
from qaboard.wizard.settings import LLM, save_user_settings, suggest_model
from qaboard.wizard.ui import UI

try:
  import pydantic_ai  # noqa: F401
  has_ai = True
except ImportError:
  has_ai = False


def git(cwd, *args):
  env = {**os.environ, 'GIT_AUTHOR_NAME': 'a', 'GIT_AUTHOR_EMAIL': 'a@a', 'GIT_COMMITTER_NAME': 'a', 'GIT_COMMITTER_EMAIL': 'a@a'}
  subprocess.run(['git', *args], cwd=cwd, check=True, capture_output=True, env=env)


def quiet_ui(interactive=False) -> UI:
  return UI(interactive=interactive, console=Console(file=io.StringIO(), width=120))


class TempDir(unittest.TestCase):
  def setUp(self):
    self._tmp = tempfile.TemporaryDirectory()
    self.root = Path(self._tmp.name).resolve()

  def tearDown(self):
    self._tmp.cleanup()


class TestPaths(TempDir):
  def test_safe_paths(self):
    self.assertEqual(safe_path(self.root, 'qa/main.py', for_write=True), self.root / 'qa' / 'main.py')
    self.assertEqual(safe_path(self.root, 'src/../qaboard.yaml', for_write=True), self.root / 'qaboard.yaml')
    for path in ('/etc/passwd', '../outside', 'qa/../../outside', '.git/config', '.GIT/config', 'C:/Windows', ''):
      with self.assertRaises(UnsafePath, msg=path):
        safe_path(self.root, path)

  def test_only_qa_and_qaboard_yaml_are_writable(self):
    safe_path(self.root, 'src/main.py')
    for path in ('src/main.py', 'qa', 'README.md', '.gitlab-ci.yml', 'qaboard.yaml.bak'):
      with self.assertRaises(UnsafePath, msg=path):
        safe_path(self.root, path, for_write=True)

  def test_symlinks_out_of_the_project(self):
    with tempfile.TemporaryDirectory() as outside:
      (self.root / 'qa').symlink_to(outside)
      with self.assertRaises(UnsafePath):
        safe_path(self.root, 'qa/main.py', for_write=True)

  def test_secrets(self):
    for name in ('.env', '.env.local', 'prod.env', 'id_rsa', 'id_ed25519.pub', 'server.pem', 'secrets.yaml', 'client_secret.json',
                 'credentials.json', '.netrc', 'token.txt', 'gitlab_token.json'):
      self.assertTrue(is_secret(Path(name)), name)
    for name in ('secrets/prod.yaml', '.ssh/config', '.aws/config', '.kube/config', 'deploy/.docker/config.json', '.pgpass'):
      self.assertTrue(is_secret(Path(name)), name)
    for name in ('.env.example', 'main.py', 'tokenizer.py', 'password_strength.cpp', 'keyboard.py', 'README.md', 'secret_sauce.example', 'src/keys/map.py'):
      self.assertFalse(is_secret(Path(name)), name)
    (self.root / '.env').write_text('KEY=1')
    with self.assertRaises(UnsafePath):
      ChangeSet(self.root).read('.env')


class TestChangeSet(TempDir):
  def test_stage_diff_apply(self):
    (self.root / 'qaboard.yaml').write_text('a: 1\n')
    changes = ChangeSet(self.root)
    changes.stage('qaboard.yaml', 'a: 2')
    changes.stage('qa/main.py', 'print(1)\n')
    self.assertFalse((self.root / 'qa').exists(), "nothing is written before apply")
    self.assertEqual(changes.read('qaboard.yaml'), 'a: 2\n')
    diff = changes.diff(changes.changes['qaboard.yaml'])
    self.assertIn('-a: 1', diff)
    self.assertIn('+a: 2', diff)
    self.assertEqual(changes.stats(changes.changes['qa/main.py']), (1, 0))
    changes.apply()
    self.assertEqual((self.root / 'qaboard.yaml').read_text(), 'a: 2\n')
    self.assertEqual((self.root / 'qa' / 'main.py').read_text(), 'print(1)\n')

  def test_unchanged_files_are_not_pending(self):
    (self.root / 'qaboard.yaml').write_text('a: 1\n')
    changes = ChangeSet(self.root)
    changes.stage('qaboard.yaml', 'a: 1\n')
    self.assertEqual(changes.pending(), [])

  def test_rollback(self):
    (self.root / 'qaboard.yaml').write_text('original\n')
    changes = ChangeSet(self.root)
    changes.stage('qa/sub/a.py', 'a = 1\n')
    changes.stage('qaboard.yaml', 'new\n')
    from qaboard.wizard import changes as changes_module
    real_write = changes_module.atomic_write
    def failing_write(path, content):
      if path.name == 'qaboard.yaml' and content == 'new\n':
        raise OSError("disk full")
      real_write(path, content)
    with mock.patch.object(changes_module, 'atomic_write', failing_write):
      with self.assertRaises(OSError):
        changes.apply()
    self.assertEqual((self.root / 'qaboard.yaml').read_text(), 'original\n')
    self.assertFalse((self.root / 'qa').exists(), "created files and folders are removed")

  def test_refuses_files_changed_meanwhile(self):
    (self.root / 'qaboard.yaml').write_text('a: 1\n')
    changes = ChangeSet(self.root)
    changes.stage('qaboard.yaml', 'a: 2\n')
    (self.root / 'qaboard.yaml').write_text('a: 3\n')
    with self.assertRaises(RuntimeError):
      changes.apply()
    self.assertEqual((self.root / 'qaboard.yaml').read_text(), 'a: 3\n')


class TestDetect(TempDir):
  def test_project_name_from_url(self):
    for url in ('git@github.com:acme/denoiser.git', 'https://github.com/acme/denoiser.git', 'https://github.com/acme/denoiser/',
                'ssh://git@gitlab.example.com:2222/acme/denoiser.git', 'https://user@gitlab.example.com/acme/denoiser'):
      self.assertEqual(project_name_from_url(url), 'acme/denoiser', url)
    self.assertEqual(project_name_from_url('git@gitlab-srv:group/sub/project.git'), 'group/sub/project')

  def test_without_credentials(self):
    self.assertEqual(without_credentials('https://oauth2:TOKEN@gitlab.example.com/g/p.git'), 'https://gitlab.example.com/g/p.git')
    self.assertEqual(without_credentials('https://TOKEN@github.com/g/p'), 'https://github.com/g/p')
    for url in ('git@github.com:g/p.git', 'ssh://git@gitlab.example.com:2222/g/p.git', 'https://github.com/g/p'):
      self.assertEqual(without_credentials(url), url)

  def test_detect(self):
    git(self.root, 'init', '-q', '-b', 'main')
    git(self.root, 'remote', 'add', 'origin', 'git@example.com:acme/denoiser.git')
    (self.root / 'src').mkdir()
    for name in ('a.cpp', 'b.cpp', 'c.h', 'tool.py'):
      (self.root / 'src' / name).write_text('')
    (self.root / 'CMakeLists.txt').write_text('')
    (self.root / '.gitlab-ci.yml').write_text('')
    (self.root / 'node_modules').mkdir()
    (self.root / 'node_modules' / 'x.js').write_text('')
    git(self.root, 'add', '.')
    git(self.root, 'commit', '-q', '-m', 'first')
    (self.root / 'new.py').write_text('')
    facts = detect(self.root)
    self.assertTrue(facts.is_git)
    self.assertEqual(facts.project_name, 'acme/denoiser')
    self.assertEqual(facts.reference_branch, 'main')
    self.assertEqual(facts.languages[0], 'C++')
    self.assertIn('CMake', facts.build_systems)
    self.assertEqual(facts.ci, ['GitLab CI'])
    self.assertEqual(facts.dirty_files, ['new.py'])
    self.assertIn('new.py', facts.files)
    self.assertFalse(any('node_modules' in f for f in facts.files))

  def test_detect_without_git(self):
    (self.root / 'main.m').write_text('')
    facts = detect(self.root)
    self.assertFalse(facts.is_git)
    self.assertEqual(facts.languages, ['MATLAB'])

  def test_guess_inputs(self):
    for name in ('a/1.png', 'a/2.png', 'b/3.png', 'notes.txt', 'x.jpg'):
      (self.root / name).parent.mkdir(parents=True, exist_ok=True)
      (self.root / name).write_text('')
    glob, examples = guess_inputs(self.root)
    self.assertEqual(glob, '*.png')
    self.assertEqual(examples, ['a/1.png', 'a/2.png', 'b/3.png'])


class TestSettings(TempDir):
  def test_save_user_settings(self):
    path = self.root / 'home' / 'secrets.yaml'
    with mock.patch.dict(os.environ, {'QA_USER_SECRETS': str(path)}):
      save_user_settings({'QA_TOKEN': 'abc'})
      save_user_settings({'QABOARD_LLM_API_KEY': 'xyz'})
    self.assertEqual(yaml.safe_load(path.read_text()), {'QA_TOKEN': 'abc', 'QABOARD_LLM_API_KEY': 'xyz'})
    if os.name != 'nt':
      self.assertEqual(stat.S_IMODE(path.stat().st_mode), 0o600)

  def test_openai_key_only_goes_to_openai(self):
    env = {'OPENAI_API_KEY': 'sk-openai', 'QABOARD_LLM_API_KEY': '', 'QABOARD_LLM_MODEL': ''}
    with mock.patch.dict(os.environ, {**env, 'QABOARD_LLM_BASE_URL': 'https://gateway.example.com/v1'}):
      llm = LLM.from_settings()
    self.assertEqual(llm.host, 'gateway.example.com')
    self.assertFalse(llm.api_key)
    with mock.patch.dict(os.environ, {**env, 'QABOARD_LLM_BASE_URL': ''}):
      llm = LLM.from_settings(model='gpt-test')
    self.assertTrue(llm.is_openai)
    self.assertEqual(llm.api_key, 'sk-openai')
    self.assertEqual(llm.model, 'gpt-test')

  def test_suggest_model(self):
    self.assertEqual(suggest_model(['text-embedding-3', 'gpt-4o-mini', 'gpt-4o', 'whisper-1']), 'gpt-4o')
    self.assertEqual(suggest_model(['llama3.1:8b', 'qwen2.5-coder:32b']), 'qwen2.5-coder:32b')
    self.assertIsNone(suggest_model([]))

  def test_try_run_input(self):
    self.assertEqual(try_run_input("qa run --input 'my file.png'"), 'my file.png')
    self.assertEqual(try_run_input("qa run -i a.png"), 'a.png')
    for command in ("qa --label '$(touch x)' batch b", "qa run --input a.png --runner local", "rm -rf /", "qa run --input --help", None):
      self.assertIsNone(try_run_input(command), command)

  def test_visible(self):
    self.assertEqual(visible("ok\x1b[2K\u202e\n"), "ok\\x1b[2K\\u202e\n")

  def test_safe_split(self):
    self.assertEqual(safe_split("qa run --input 'my file.png'"), ['qa', 'run', '--input', 'my file.png'])
    self.assertEqual(safe_split('qa run "unclosed'), [])


def make_project(root: Path):
  git(root, 'init', '-q', '-b', 'main')
  git(root, 'remote', 'add', 'origin', 'https://example.invalid/acme/denoiser.git')
  (root / 'denoise.py').write_text("import argparse\nparser = argparse.ArgumentParser()\nparser.add_argument('--input')\n")
  (root / '.env').write_text("API_KEY=do-not-leak hunter2\n")


class TestWizard(TempDir):
  env = {'QABOARD_HOST': 'localhost:1', 'QABOARD_SITE_CONFIG': '', 'QA_USER_SECRETS': '/nonexistent/secrets.yaml'}

  def run_wizard(self, **kwargs):
    with mock.patch.dict(os.environ, self.env), mock.patch('qaboard.wizard.UI', lambda interactive: quiet_ui(interactive)):
      return run_wizard(root=self.root, assume_yes=True, **kwargs)

  def test_template(self):
    make_project(self.root)
    self.assertEqual(self.run_wizard(ai=False), 0)
    config = yaml.safe_load((self.root / 'qaboard.yaml').read_text())
    self.assertEqual(config['project']['name'], 'acme/denoiser')
    self.assertEqual(config['project']['reference_branch'], 'main')
    for name in ('main.py', 'batches.yaml', 'metrics.yaml'):
      self.assertTrue((self.root / 'qa' / name).exists())
    self.assertFalse(stat.S_IMODE((self.root / 'qaboard.yaml').stat().st_mode) & 0o002, "not world-writable")

  def test_dryrun(self):
    make_project(self.root)
    self.assertEqual(self.run_wizard(ai=False, dryrun=True), 0)
    self.assertFalse((self.root / 'qaboard.yaml').exists())
    self.assertFalse((self.root / 'qa').exists())

  def test_keeps_existing_files(self):
    make_project(self.root)
    (self.root / 'qa').mkdir()
    (self.root / 'qa' / 'main.py').write_text('# mine\n')
    self.run_wizard(ai=False)
    self.assertEqual((self.root / 'qa' / 'main.py').read_text(), '# mine\n')
    self.assertTrue((self.root / 'qa' / 'batches.yaml').exists())

  def test_existing_config(self):
    (self.root / 'qaboard.yaml').write_text('project: {name: x}\n')
    self.assertEqual(self.run_wizard(ai=False), 0)
    self.assertEqual((self.root / 'qaboard.yaml').read_text(), 'project: {name: x}\n')

  def test_inputs_answers(self):
    make_project(self.root)
    wizard = Wizard(quiet_ui(), dryrun=True, ai=False, model=None, root=self.root)
    wizard.answers = {'name': 'acme/x', 'reference_branch': 'main', 'url': None, 'database': '/data/my inputs', 'glob': '*.png', 'examples': ['a/1.png']}
    with mock.patch.dict(os.environ, self.env):
      changes = wizard.template_changes(detect(self.root))
    config = yaml.safe_load(changes.read('qaboard.yaml'))
    self.assertEqual(config['inputs']['globs'], '*.png')
    self.assertEqual(config['inputs']['database']['linux' if os.name != 'nt' else 'windows'], '/data/my inputs')
    self.assertEqual(yaml.safe_load(changes.read('qa/batches.yaml'))['my-batch']['inputs'], ['a/1.png'])


@unittest.skipUnless(has_ai, "needs pip install qaboard[wizard]")
class TestAgent(TempDir):
  """The agent's tools, driven by a scripted model instead of an LLM."""

  def scripted_model(self, steps):
    """Each step returns the tool calls of one model response. We record what the tools answered."""
    from pydantic_ai.messages import ModelResponse, ToolCallPart, ModelRequest
    from pydantic_ai.models.function import FunctionModel
    self.tool_results = []
    def model(messages, info):
      last = messages[-1]
      if isinstance(last, ModelRequest):
        self.tool_results.extend(str(getattr(p, 'content', '')) for p in last.parts if p.part_kind in ('tool-return', 'retry-prompt'))
      index = sum(isinstance(m, ModelResponse) for m in messages)
      if index < len(steps):
        return ModelResponse(parts=[ToolCallPart(name, args) for name, args in steps[index]])
      final = {'summary': ['Wired qa/main.py to denoise.py'], 'try_command': 'qa run --input a.png', 'todo': []}
      return ModelResponse(parts=[ToolCallPart(info.output_tools[0].name, final)])
    return FunctionModel(model)

  def run_agent(self, steps):
    from qaboard.wizard.agent import Deps, make_agent, run_agent
    make_project(self.root)
    (self.root / 'creds.py').write_text("KEY = 'ghp_" + "a" * 36 + "'\n")
    (self.root / 'key.txt').write_text("-----BEGIN RSA PRIVATE KEY-----\nMIIFAKEKEYBODY1\nMIIFAKEKEYBODY2\n-----END RSA PRIVATE KEY-----\n")
    (self.root / 'docs').mkdir()
    (self.root / 'docs' / 'notes.md').symlink_to(self.root / '.env')
    outside = Path(tempfile.mkdtemp())
    self.addCleanup(lambda: __import__('shutil').rmtree(outside))
    (outside / 'creds').write_text("outside-secret\n")
    (self.root / 'docs' / 'ref.txt').symlink_to(outside / 'creds')
    facts = detect(self.root)
    changes = ChangeSet(self.root)
    changes.stage('qa/main.py', "def run(context):\n  return {'is_failed': False}\n")
    deps = Deps(changes=changes, facts=facts, ui=quiet_ui())
    result = run_agent(make_agent(self.scripted_model(steps)), deps, "go")
    return result, changes

  def test_reads_and_edits(self):
    result, changes = self.run_agent([
      [('list_files', {'pattern': '*.py'})],
      [('read_file', {'path': 'qa/main.py'}), ('search', {'regex': 'argparse'})],
      [('edit_file', {'path': 'qa/main.py', 'old_text': "return {'is_failed': False}", 'new_text': "import subprocess\n  return {'is_failed': False}"})],
    ])
    self.assertIn('denoise.py', self.tool_results[0])
    self.assertNotIn('.env', self.tool_results[0].split())
    self.assertIn("denoise.py:1: import argparse", '\n'.join(self.tool_results))
    self.assertIn('import subprocess', changes.read('qa/main.py'))
    self.assertFalse((self.root / 'qa').exists(), "the agent only stages changes")
    self.assertEqual(result.output.try_command, 'qa run --input a.png')

  def test_guardrails(self):
    _, changes = self.run_agent([
      [('read_file', {'path': '.env'})],
      [('read_file', {'path': 'creds.py'})],
      [('write_file', {'path': 'denoise.py', 'content': 'hacked'})],
      [('write_file', {'path': 'qa/main.py', 'content': 'overwritten without reading'})],
      [('write_file', {'path': 'qa/new.py', 'content': 'def broken(:'})],
      [('write_file', {'path': 'qa/new.py', 'content': "TOKEN = 'sk-" + "b" * 30 + "'"})],
      [('search', {'regex': 'hunter2|outside-secret'})],
      [('read_file', {'path': 'key.txt', 'start_line': 3})],
      [('write_file', {'path': 'qa/new.py', 'content': "# hidden\x1b[2K\x1b[1A\nx = 1\n"})],
    ])
    results = '\n'.join(self.tool_results)
    self.assertIn('secrets', self.tool_results[0])
    self.assertNotIn('do-not-leak', results)
    self.assertIn('[REDACTED]', self.tool_results[1])
    self.assertNotIn('ghp_aaaa', results)
    self.assertIn('only writes', self.tool_results[2])
    self.assertIn('Read qa/main.py', self.tool_results[3])
    self.assertIn('syntax error', self.tool_results[4])
    self.assertIn('secret', self.tool_results[5])
    self.assertEqual(self.tool_results[6], 'No matches.', "search doesn't follow symlinks to secrets or out of the project")
    self.assertNotIn('MIIFAKEKEYBODY', self.tool_results[7])
    self.assertIn('control characters', self.tool_results[8])
    self.assertEqual(changes.read('denoise.py').strip()[:6], 'import')
    self.assertNotIn('qa/new.py', changes.changes)

  def test_wizard_with_ai(self):
    from qaboard.wizard import agent as agent_module
    model = self.scripted_model([
      [('read_file', {'path': 'qa/main.py'})],
      [('edit_file', {'path': 'qa/main.py', 'old_text': 'def run(context):', 'new_text': 'def run(context):\n  # runs denoise.py'})],
    ])
    make_project(self.root)
    env = {**TestWizard.env, 'QABOARD_LLM_BASE_URL': 'https://llm.example.com/v1', 'QABOARD_LLM_API_KEY': 'k', 'QABOARD_LLM_MODEL': 'm'}
    with mock.patch.dict(os.environ, env), \
         mock.patch('qaboard.wizard.UI', lambda interactive: quiet_ui(interactive)), \
         mock.patch.object(agent_module, 'build_model', lambda llm: model), \
         mock.patch.object(LLM, 'list_models', lambda self: (['m'], 'ok')):
      self.assertEqual(run_wizard(root=self.root, assume_yes=True, ai=True), 0)
    self.assertIn('# runs denoise.py', (self.root / 'qa' / 'main.py').read_text())
    self.assertTrue((self.root / 'qaboard.yaml').exists())


class FakeOpenAI:
  """An OpenAI-compatible server on localhost, that answers chat completions with scripted tool calls."""
  def __init__(self, steps):
    import json
    import threading
    from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
    self.requests = []
    fake = self

    class Handler(BaseHTTPRequestHandler):
      def log_message(self, *args):
        pass

      def reply(self, body):
        data = json.dumps(body).encode()
        self.send_response(200)
        self.send_header('Content-Type', 'application/json')
        self.send_header('Content-Length', str(len(data)))
        self.end_headers()
        self.wfile.write(data)

      def do_GET(self):
        fake.requests.append(('GET', self.path, self.headers.get('Authorization'), ''))
        self.reply({'object': 'list', 'data': [{'id': 'text-embedding-3', 'object': 'model'}, {'id': 'fake-coder', 'object': 'model'}]})

      def do_POST(self):
        raw = self.rfile.read(int(self.headers['Content-Length'])).decode()
        fake.requests.append(('POST', self.path, self.headers.get('Authorization'), raw))
        body = json.loads(raw)
        index = sum(m['role'] == 'assistant' for m in body['messages'])
        if index < len(steps):
          calls = [(name, args) for name, args in steps[index]]
        else:
          final = next(t['function']['name'] for t in body['tools'] if t['function']['name'] not in ('list_files', 'read_file', 'search', 'write_file', 'edit_file', 'ask_user'))
          calls = [(final, {'summary': ['Done'], 'try_command': None, 'todo': ['Check the metrics']})]
        tool_calls = [{'id': f'call_{index}_{i}', 'type': 'function', 'function': {'name': name, 'arguments': json.dumps(args)}} for i, (name, args) in enumerate(calls)]
        self.reply({
          'id': f'chatcmpl-{index}', 'object': 'chat.completion', 'created': 0, 'model': body['model'],
          'choices': [{'index': 0, 'message': {'role': 'assistant', 'content': None, 'tool_calls': tool_calls}, 'finish_reason': 'tool_calls'}],
          'usage': {'prompt_tokens': 100, 'completion_tokens': 10, 'total_tokens': 110},
        })

    self.server = ThreadingHTTPServer(('127.0.0.1', 0), Handler)
    self.url = f'http://127.0.0.1:{self.server.server_address[1]}/v1'
    threading.Thread(target=self.server.serve_forever, daemon=True).start()

  def close(self):
    self.server.shutdown()
    self.server.server_close()


@unittest.skipUnless(has_ai, "needs pip install qaboard[wizard]")
class TestOpenAICompatibleAPI(TempDir):
  def test_wizard_with_an_openai_compatible_api(self):
    make_project(self.root)
    fake = FakeOpenAI([
      [('list_files', {}), ('read_file', {'path': 'denoise.py'})],
      [('read_file', {'path': '.env'}), ('read_file', {'path': 'qa/main.py'})],
      [('edit_file', {'path': 'qa/main.py', 'old_text': 'def run(context):', 'new_text': 'def run(context):\n  # calls denoise.py'})],
    ])
    self.addCleanup(fake.close)
    # No model configured: the wizard picks one from the API's list
    env = {**TestWizard.env, 'QABOARD_LLM_BASE_URL': fake.url, 'QABOARD_LLM_API_KEY': 'test-key', 'QABOARD_LLM_MODEL': '', 'OPENAI_API_KEY': 'sk-must-not-be-sent'}
    with mock.patch.dict(os.environ, env), mock.patch('qaboard.wizard.UI', lambda interactive: quiet_ui(interactive)):
      self.assertEqual(run_wizard(root=self.root, assume_yes=True, ai=True), 0)
    self.assertIn('# calls denoise.py', (self.root / 'qa' / 'main.py').read_text())
    posts = [r for r in fake.requests if r[0] == 'POST']
    self.assertEqual(len(posts), 4)
    self.assertTrue(all(r[1] == '/v1/chat/completions' for r in posts))
    self.assertTrue(all(r[2] == 'Bearer test-key' for r in fake.requests))
    self.assertIn('"model": "fake-coder"', posts[0][3].replace('":"', '": "'))
    everything_sent = ''.join(r[3] for r in fake.requests)
    self.assertIn('argparse', everything_sent)
    self.assertNotIn('do-not-leak', everything_sent)
    self.assertNotIn('sk-must-not-be-sent', everything_sent)


if __name__ == '__main__':
  unittest.main()
