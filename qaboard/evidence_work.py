"""
The person's own work, for `qa mcp` (qaboard/mcp_server.py): what they ran lately (`my_work`), and a link that opens
Run Tests / Tuning filled in, for them to check and start (`prepare_tuning`). Nothing here depends on MCP.

An agent in a chat client knows nothing of what the person works on: `my_work` starts from QA-Board's own record of it,
the batches they started (backend/mine.py), not the CI's: in a monorepo, every commit gets many projects' CI runs that
have nothing to do with their work.

Agents prepare, people start: `prepare_tuning` only checks the batches and builds the link (the web app reads it in
webapp/src/components/tuning/prefill_link.js). Nothing runs until the person clicks start in the form.
"""
import datetime
import fnmatch
import json
import math
import re
from typing import Any, Dict, List, Optional, Sequence, Union
from urllib.parse import urlencode

from .evidence import select
from .evidence_base import Answer, Api, EvidenceError, MAX_ITEMS, code, format_number, one_line
from .results import ResultsError

# Where people's batches come from: the web app (Run Tests / Tuning) or a terminal. Not the CI.
THEIR_ORIGINS = 'webapp,local'
# The QA-Board batch files the form lists besides the commit's (backend/backend/api/tuning.py: SHARED_BATCHES_FILE)
SHARED_BATCHES_FILE = 'extra-batches'
# Links longer than this may not survive chat clients, proxies and browsers
MAX_LINK = 6000
# Values to try: more than this many combinations is rarely what people mean
MANY_COMBINATIONS = 1000

Parameters = Union[Dict[str, Any], List[Dict[str, Any]]]


def whoami(api: Api) -> Dict[str, Any]:
  try:
    me = api.get('user/me/') or {}
  except ResultsError as e:
    raise EvidenceError(f"Could not tell who you are on {api.public_url}: {e}") from e
  if not me.get('is_authenticated') or not me.get('user_name'):
    raise EvidenceError(f"Not signed in to {api.public_url}: run `qa auth login`, or connect the MCP server with your account.")
  return me


def days_ago(days: int) -> str:
  return (datetime.datetime.now(datetime.timezone.utc) - datetime.timedelta(days=days)).strftime('%Y-%m-%dT%H:%M:%S.000Z')


# ---------------------------------------------------------------------------- my_work

def tuning_of(batch: Dict[str, Any]) -> Optional[str]:
  """What a batch started from the web app tried, in a few words: "threshold, kernel: 6 combinations" """
  submissions = (batch.get('data') or {}).get('submissions')
  if not isinstance(submissions, dict):
    return None
  for submission in submissions.values():
    request = submission.get('request') if isinstance(submission, dict) else None
    search = (request or {}).get('tuning_search') if isinstance(request, dict) else None
    if not isinstance(search, dict):
      continue
    if search.get('search_type') == 'optimize':
      return 'automated tuning'
    parameters = search.get('parameter_search')
    if isinstance(parameters, (dict, list)) and parameters:
      keys = list(dict.fromkeys(k for s in (parameters if isinstance(parameters, list) else [parameters]) if isinstance(s, dict) for k in s))
      shown = ', '.join(code(k) for k in keys[:4]) + (f" and {len(keys) - 4} more" if len(keys) > 4 else '')
      tried = combinations(parameters)
      n_iter = (search.get('search_options') or {}).get('n_iter')
      if search.get('search_type') == 'sampler' and isinstance(n_iter, int) and 0 < n_iter < tried:
        return f"{shown}: {n_iter} of {tried} combinations"
      return f"{shown}: {tried} combination{'s' if tried != 1 else ''}"
  return None


def runs_text(batch: Dict[str, Any]) -> str:
  failed, unfinished = batch.get('failed_outputs') or 0, batch.get('pending_outputs') or 0
  runs = (batch.get('valid_outputs') or 0) + failed + unfinished
  return f"{runs} runs" + (f", {failed} failed" if failed else '') + (f", {unfinished} unfinished" if unfinished else '')


def default_metric_text(batch: Dict[str, Any]) -> str:
  name = batch.get('default_metric')
  value = (batch.get('aggregated_metrics') or {}).get(f"{name}_median") if name else None
  if value is None:
    return ''
  info = batch.get('default_metric_info') or {}
  return f", median {code(info.get('short_label') or info.get('label') or name)} {format_number(value, info.get('suffix') or '', info.get('scale') or 1.0)}"


def my_work(api: Api, project: Optional[str] = None, days: int = 14, include_ci: bool = False, limit: int = 30) -> Answer:
  me = whoami(api)
  params: Dict[str, Any] = {'scope': 'mine', 'origin': THEIR_ORIGINS, 'since_days': days, 'limit': limit, 'metrics': 'default'}
  if project:
    params['project'] = project
  batches = api.get('batches', params) or []
  who = me.get('full_name') or me['user_name']
  lines = [f"## {who}'s work on {api.public_url}, the last {days} days{' in ' + project if project else ''}"]
  if not batches:
    lines.append("No batches started from the web app or a terminal" + (" (the CI's are left out: `include_ci` with a `project`)." if not include_ci else '.'))

  # by project, then branch: what they work on
  groups: Dict[tuple, List[Dict[str, Any]]] = {}
  for b in batches:
    groups.setdefault((b.get('project'), (b.get('commit') or {}).get('branch') or ''), []).append(b)
  rows = []
  for (proj, branch), items in groups.items():
    lines.append(f"\n### {code(proj)}" + (f" on {code(branch)}" if branch else ''))
    for b in items:
      commit = b.get('commit') or {}
      sha = commit.get('id') or b.get('commit_id') or ''
      url = api.page(proj, 'commit', sha, batch=b.get('label'))
      tuning = tuning_of(b)
      when = (b.get('latest_output_datetime') or b.get('created_date') or '')[:16].replace('T', ' ')
      origin = {'webapp': 'from the web app', 'local': 'from a terminal'}.get(b.get('origin') or '', b.get('origin') or '')
      lines.append(f"- {code(b.get('label'))} on {sha[:8]} {code(one_line(commit.get('message'), 60))}, {when}, {origin}: "
                   f"{runs_text(b)}{default_metric_text(b)}" + (f". Tried {tuning}" if tuning else '') + f". [Open]({url})")
      rows.append({'project': proj, 'branch': branch, 'commit': sha, 'label': b.get('label'), 'origin': b.get('origin'), 'date': when,
                   'message': one_line(commit.get('message'), 80), 'runs': runs_text(b), 'tuning': tuning, 'url': url,
                   'failed': b.get('failed_outputs') or 0, 'unfinished': b.get('pending_outputs') or 0})

  milestones = []
  projects = [project] if project else list(dict.fromkeys(p for p, _ in groups))[:5]
  for proj in projects:
    try:
      found = (api.get('milestones', {'project': proj}) or {}).get('milestones') or []
    except ResultsError:
      continue
    since = days_ago(days)
    mine = [m for m in found if isinstance(m, dict) and m.get('is_mine') and str(m.get('updated_at') or '') >= since[:19]]
    milestones += [{'project': proj, 'label': m.get('label'), 'id': m.get('id'), 'shared': m.get('shared'), 'commit': m.get('commit'), 'batch': m.get('batch')} for m in mine]
  if milestones:
    lines.append("\n### Milestones you saved (usable as `reference`)")
    lines += [f"- {code(m['project'])}: {code(m['label'] if m['shared'] else '#' + str(m['id']))} ({m['label']}, {str(m['commit'] or '')[:8]}, label {code(m['batch'])})"
              for m in milestones[:MAX_ITEMS]]

  ci = []
  if include_ci:
    if not project:
      raise EvidenceError("The CI's results are many in a monorepo: give `project` with `include_ci`.")
    commits = api.get('commits', {'project': project, 'scope': 'mine', 'only_ci_batches': 'true', 'from': days_ago(days), 'limit': 20}) or []
    for c in commits:
      for label, b in (c.get('batches') or {}).items():
        ci.append({'commit': c.get('id'), 'branch': c.get('branch'), 'label': label, 'message': one_line(c.get('message'), 80),
                   'runs': runs_text(b), 'url': api.page(project, 'commit', c.get('id'), batch=label)})
    lines.append(f"\n### The CI's results on your commits in {code(project)}")
    lines += [f"- {str(r['commit'])[:8]} {code(r['branch'] or '')} {code(r['message'])}: {code(r['label'])} {r['runs']}. [Open]({r['url']})" for r in ci[:MAX_ITEMS]] or ["None."]

  if rows:
    lines.append("\nNext: `compare` a batch with its reference (`project`, `commit`, `label`), `failures` for failed runs, "
                 "`prepare_tuning` to give them a link to start another tuning.")
  return Answer('\n'.join(lines), {'view': 'my_work', 'user': me['user_name'], 'days': days, 'batches': rows[:MAX_ITEMS],
                                   'milestones': milestones[:MAX_ITEMS], 'ci': ci[:MAX_ITEMS]})


# ---------------------------------------------------------------------------- prepare_tuning

def combinations(parameters: Parameters) -> int:
  """How many sets of values a `parameter_search` tries: every combination within a set, the sets one after the other"""
  sets = parameters if isinstance(parameters, list) else [parameters]
  return sum(math.prod(len(v) if isinstance(v, list) else 1 for v in s.values()) for s in sets if isinstance(s, dict))


def checked_parameters(parameters: Any) -> Parameters:
  """`{name: value or [values]}` or a list of those, with JSON values: like the form and the server take them"""
  sets = parameters if isinstance(parameters, list) else [parameters]
  if not sets or not all(isinstance(s, dict) and s and all(isinstance(k, str) and k for k in s) for s in sets):
    raise EvidenceError("`parameters`: give {\"name\": [values to try]}, or a list of those for values that go together.")
  try:
    json.dumps(parameters, allow_nan=False)
  except ValueError as e:
    raise EvidenceError(f"`parameters` must be JSON values: {e}") from e
  return parameters


def available_batches(api: Api, project: str, commit: str, user_name: str) -> Dict[str, Any]:
  """What the form lists: the batches of the commit's files and of QA-Board's (shared, the person's)"""
  query = urlencode({'project': project, 'commit': commit})
  return api.post(f"tests/batches?{query}", {'groups': [SHARED_BATCHES_FILE, user_name]}) or {}


def prepare_tuning(api: Api, batches: Sequence[str], url: Optional[str] = None, project: Optional[str] = None, commit: Optional[str] = None,
                   parameters: Any = None, label: Optional[str] = None, search_type: str = 'grid', n_iter: Optional[int] = None) -> Answer:
  me = whoami(api)
  selection = select(api, url, project, commit)
  # the form reads "night, day" as two batches
  names = [n.strip() for b in batches for n in str(b).split(',') if n.strip()]
  if not names:
    raise EvidenceError("Which batches to run? Give `batches`: `prepare_tuning` lists those available when one is unknown.")
  if search_type not in ('grid', 'sampler'):
    raise EvidenceError("`search_type`: grid (every combination) or sampler (`n_iter` random combinations).")
  if search_type == 'sampler' and not (isinstance(n_iter, int) and n_iter > 0):
    raise EvidenceError("A sampler tries `n_iter` combinations: give it.")

  notes = []
  tests = None  # how many tests the batches have, when their files say
  try:
    listed = available_batches(api, selection.project, selection.commit, me['user_name'])
  except ResultsError as e:
    listed = None
    notes.append(f"Could not check the batch names ({e}): the form will say if one is unknown.")
  if listed is not None:
    known = [str(b['name']) for b in listed.get('batches') or [] if isinstance(b, dict) and b.get('name')]
    unknown = [n for n in names if not any(fnmatch.fnmatchcase(k, n) for k in known)]
    if unknown:
      raise EvidenceError(f"No batch {', '.join(code(n) for n in unknown)} on this commit. Available: "
                          f"{', '.join(code(k) for k in known[:40]) or 'none'}" + (f" and {len(known) - 40} more" if len(known) > 40 else '') + '.'
                          + ''.join(f"\n- {one_line(w, 200)}" for w in (listed.get('warnings') or [])[:3]))
    notes += [one_line(w, 200) for w in (listed.get('warnings') or [])[:3]]
    sizes = {b['name']: b for b in listed.get('batches') or [] if isinstance(b, dict) and b.get('name') in names}
    inputs = [sizes.get(n, {}).get('inputs') for n in names]  # patterns and aliases: unknown
    counted = [i for i in inputs if isinstance(i, int)]
    if len(counted) == len(names):
      tests = sum(counted)

  # without values to try, none: else the form would keep those it had, maybe last week's
  prefill: Dict[str, Any] = {'batches': names, 'parameter_search': {}}
  tried = None
  if parameters is not None:
    prefill['parameter_search'] = checked_parameters(parameters)
    tried = combinations(prefill['parameter_search'])
    if search_type == 'sampler':
      tried = min(tried, n_iter or tried)
    if tried > MANY_COMBINATIONS:
      notes.append(f"That's {tried} combinations: ask the person if they meant that many, or use `search_type='sampler'`.")
  if label:
    # like the form cleans what people type (webapp/src/components/tuning/prefill.js clean_label)
    label = re.sub(r'[^\w.@:=]', '-', str(label).strip(), flags=re.ASCII)
    prefill['label'] = label
    existing = (api.get(f"commit/{selection.commit}", {'project': selection.project, 'with_outputs': 'false', 'metrics': '{}'}) or {}).get('batches') or {}
    if label in existing:
      notes.append(f"The commit already has a batch {code(label)}: starting adds runs to it. Another label keeps them apart.")
  if parameters is not None:
    prefill['search_type'] = search_type
    if search_type == 'sampler':
      prefill['n_iter'] = n_iter
  link = api.page(selection.project, 'commit', selection.commit, selected_views='tuning', prefill=json.dumps(prefill, separators=(',', ':')))
  if len(link) > MAX_LINK:
    raise EvidenceError(f"The link would be {len(link)} characters, too long to share: try fewer values, or describe them to the person.")

  runs = f"{tried if tried is not None else 1} combination(s)" + (f" × {tests} test(s) = about {(tried or 1) * tests} runs" if tests else '')
  lines = [
    f"## Run Tests / Tuning on {selection.project} {selection.commit[:8]}, filled in",
    f"Give the person this link: {link}",
    f"It fills in: batches {', '.join(code(n) for n in names)}" + (f", label {code(label)}" if label else '')
    + (f", values to try for {', '.join(code(k) for k in dict.fromkeys(k for s in (parameters if isinstance(parameters, list) else [parameters]) for k in s))}" if parameters is not None else '')
    + f". {runs}.",
    "Nothing runs until they check the form and start it. Then `my_work` and `compare` follow the batch.",
    *notes,
  ]
  return Answer('\n'.join(lines), {'view': 'link', 'url': link, 'project': selection.project, 'commit': selection.commit, 'prefill': prefill, 'tests': tests})
