"""
Unit tests for starting batches from QA-Board (backend/api/tuning.py)
"""
from types import SimpleNamespace


def test_parse_bsub_job_id():
    from backend.api.tuning import parse_bsub_job_id
    assert parse_bsub_job_id("Job <1234> is submitted to queue <normal>.\n") == "1234"
    assert parse_bsub_job_id("some wrapper output\nJob <42> is submitted to default queue <short>.") == "42"
    assert parse_bsub_job_id("bsub: command not found") is None
    assert parse_bsub_job_id(None) is None


def test_record_submission_keeps_other_data():
    from backend.api.tuning import record_submission
    batch = SimpleNamespace(data={"commands": {"abc": {"argv": ["qa", "batch"]}}})
    record_submission(batch, {"id": "s1", "created_at": "2026-10-05T10:00:00Z", "status": "submitting"})
    record_submission(batch, {"id": "s1", "created_at": "2026-10-05T10:00:00Z", "status": "failed", "exit_code": 1})
    assert batch.data["commands"] == {"abc": {"argv": ["qa", "batch"]}}
    assert batch.data["submissions"] == {"s1": {"id": "s1", "created_at": "2026-10-05T10:00:00Z", "status": "failed", "exit_code": 1}}


def test_record_submission_keeps_the_most_recent():
    from backend.api.tuning import record_submission, MAX_SUBMISSIONS
    batch = SimpleNamespace(data=None)
    for index in range(MAX_SUBMISSIONS + 5):
        record_submission(batch, {"id": f"s{index}", "created_at": f"2026-10-05T10:{index:02d}:00Z"})
    ids = set(batch.data["submissions"])
    assert len(ids) == MAX_SUBMISSIONS
    assert "s0" not in ids and f"s{MAX_SUBMISSIONS + 4}" in ids


def test_check_tuning_search():
    from backend.api.tuning import check_tuning_search
    assert check_tuning_search({"search_type": "grid", "parameter_search": [{"a": 1}, {"a": 2}]}) == []
    assert check_tuning_search({"search_type": "grid", "parameter_search": {"a": 1}}) == []
    assert check_tuning_search({"search_type": "sampler", "search_options": {"n_iter": 5}, "parameter_search": [{"a": 1}]}) == []
    assert check_tuning_search({"search_type": "grid", "parameter_search": [1]})
    assert check_tuning_search(None)
    assert "Unknown search type" in check_tuning_search({"search_type": "random"})[0]
    optimize = lambda yaml: check_tuning_search({"search_type": "optimize", "parameter_search": yaml})
    assert "not valid YAML" in optimize("a: [")[0]
    errors = " ".join(optimize("evaluations: 0\nsearch_space: []"))
    assert "objective" in errors and "positive integer" in errors and "empty" in errors
    assert optimize("objective: {psnr: 1}\nevaluations: 10\nsearch_space:\n- Real: {name: x, low: 0, high: 1}") == []


def make_commit(tmp_path, config, batches=None):
    import yaml
    root = tmp_path / "artifacts"
    (root / "sub").mkdir(parents=True)
    (root / "qaboard.yaml").write_text("project: {name: repo}")
    (root / "sub/qaboard.yaml").write_text("")
    if batches is not None:
        (root / "sub/batches.yaml").write_text(yaml.dump(batches))
    status = {"ok": True, "problems": []}
    return SimpleNamespace(
        hexsha="abcdef",
        deleted=False,
        data={"qatools_config": config},
        project=SimpleNamespace(id="repo/sub", id_relative="sub", id_git="repo", data={}),
        project_id="repo/sub",
        artifacts_dir=root / "sub",
        repo_artifacts_dir=root,
        artifacts_status=lambda max_checked_files=500: status,
        recreate_artifacts_settings=None,
    )


def test_check_tuning_request(tmp_path):
    from backend.api.tuning import check_tuning_request
    config = {"project": {"name": "repo", "entrypoint": "sub/qa/main.py"}, "inputs": {"batches": "sub/batches.yaml", "platforms": ["linux"]}}
    ci_commit = make_commit(tmp_path, config, batches={"smoke": {"inputs": ["a.jpg"]}, "aliases": {"all": ["smoke"]}})
    data = {"batch_label": "tuning", "selected_group": "all", "groups": [], "platform": "linux", "tuning_search": {"search_type": "grid", "parameter_search": {}}}
    check = check_tuning_request(ci_commit, data)
    assert check["errors"] == []
    assert check["batches"] == ["smoke"]
    # the entrypoint is missing from the artifacts
    assert any("entrypoint" in w for w in check["warnings"])

    check = check_tuning_request(ci_commit, {**data, "selected_group": "smok", "platform": "windows", "batch_label": " "})
    errors = " ".join(check["errors"])
    assert "Unknown batch: smok" in errors and "Defined: all, smoke" not in errors and "smoke" in errors
    assert "Unknown platform" in errors
    assert "name to the batch" in errors


def test_check_tuning_request_without_batches_files(tmp_path):
    from backend.api.tuning import check_tuning_request
    config = {"project": {"name": "repo"}, "inputs": {"batches": "sub/batches.yaml"}}
    ci_commit = make_commit(tmp_path, config)
    check = check_tuning_request(ci_commit, {"batch_label": "x", "selected_group": "smoke", "tuning_search": {"search_type": "grid"}})
    assert "No batches are defined" in " ".join(check["errors"])
