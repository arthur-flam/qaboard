#!/usr/bin/env python3
"""
Applies edits.json to a project where `qa wizard` wrote its template, like the AI assistant would:
  cd ~/denoiser && qa wizard --yes --no-ai && python path/to/apply_edits.py .
"""
import json
import sys
from pathlib import Path


def apply_edits(root: Path, edits_path: Path = Path(__file__).resolve().parent / 'edits.json'):
  for edit in json.loads(edits_path.read_text())['edits']:
    path = root / edit['path']
    if edit['tool'] == 'write_file':
      path.parent.mkdir(parents=True, exist_ok=True)
      path.write_text(edit['content'])
      print(f"wrote   {edit['path']}")
      continue
    content = path.read_text()
    count = content.count(edit['old_text'])
    if count == 0 and edit.get('optional'):
      print(f"skipped {edit['path']}: {edit['why']}")
      continue
    if count != 1:
      sys.exit(f"{edit['path']}: old_text found {count} times, expected once:\n{edit['old_text']}")
    path.write_text(content.replace(edit['old_text'], edit['new_text']))
    print(f"edited  {edit['path']}: {edit['why']}")


if __name__ == '__main__':
  apply_edits(Path(sys.argv[1] if len(sys.argv) > 1 else '.'))
