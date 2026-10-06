"""
APIs related to parameter tuning
"""
import re
import os
import sys
import json
import uuid
import datetime
import itertools
import subprocess
from shlex import quote
from html import escape
from pathlib import Path
from typing import Dict, Any

import yaml
from flask import request, jsonify, g
from sqlalchemy.orm.exc import NoResultFound
from sqlalchemy.orm.attributes import flag_modified

from qaboard.utils import merge
from qaboard.iterators import iter_inputs, resolve_aliases
from qaboard.conventions import deserialize_config, batches_files

from backend import app, db_session
from ..models import CiCommit, Project
from ..config import qaboard_data_shared_dir
from ..shell_utils import safe_user_name, lsf_bridge_command
from ..storage import check_storage_path, UnsafePathError
from ..artifacts import ArtifactsUnavailable
from .auth import login_required


def get_groups_path(project_id, name="extra-batches"):
    """
    Return the path of the file where we save the groups of tests we defined for a project.
    Creates it if it does not exist yet.
    """
    name = str(name)
    if not re.match(r'^[\w.@-]+$', name) or name.startswith('.'):
        raise ValueError(f"Invalid group file name: {name!r}")
    root = qaboard_data_shared_dir.resolve()
    path = (root / project_id / f"{name}.yml").resolve()
    if not path.is_relative_to(root):
        raise ValueError(f"Invalid project: {project_id!r}")
    if not path.exists():
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("w") as f:
            f.write("""# Docs:\n# https://samsung.github.io/qaboard/docs/batches-running-on-multiple-inputs""")
    return path



@app.route("/api/v1/tests/groups", methods=["GET", "POST"])
def groups():
    """
    Return or update the groups of tests we defined for a project.
    TODO: We could just make it part of the database, why bother with files...
          It could be saved as test as project.data.test_groups
          We would *just* need to write the migration, and it would save 30 lines of code.
    """
    project_id = request.args["project"]
    name = request.args["name"]
    try:
        groups_path = get_groups_path(project_id, name=name)
    except ValueError as e:
        return jsonify({"error": str(e)}), 400
    if request.method == "POST":
        return login_required(update_groups)(groups_path)
    else:
        try:
            with groups_path.open("r") as f:
                return f.read()
        except:
            return (
                jsonify(
                    {"error": f"Could not open or read {groups_path}"}
                ),
                500,
            )


def update_groups(groups_path):
    data = request.get_json()
    try:
      yaml.load(data["groups"], Loader=yaml.SafeLoader)
    except Exception as e:
      return jsonify(str(e)), 400
    with groups_path.open("w") as f:
        f.write(data["groups"])
    return jsonify("OK")


def get_commit_batches_paths(ci_commit):
  batches_paths = []
  commit_config = ci_commit.data.get('qatools_config') or ci_commit.project.data.get('qatools_config')
  if not commit_config:
    return []
  commit_group_files = batches_files(
    commit_config,
    None,
    Path(ci_commit.project.id),
    Path(ci_commit.project.id_relative),
    ci_commit.repo_artifacts_dir,
  )
  print(commit_group_files, file=sys.stderr)
  # custom groups have priority over the commit's groups
  for group_file in commit_group_files:
    if (ci_commit.repo_artifacts_dir / group_file).exists():
      batches_paths.insert(0, ci_commit.repo_artifacts_dir / group_file)
  return batches_paths


@app.route("/api/v1/tests/group", methods=["POST"])
@login_required
def get_group():
    if not request.args["name"]:
        return jsonify({"tests": []})

    project_id = request.args["project"]
    project = Project.get_or_create(session=db_session, id=project_id)
    data = request.get_json()
    try:
        groups = list(data["groups"])
    except Exception as e:
        return jsonify({"error": f"Invalid groups: {e}"}), 400

    message = None
    try:
        batches_paths = [get_groups_path(project_id, name=group) for group in groups]
    except ValueError as e:
        return jsonify({"error": str(e)}), 400

    commit_id = request.args.get("commit")
    if not commit_id:
        return jsonify({"error": "Missing the commit"}), 400
    ci_commit = find_commit(project_id, commit_id)
    if not ci_commit:
        return jsonify({"error": f"Commit {commit_id} was not found in {project_id}"}), 404
    qatools_config = ci_commit.data.get("qatools_config") or project.data.get("qatools_config", {})
    if not qatools_config:
        return jsonify({"tests": [], "error": "QA-Board doesn't know this project's configuration (qaboard.yaml)."})

    # When they are not usable, the commit page and the form's check (check_tuning_request) say why
    artifacts_status = ci_commit.artifacts_status()
    commit_batches_paths = get_commit_batches_paths(ci_commit)
    if artifacts_status["ok"] and not commit_batches_paths:
        message = f"""
          <p>Could not find the <code>inputs.batches</code> files defined in <em>qaboard.yaml</em> in the artifacts.
            <br/><code>{escape(str(ci_commit.repo_artifacts_dir))}</code>
          </p>
          <p>Check they are listed in <code>artifacts</code> in <em>qaboard.yaml</em>, then call <code>qa save-artifacts</code>.</p>
        """
    batches_paths = [*commit_batches_paths, *batches_paths]

    if 'project' not in qatools_config or 'entrypoint' not in qatools_config.get('project', {}):
        return jsonify({"tests": [], "error": "qaboard.yaml doesn't define `project.entrypoint`", "message": message})
    qatools_config = {**qatools_config, 'project': {**qatools_config['project']}}
    has_custom_iter_inputs = False
    # TODO: make it more robust in case of "from iters import *"
    qatools_config['project']['entrypoint'] = ci_commit.repo_artifacts_dir / qatools_config['project']['entrypoint']
    if qatools_config['project']['entrypoint'].exists():
        with qatools_config['project']['entrypoint'].open() as f:
            entrypoint_source = f.read()
        has_custom_iter_inputs = re.search(r'^\s*(def iter_inputs\(|from .* import.* iter_inputs)', entrypoint_source, re.MULTILINE)
    # project fallback?
    if has_custom_iter_inputs:
        cwd = ci_commit.artifacts_dir
        # We run code from this folder, which comes from unauthenticated API calls
        try:
            check_storage_path(cwd)
        except UnsafePathError as e:
            return jsonify({"error": str(e)}), 400
        parent_including_cwd = [*list(reversed(list(cwd.parents))), cwd]
        envrcs = [f'source {quote(str(p / ".envrc"))}\n' for p in parent_including_cwd if (p / '.envrc').exists()]
        cmd = ' '.join([
            'qa',
            'batch',
            *list(itertools.chain.from_iterable((('--batches-file', quote(str(f))) for f in batches_paths))),
            '--list',
            '--',
            quote(request.args["name"]),
        ])
        cmd = '\n'.join([*envrcs, cmd])
        print(cmd)
        try:
            process = subprocess.run(
                ['bash', '-c', cmd],
                cwd=cwd,
                encoding="utf-8",
                capture_output=True,
            )
            print(process.stderr)
            process.check_returncode()
            tests = json.loads(process.stdout)
        except subprocess.CalledProcessError:
            # qa writes its errors on stderr
            error = "\n".join((process.stderr or process.stdout or "").strip().splitlines()[-20:])
            return jsonify({"error": f"`qa batch --list` failed: {error}", "cmd": str(cmd), "message": message}), 500
        except Exception as e:
            return jsonify({"error": f"`qa batch --list` failed: {e}", "cmd": str(cmd), "message": message}), 500
        return jsonify({"tests": tests, "message": message})

    # We don't need to seperate the two cases, but
    # doing so might let us avoid a fork and qa startup...
    # like in qaboard/config.py
    config_inputs = qatools_config.get('inputs', {})
    config_inputs_types = config_inputs.get('types', {})
    default_input_type = config_inputs_types.get('default', 'default')
    from qaboard.conventions import get_settings
    input_settings = get_settings(default_input_type, qatools_config)
    # like in qaboard/qa.py
    from qaboard.config import get_default_configuration, get_default_database
    default_configuration = get_default_configuration(input_settings)
    default_configurations = deserialize_config(default_configuration)
    default_database = get_default_database(input_settings)
    print('group', request.args["name"], batches_paths)
    try:
        tests = list(
            iter_inputs(
                [request.args["name"]],  # batches
                batches_paths,           # batches_files,
                default_database,        # database
                default_configurations,  # default_configuration
                'linux',                 # platform
                {"type": 'lsf'},         # default_job_configuration
                qatools_config,
                default_inputs_settings=input_settings,
            )
        )
        return jsonify({
            "tests": [{"input_path": str(run_context.rel_input_path), "configurations": run_context.configurations} for run_context in tests],
            "message": message,
        })
    except Exception as e:
        print(f'Error: {e}')
        return jsonify({"tests": [], "error": str(e), "message": message})


def _generate_batch_script(ci_commit, user, working_directory, command_id, batch_command, data):
    """Generate the qa_batch.sh script (shared across all runners)."""
    parent_including_cwd = [*list(reversed(list(working_directory.parents))), working_directory]
    envrcs = [f'source {quote(str(p / ".envrc"))}\n' for p in parent_including_cwd if (p / '.envrc').exists()]

    default_user = os.environ.get('QABOARD_DEFAULT_USER', 'qaboard')
    outputs_dir_prefix = str(ci_commit.outputs_dir).replace(f'/outputs/{default_user}/', f'/outputs/{user}/')
    script = "".join([
        "#!/bin/bash\n",
        # Helps users understand why they see no runs
        'trap \'echo "[qaboard] qa batch exited with code $?"\' EXIT\n',
        'export LC_ALL=en_US.utf8;\n',
        'export LANG=en_US.utf8;\n\n',
        'export MPLBACKEND=agg;\n',
        ('\n'.join(envrcs) + '\n') if envrcs else "",
        "set -xe\n\n",
        f'cd {quote(str(working_directory))};\n\n',
        f"\nexport CI=true;\n",
        f"\nexport GIT_COMMIT={quote(ci_commit.hexsha)};\n",
        f"export QABOARD_TUNING=true;\n\n",
        f"export QA_OUTPUTS_COMMIT={quote(outputs_dir_prefix)};\n\n",
        f"export QATOOLS_CI_COMMIT_DIR={quote(str(ci_commit.outputs_dir))};\n\n",
        f"export QA_BATCH_COMMAND_ID={quote(command_id)};\n\n",
        f"{batch_command};\n\n",
    ])
    return script


def _run_script(qa_batch_path, log_dir):
    """Run a bash script, appending its output to log_dir/log.txt"""
    cmd = ['bash', str(qa_batch_path)]
    print(cmd)
    with (Path(log_dir) / 'log.txt').open('a') as log:
        out = subprocess.run(cmd, stdout=log, stderr=subprocess.STDOUT)
    out.check_returncode()


def _dispatch_local(qa_batch_path, log_dir):
    """Run batch script locally via subprocess."""
    _run_script(qa_batch_path, log_dir)


def _dispatch_celery(qa_batch_path, log_dir):
    """Run batch script via celery worker. Injects broker URL into script."""
    broker_url = os.environ.get('CELERY_BROKER_URL', 'pyamqp://guest:guest@qaboard:5672//')
    qaboard_host = os.environ.get('QABOARD_HOST', 'localhost')
    qaboard_protocol = os.environ.get('QABOARD_PROTOCOL', 'http')

    celery_env = "".join([
        f"export QABOARD_PROTOCOL={quote(qaboard_protocol)}\n",
        f"export QABOARD_HOST={quote(qaboard_host)}\n",
        f"export CELERY_BROKER_URL={quote(broker_url)}\n",
        f"export no_proxy={quote(f'{qaboard_host},proxy,rabbitmq,qaboard')}\n",
    ])
    with qa_batch_path.open("r") as f:
        content = f.read()
    content = content.replace("#!/bin/bash\n", f"#!/bin/bash\n{celery_env}", 1)
    with qa_batch_path.open("w") as f:
        f.write(content)
    _run_script(qa_batch_path, log_dir)


def parse_bsub_job_id(output: str):
    """Returns the job ID from bsub's output: "Job <1234> is submitted to queue <normal>." """
    match = re.search(r'Job <(\d+)> is submitted', output or '')
    return match.group(1) if match else None


def _dispatch_lsf(qa_batch_path, log_dir, user, ci_commit, do_optimize):
    """
    Run batch script via LSF job submission (SSH + bsub).
    Returns {"lsf_job_id", "queue"}. The job writes its output in log_dir/log.txt, and LSF its report in log_dir/log.lsf.txt.
    """
    # TODO: We use a bridge server to submit - ideally we should use
    #       some LSF API to do it, but their docs/auth are terrible. 
    qatools_config = ci_commit.project.data.get("qatools_config", {})
    lsf_config = qatools_config.get('runners', qatools_config).get("lsf", {})
    default_queue = lsf_config.get('queue', 'default')
    queue = lsf_config.get('long_queue', 'default') if do_optimize else default_queue

    # The command given to bsub is parsed again by a shell on the execution host, so it's quoted twice
    job_command = f'bash {quote(str(qa_batch_path))} &>> {quote(f"{log_dir}/log.txt")}'
    start_script = "\n".join([
        "#!/bin/bash",
        "set -xe",
        "",
        f'mkdir -p {quote(str(log_dir))}',
        f'bsub -q {quote(str(queue))} -o {quote(f"{log_dir}/log.lsf.txt")} -sp 4000 {quote(job_command)}',
    ])
    print(start_script)

    start_path = log_dir / "start.sh"
    with start_path.open("w") as f:
        f.write(start_script)

    # raises if the user or path are not safe to use in the LSF bridge
    cmd = lsf_bridge_command(user, start_path)
    print(cmd)
    out = subprocess.run(cmd, shell=True, encoding="utf-8", stdout=subprocess.PIPE, stderr=subprocess.STDOUT)
    print(out.stdout)
    if out.returncode != 0:
        # Users see this log in QA-Board
        with (log_dir / 'log.txt').open('a') as log:
            log.write(f"ERROR: Could not submit the job to LSF (exit code {out.returncode}):\n{out.stdout}\n")
    out.check_returncode()
    return {"lsf_job_id": parse_bsub_job_id(out.stdout), "queue": queue}


# We keep the history of the last batches started from QA-Board
MAX_SUBMISSIONS = 20

def record_submission(batch, submission):
    """
    Remember in batch.data.submissions how users started the batch from QA-Board.
    QA-Board uses it to show the status of `qa batch`, and its logs.
    """
    data = batch.data or {}
    submissions = {**data.get('submissions', {}), submission['id']: submission}
    if len(submissions) > MAX_SUBMISSIONS:
        recent = sorted(submissions.values(), key=lambda s: s.get('created_at', ''))[-MAX_SUBMISSIONS:]
        submissions = {s['id']: s for s in recent}
    batch.data = {**data, 'submissions': submissions}
    flag_modified(batch, "data")


def find_commit(project_id, hexsha):
    return (CiCommit.query
            .filter(CiCommit.project_id == project_id, CiCommit.hexsha.startswith(hexsha))
            .order_by(CiCommit.id)
            .first())


def load_batches(ci_commit, project_id, groups):
    """Merges the batches defined in the commit's batches files and in the custom groups. Returns (merged_batches, errors)."""
    errors = []
    batches_paths = get_commit_batches_paths(ci_commit)
    for group in groups:
        try:
            batches_paths.append(get_groups_path(project_id, name=group))
        except ValueError as e:
            errors.append(str(e))
    merged_batches: Dict[str, Any] = {}
    for path in batches_paths:
        try:
            with path.open() as f:
                c_dict = yaml.load(f, Loader=yaml.SafeLoader) or {}
            if not isinstance(c_dict, dict):
                raise ValueError("it should define a mapping of batches")
        except Exception as e:
            errors.append(f"Could not read the batches in {path.name}: {e}")
            continue
        merged_batches = merge(c_dict, merged_batches)
    merged_batches['aliases'] = merged_batches.get('aliases', merged_batches.get('groups', {})) or {} # backward-compat
    return merged_batches, errors


def pipeline_batches(batches, merged_batches):
    # FIXME:  handle pipelines. replace with a generic solution.
    for b in list(batches):
        batch_context = merged_batches.get(b, {}) or {}
        if not isinstance(batch_context, dict) or batch_context.get('type', " ") != 'pipeline':
            continue
        for key in batch_context.keys():
            if key.lower() in ['configuration', 'configurations', 'configs']:
                for step in batch_context.get(key, []) or []:
                    if isinstance(step, dict) and 'batch' in step.keys():
                        step_config = step.get('batch')
                        if isinstance(step_config, str): batches.append(step_config)
                        elif isinstance(step_config, list): batches = batches + [b for b in step_config if isinstance(b, str)]
                        batches = list(resolve_aliases(batches, merged_batches['aliases']))
    return batches


def check_tuning_search(tuning_search) -> list:
    if not isinstance(tuning_search, dict):
        return ["The tuning parameters are missing."]
    search_type = tuning_search.get('search_type', 'grid')
    parameter_search = tuning_search.get('parameter_search')
    if search_type == 'optimize':
        try:
            optim_config = yaml.load(parameter_search or '', Loader=yaml.SafeLoader)
        except Exception as e:
            return [f"The automated tuning configuration is not valid YAML: {e}"]
        if not isinstance(optim_config, dict):
            return ["The automated tuning configuration should be a YAML mapping, with an `objective`, an `evaluations` budget and a `search_space`."]
        errors = [f"The automated tuning configuration needs a `{key}`." for key in ('objective', 'evaluations', 'search_space') if key not in optim_config]
        if 'evaluations' in optim_config and (not isinstance(optim_config['evaluations'], int) or optim_config['evaluations'] < 1):
            errors.append("`evaluations` should be a positive integer.")
        if 'search_space' in optim_config and not optim_config['search_space']:
            errors.append("`search_space` is empty: there is nothing to optimize.")
        return errors
    if search_type not in ('grid', 'sampler'):
        return [f"Unknown search type: {search_type}"]
    if parameter_search is None:
        return []
    params = parameter_search if isinstance(parameter_search, list) else [parameter_search]
    if not all(isinstance(p, dict) for p in params):
        return ["The tuning parameters should be a mapping of parameters to the values to try, or a list of them."]
    return []


def check_tuning_request(ci_commit, data):
    """
    Checks a request to run a batch from QA-Board, before we start anything.
    Returns {errors, warnings, artifacts, nb_batches, merged_batches, batches}: with errors we don't start.
    """
    from .commit import commit_artifacts_info
    errors, warnings = [], []
    data = data or {}
    project_config = ci_commit.project.data.get("qatools_config")
    config = ci_commit.data.get("qatools_config") or project_config
    if not config:
        errors.append("QA-Board doesn't know this project's configuration: run `qa save-artifacts` or `qa batch` on this commit, with qaboard.yaml.")
        config = {}

    label = str(data.get('batch_label') or '').strip()
    if not label:
        errors.append("Give a name to the batch.")
    elif label in ('default',) and data.get('tuning_search', {}).get('search_type') == 'optimize':
        warnings.append("Automated tuning creates one batch per iteration: give it a more specific name than \"default\".")

    platforms = config.get('inputs', {}).get('platforms') if isinstance(config.get('inputs'), dict) else None
    if data.get('platform') and platforms and data['platform'] not in platforms:
        errors.append(f"Unknown platform {data['platform']!r}. Available in qaboard.yaml: {', '.join(map(str, platforms))}")

    errors.extend(check_tuning_search(data.get('tuning_search')))

    artifacts = commit_artifacts_info(ci_commit)
    if not artifacts["ok"]:
        problems = " ".join(artifacts.get("problems", []))
        if artifacts.get("recreate") and not artifacts.get("recreate_errors"):
            warnings.append(f"The artifacts are missing: {problems} When you start, QA-Board will first ask {artifacts['recreate']} to recreate them.")
        else:
            warnings.append(f"The artifacts are missing: {problems} When you start, QA-Board will try to restore them from the source code, but not build outputs (e.g. binaries).")
    for error in artifacts.get("recreate_errors", []):
        warnings.append(f"qaboard.yaml: {error}")

    entrypoint = config.get('project', {}).get('entrypoint') if isinstance(config.get('project'), dict) else None
    if entrypoint and artifacts["ok"] and not (ci_commit.repo_artifacts_dir / entrypoint).exists():
        warnings.append(f"The project's entrypoint ({entrypoint}) is not in the artifacts: runs will likely fail. Check `artifacts` in qaboard.yaml.")

    merged_batches, batches = {}, []
    selected_group = str(data.get('selected_group') or '').strip()
    groups = data.get('groups') or []
    if not isinstance(groups, list):
        errors.append("`groups` should be a list.")
        groups = []
    if not selected_group:
        errors.append("Select the batch of inputs to run on.")
    else:
        merged_batches, batches_errors = load_batches(ci_commit, ci_commit.project_id, groups)
        warnings.extend(batches_errors)
        batches = list(resolve_aliases(selected_group, merged_batches['aliases']))
        batches = pipeline_batches(batches, merged_batches)
        unknown = [b for b in batches if b not in merged_batches]
        if unknown:
            defined = sorted(k for k in merged_batches if k not in ('aliases', 'groups', 'database'))
            hint = f" Defined: {', '.join(defined[:20])}{'...' if len(defined) > 20 else ''}" if defined else " No batches are defined: check `inputs.batches` in qaboard.yaml, and that the batches files are in the artifacts."
            errors.append(f"Unknown batch{'es' if len(unknown) > 1 else ''}: {', '.join(unknown)}.{hint}")
    return {
        "errors": errors,
        "warnings": warnings,
        "artifacts": artifacts,
        "merged_batches": merged_batches,
        "batches": batches,
    }


@app.route("/api/v1/commit/<hexsha>/batch/check", methods=["POST"], strict_slashes=False)
@login_required
def check_tuning(hexsha):
    """Validates a request to start a batch, without starting it. Returns {errors, warnings, artifacts}."""
    project_id = request.args["project"]
    ci_commit = find_commit(project_id, hexsha)
    if not ci_commit:
        return jsonify({"error": f"Commit {hexsha} was not found in {project_id}", "errors": [f"Commit {hexsha} was not found in {project_id}"], "warnings": []}), 404
    check = check_tuning_request(ci_commit, request.get_json())
    return jsonify({k: v for k, v in check.items() if k in ("errors", "warnings", "artifacts", "batches")})


@app.route("/api/v1/commit/<hexsha>/batch", methods=["POST"], strict_slashes=False)
@login_required
def start_tuning(hexsha):
    """
    Request that we run extra tests for a given project.
    """
    project_id = request.args["project"]
    data = request.get_json()

    # We run as the logged-in user, ignoring data['user']
    try:
        user = safe_user_name(g.user['user_name'])
    except ValueError as e:
        return jsonify({"error": str(e)}), 400

    ci_commit = find_commit(project_id, hexsha)
    if not ci_commit:
        return jsonify({"error": f"Commit {hexsha} was not found in {project_id}"}), 404

    check = check_tuning_request(ci_commit, data)
    if not check["artifacts"]["ok"]:
        # We never run from missing artifacts: e.g. without its qaboard.yaml, a subproject's runs would be saved in the parent project
        try:
            ci_commit.ensure_artifacts(user=user)
        except ArtifactsUnavailable as e:
            db_session.add(ci_commit)
            db_session.commit()
            return jsonify(e.to_dict()), 409
        db_session.add(ci_commit)
        db_session.commit()
        # the batches files come from the artifacts
        check = check_tuning_request(ci_commit, data)
    if check["errors"]:
        return jsonify({"error": " ".join(check["errors"]), "errors": check["errors"], "warnings": check["warnings"]}), 400

    # Now that we updated the last_output_datetime, the artifacts won't be deleted again until a little while
    ci_commit.latest_output_datetime = datetime.datetime.now()
    batch = ci_commit.get_or_create_batch(data['batch_label'])
    db_session.add(ci_commit)
    db_session.commit()

    merged_batches, batches = check["merged_batches"], check["batches"]
    merged_batches = { key:value for key, value in merged_batches.items() if key in ['aliases', 'database', *batches]}
    # TODO: filter the aliases, but it requires care in case of multiple levels of aliases...

    # We store in this directory the scripts used to run this new batch, as well as the logs
    # We may instead want to use the folder where this batch's results are stored
    # Or even store the metadata in the database itself...
    prev_mask = os.umask(000)

    batch_dir = batch.batch_dir
    # FIXME: if the output directory includes "{user}", we will use the current user (qaboard)
    # but it's likely better to use the user that requested the tuning
    default_user = os.environ.get('QABOARD_DEFAULT_USER', 'qaboard')
    batch_dir = Path(str(batch_dir).replace(f'/outputs/{default_user}/', f'/outputs/{user}/'))
    # We write scripts in the batch's folder, and run code from the artifacts folder
    try:
        check_storage_path(batch_dir)
        check_storage_path(ci_commit.artifacts_dir)
    except UnsafePathError as e:
        os.umask(prev_mask)
        return jsonify({"error": str(e)}), 400
    if not batch.batch_dir_override:
        batch.batch_dir_override = str(batch_dir)
        db_session.add(batch)
        db_session.commit()

    if not batch_dir.exists():
        batch_dir.mkdir(exist_ok=True, parents=True)
    os.umask(prev_mask)

    command_id = str(uuid.uuid4())
    # Each batch started from QA-Board has its own scripts and logs
    created_at = datetime.datetime.utcnow()
    log_dir = batch_dir / 'qaboard-batches' / f"{created_at.strftime('%Y%m%d-%H%M%S')}-{command_id[:8]}"
    prev_mask = os.umask(000)
    log_dir.mkdir(parents=True, exist_ok=True)
    os.umask(prev_mask)
    merged_batches_path = f'{log_dir}/batches.yaml'
    with Path(merged_batches_path).open('w') as f:
        f.write(yaml.dump(merged_batches))

    working_directory = ci_commit.artifacts_dir
    print(working_directory)

    # This will make us do automated tuning, versus a single manual batch
    do_optimize = data['tuning_search']['search_type'] == 'optimize'
    if do_optimize:
        # we write somewhere the optimzation search configuration
        # it needs to be accessed from LSF so we can't use temporary files...
        config_path = batch_dir / 'optim-config.yaml'
        checkpoint_path = batch_dir / 'checkpoint.pkl'
        config_option = f"--config-file {quote(str(config_path))} --checkpoint {quote(str(checkpoint_path))}"
        with config_path.open("w") as f:
            f.write(data['tuning_search']['parameter_search'])
    else:
        config_option = f"--tuning-search {quote(json.dumps(data['tuning_search']))}"

    overwrite = "--action-on-existing run" if data["overwrite"] in ("on", True) else "--action-on-existing sync"
    batch_command = " ".join([
        "qa",
        f"--platform {quote(str(data['platform']))}" if "platform" in data else "",
        f"--label {quote(str(data['batch_label']))}",
        "optimize" if do_optimize else "batch",
        f"--batches-file {quote(merged_batches_path)}",
        f"--batch {quote(str(data['selected_group']))}",
        # f"--runner=local", # uncomment if testing from Samsung SIRC where LSF is the default
        config_option,
        f"{overwrite} --no-wait" if not do_optimize else '',
    ])
    print(batch_command)

    qa_batch_script = _generate_batch_script(ci_commit, user, working_directory, command_id, batch_command, data)
    print(qa_batch_script)
    qa_batch_path = log_dir / "qa_batch.sh"
    with qa_batch_path.open("w") as f:
        f.write(qa_batch_script)

    runner = os.environ.get('QABOARD_TUNING_RUNNER', 'local')
    submission = {
        "id": command_id,
        "created_at": created_at.isoformat() + 'Z',
        "user": user,
        "runner": runner,
        "command": batch_command,
        "log_dir": str(log_dir),
        # local/celery: we wait for `qa batch` (it uses --no-wait), LSF: `qa batch` is an LSF job
        "status": "submitting",
    }
    record_submission(batch, submission)
    db_session.add(batch)
    db_session.commit()

    try:
        if runner == 'lsf':
            submission.update(_dispatch_lsf(qa_batch_path, log_dir, user, ci_commit, do_optimize))
            submission["status"] = "submitted"
        elif runner == 'celery':
            _dispatch_celery(qa_batch_path, log_dir)
            submission.update({"status": "done", "exit_code": 0})
        else:
            _dispatch_local(qa_batch_path, log_dir)
            submission.update({"status": "done", "exit_code": 0})
    except Exception as e:
        submission["status"] = "failed"
        if isinstance(e, subprocess.CalledProcessError):
            submission["exit_code"] = e.returncode
        else:
            submission["error"] = str(e)
    # `qa batch` may have updated the batch meanwhile
    db_session.refresh(batch)
    record_submission(batch, submission)
    db_session.add(batch)
    db_session.commit()

    if submission["status"] == "failed":
        error_log = (log_dir / 'log.txt').read_text() if (log_dir / 'log.txt').exists() else submission.get("error", "Failed to start batch")
        return jsonify({"error": error_log, "cmd": runner, "submission": submission}), 500
    return jsonify({"cmd": runner, "stdout": "OK", "submission": submission})
