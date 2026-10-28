"""
Commits with their outputs (the commit page, History): their outputs are serialized from rows, without ORM objects
(serialize_outputs in backend/models/Output.py). They must be what Output.to_dict() gives.
"""
from urllib.parse import quote

from .test_ingest import output_payload, PROJECT, SHA


def test_outputs_like_to_dict(client, monkeypatch, tmp_path):
  import backend.storage
  monkeypatch.setattr(backend.storage, 'storage_roots', [tmp_path])
  # a run that says where its outputs are, and one from an old client: its folder follows conventions
  for payload in [
    output_payload(rel_input_path='inputs/a.txt', output_directory=str(tmp_path / 'run-a'), input_metadata={"lux": 10}, data={"storage": 12}),
    output_payload(rel_input_path='inputs/b.txt', batch_label='tuning', metrics={"loss": 2.0}, is_failed=True),
  ]:
    response = client.post('/api/v1/output/', json=payload)
    assert response.status_code == 200, response.get_data(as_text=True)
  from backend.database import db_session
  from backend.models import Output
  db_session.remove()
  expected = {str(o.id): o.to_dict() for o in Output.query.all()}
  assert sorted(o['output_dir_url'] for o in expected.values())[1] == quote(f"/s{tmp_path}/run-a")

  def outputs(commit):
    return {id: output for batch in commit['batches'].values() for id, output in batch['outputs'].items()}
  response = client.get(f'/api/v1/commit/{SHA}', query_string={"project": PROJECT})
  assert response.status_code == 200, response.get_data(as_text=True)
  assert outputs(response.get_json()) == expected
  response = client.get('/api/v1/commits', query_string={"project": PROJECT, "with_outputs": "true"})
  assert response.status_code == 200, response.get_data(as_text=True)
  assert outputs(response.get_json()[0]) == expected
  # the latest commit of the branch
  response = client.get('/api/v1/commit/', query_string={"project": PROJECT, "branch": "master"})
  assert outputs(response.get_json()) == expected
