"""
Helpers to build shell commands from data we don't control.

Most values we interpolate in shell scripts (batch labels, paths, configurations,
user names...) come from the database, where they were written by unauthenticated
API calls (e.g. `POST /api/v1/output`). They must always be treated as untrusted.

- In scripts we write ourselves, use `quote()` on every interpolated value.
- For `QA_RUNNERS_LSF_BRIDGE`, we don't know how many shells will re-parse the
  values (e.g. `ssh host 'bsub_su {user} {bsub_command}'` is parsed locally then remotely),
  so no quoting is reliable: we only accept values that need no quoting at all.
"""
import os
import re
from shlex import quote


def is_shell_safe(value) -> bool:
  """True if the value can be used as-is as a single word, in any number of nested shells."""
  value = str(value)
  return bool(value) and quote(value) == value


def shell_safe(value, what="value") -> str:
  value = str(value)
  if not is_shell_safe(value):
    raise ValueError(f"Refusing to use {what} in a shell command, it contains unsafe characters: {value!r}")
  return value


# Unix-like user names. We are stricter than shell_safe since we also use them in paths.
_user_name_re = re.compile(r'^[A-Za-z0-9_][A-Za-z0-9_.-]{0,63}$')

def safe_user_name(user_name) -> str:
  user_name = str(user_name or '')
  if not _user_name_re.match(user_name):
    raise ValueError(f"Invalid user name: {user_name!r}")
  return user_name


def lsf_bridge_command(user: str, script_path) -> str:
  """
  Returns the command that runs `bash script_path` as `user`, via $QA_RUNNERS_LSF_BRIDGE if defined.
  Both values are validated so that they are safe whatever the quoting used in the bridge template.
  """
  user = shell_safe(safe_user_name(user), "user")
  command = f"bash {shell_safe(script_path, 'script path')}"
  lsf_bridge = os.environ.get('QA_RUNNERS_LSF_BRIDGE', '')
  if not lsf_bridge:
    return command
  return (lsf_bridge
    .replace('{user}', user)
    .replace('{bsub_command}', command)
    .replace('{command}', command))


def qa_batch_option(batch) -> str:
  """
  `--batch=NAME` for `qa batch`, as one shell word. Unlike a positional BATCH, it can't be taken for an option,
  even if it starts with "-". (`qa ... -- NAME` doesn't work: what comes after "--" is given to the user's code.)
  """
  return '--batch=' + quote(str(batch))


def qa_redo_command(label, configuration, database, output_type, extra_parameters: str, input_path, job_options) -> str:
  """The `qa batch` command that runs a single output again (see Output.redo)."""
  job_options_cli = []
  if not job_options:
    # for backward compatibility, it's a good defaut at SIRC
    job_options_cli = ["--lsf-max-memory", "20000"]
  elif job_options.get('type') == "lsf":
    # TODO: support other runners... maybe create an ad-hoc functions in their classes...
    if 'queue' in job_options:
      job_options_cli += ["--lsf-queue", quote(str(job_options['queue']))]
    if 'max_memory' in job_options and job_options['max_memory'] != 0:
      job_options_cli += ["--lsf-max-memory", quote(str(job_options['max_memory']))]
    if 'resources' in job_options and job_options['resources']:
      job_options_cli += ["--lsf-resources", quote(str(job_options['resources']))]
    if 'max_threads' in job_options and job_options['max_threads'] != 0:
      job_options_cli += ["--lsf-max-threads", quote(str(job_options['max_threads']))]
  return ' '.join([
    'qa',
    '--label', quote(str(label)),
    '--configuration', quote(str(configuration)),
    '--database', quote(str(database)),
    '--type', quote(str(output_type)),
    '--tuning', quote(extra_parameters),
    'batch',
    '--no-wait',
    *job_options_cli,
    '--action-on-existing=run',
    '--action-on-pending=run',
    qa_batch_option(input_path),
    # FIXME: if forwarded_args in parsed(self.configuration), add it..
  ])
