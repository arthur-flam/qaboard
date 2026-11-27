"""
What `look_at_output` (qaboard/evidence.py) says of outputs that the web app's viewers read by their format rather
than as images or text: benchmarks, profiles (flame graphs), series, JUnit test reports, Plotly figures, logged media.
Each answer compares a run's file with the reference run's, compactly: the counts, then what moved most.

Types are recognized the way the web app picks a viewer (webapp/src/viewers/OutputViewer.jsx): images by their
extension, then the `type` of the qaboard.yaml visualization whose `path` matches the file, then the file's name. The
names' rules live next to the parsers (is_benchmarks, is_junit, is_flame_graph, is_series), tested on the same names
as the web app's (tests/fixtures/file-types.json). Nothing here knows what a metric or a test means: what the outputs
are about comes from the files and from qaboard.yaml.

Files are read with the parsers the web app's formats share (qaboard/benchmarks.py, flamegraphs.py, series.py,
junit.py, media.py): never another reading of their format. Plotly figures are Plotly's own JSON.

The answers' `data` is one view for all types, "table" (qaboard/mcp_app.html renders it): a title, summary lines,
`columns` and `rows` ({cells, verdict}), and `charts`, curves of [x, y] points, new and reference.
"""
import array
import base64
import binascii
import gzip
import io
import json
import math
import re
import sys
import zlib
from dataclasses import dataclass, field
from typing import Any, Callable, Dict, List, Optional, Sequence, Tuple

from .benchmarks import Benchmark, Benchmarks, DISPLAY_UNITS, is_benchmarks, parse_benchmarks
from .flamegraphs import Profile, detect_format, is_flame_graph, read_profile
from .junit import FAILING, is_junit, parse_junit
from .media import INDEX as MEDIA_INDEX, parse_media_index
from .series import is_series, parse_series
from .evidence_base import (Answer, Api, EvidenceError, TooLarge, IMAGES, TEXTS, MAX_ITEMS, code, consistency, extension, format_bytes,
                            format_change, format_number, one_line, verdict)
from .http_client import ResultsError


# Formats are read whole (a cut file doesn't parse), up to this size, compressed or not: one server answers everyone
# with `qa mcp --http`, a file that expands to gigabytes must not take its memory
FORMAT_BYTES = 32_000_000
# What the parsers raise besides ValueError on content they don't expect: of the right syntax but not of the expected
# shape (a number where a list should be...), corrupt (protobuf, gzip)
MALFORMED = (TypeError, KeyError, AttributeError, IndexError, AssertionError, EOFError, OSError, zlib.error)
# Curves in `data`, and their points: enough for a small chart
MAX_CHARTS = 4
CHART_POINTS = 60
# What the model reads of lists that aren't table rows (names of new benchmarks, files to look at...)
MAX_NAMES = 6


# ---------------------------------------------------------------------------- types

@dataclass(frozen=True)
class OutputType:
  kind: str                       # image, text, binary, other (a type in qaboard.yaml with no answer here), or a key of FORMATS
  declared: Optional[str] = None  # the `type` of the file's visualization in qaboard.yaml, e.g. benchmarks/jmh

  @property
  def label(self) -> str:
    """What the file listing says of it"""
    if self.kind in FORMATS:
      return FORMATS[self.kind].label
    if self.kind == 'other':
      return f"type {code(self.declared)}"
    return '' if self.kind == 'binary' else self.kind


def declared_kind(declared: str) -> str:
  """What viewer OutputViewer.jsx opens for a visualization's `type`"""
  if declared.startswith('image'):
    return 'image'
  if declared.startswith('flame'):  # flame/json, flame/folded...: the viewer reads the content
    return 'flame'
  if declared == 'benchmarks' or declared.startswith('benchmarks/'):
    return 'benchmarks'
  kinds = {'plotly/json': 'plotly', 'junit/xml': 'junit', 'series': 'series', 'media': 'media',
           'text/plain': 'text', 'text/html': 'text', 'plotly/html': 'text'}
  # audio, video, point clouds, bit-accuracy...: viewers of the web app, nothing a model reads here
  return kinds.get(declared, 'other')


# The web app's viewers for files without a type in qaboard.yaml, in the order OutputViewer.jsx tries them
NAMED_LIKE: Tuple[Tuple[str, Callable[[str], bool]], ...] = (
  ('plotly', lambda path: path.endswith('plotly.json')),
  ('flame', is_flame_graph),
  ('junit', is_junit),
  ('benchmarks', is_benchmarks),
  ('series', is_series),
  # the "Logged media" of runs (webapp/src/viewers/runView.js)
  ('media', lambda path: path == MEDIA_INDEX),
)


def output_type(path: str, visualizations: Sequence[Dict[str, Any]]) -> OutputType:
  """How the web app shows a run's file: the viewer it opens, which says what we can compare"""
  if extension(path) in IMAGES:
    return OutputType('image')
  declared = declared_type(path, visualizations)
  if declared:
    return OutputType(declared_kind(declared), declared)
  for kind, named_like in NAMED_LIKE:
    if named_like(path):
      return OutputType(kind)
  return OutputType('text' if extension(path) in TEXTS else 'binary')


def visualizations_of(project_data: Dict[str, Any]) -> List[Dict[str, Any]]:
  """The visualizations of a project's qaboard.yaml; `detailed_views` is their old name (webapp/src/viewers/controls.js)"""
  outputs = (project_data.get('qatools_config') or {}).get('outputs') or {}
  if not isinstance(outputs, dict):
    return []
  views = [*(outputs.get('visualizations') or []), *(outputs.get('detailed_views') or [])]
  return [view for view in views if isinstance(view, dict)]


def declared_type(path: str, visualizations: Sequence[Dict[str, Any]]) -> Optional[str]:
  for view in visualizations:
    if isinstance(view.get('type'), str) and isinstance(view.get('path'), str) and path_matches(view['path'], path):
      return view['type']
  return None


# The tokens of path-to-regexp 1.x, which reads visualizations' paths in the web app (matchPath, webapp/src/router.js):
# an escaped character, or a parameter (`:frame`, with its own regular expression in parentheses, or an unnamed
# group) with an optional prefix (/ or .) and modifier (?, *, +), or `*`
PATH_TOKEN = re.compile(r'(\\.)|([/.])?(?:(?::(\w+)(?:\(((?:\\.|[^\\()])+)\))?|\(((?:\\.|[^\\()])+)\))([+*?])?|(\*))')


def path_matches(pattern: str, path: str) -> bool:
  """Whether a visualization's `path` (e.g. `debug/:frame/out.png`, `(.*)\\.flame\\.json`) matches a file, like the web app"""
  parts, end = [], 0
  for match in PATH_TOKEN.finditer(pattern):
    escaped, prefix, _, capture, group, modifier, asterisk = match.groups()
    parts.append(re.escape(pattern[end:match.start()]))
    end = match.end()
    if escaped:
      parts.append(re.escape(escaped[1]))
      continue
    prefix = re.escape(prefix or '')
    body = capture or group or ('.*' if asterisk else f"[^{prefix or '/'}]+?")
    if modifier in ('+', '*'):
      body = f"{body}(?:{prefix}{body})*"
    parts.append(f"(?:{prefix}({body}))?" if modifier in ('?', '*') else f"{prefix}({body})")
  parts.append(re.escape(pattern[end:]))
  try:
    # not "exact", not "strict", not "sensitive": a trailing / is fine, so is a file under a folder that matches
    return re.match(''.join(parts) + r'(?:/(?=$))?(?=/|$)', path, re.IGNORECASE) is not None
  except re.error:  # JavaScript-only syntax: the web app may match it, we can't tell
    return False


# ---------------------------------------------------------------------------- answers

@dataclass
class Row:
  cells: List[str]               # the first is a name from the run: the text shows it as inline code
  verdict: Optional[str] = None  # better, worse, changed...: how the app colors the row


@dataclass
class Table:
  """What a comparison says: summary lines, then rows, the most interesting first. The answer cuts them."""
  title: str
  summary: List[str]
  columns: List[str] = field(default_factory=list)
  rows: List[Row] = field(default_factory=list)
  footer: List[str] = field(default_factory=list)
  # {name, new: [[x, y]...], reference: [[x, y]...]}
  charts: List[Dict[str, Any]] = field(default_factory=list)


@dataclass(frozen=True)
class Context:
  path: str
  declared: Optional[str]

  @property
  def format(self) -> Optional[str]:
    """The format qaboard.yaml says, e.g. `jmh` of `benchmarks/jmh`"""
    return self.declared.split('/', 1)[1] if self.declared and '/' in self.declared else None


@dataclass(frozen=True)
class Format:
  label: str
  parse: Callable[[bytes, Context], Any]    # raises ValueError when the file isn't in the format
  compare: Callable[[Any, Optional[Any], Context], Table]


class Unreadable(ValueError):
  """The file isn't in its type's format after all: look_at_output shows it as text, as the web app's viewers do"""


def look_at(api: Api, kind: OutputType, run: Dict[str, Any], ref_run: Optional[Dict[str, Any]], path: str, header: str,
            notes: List[str], data: Dict[str, Any], top: int) -> Answer:
  """The answer of look_at_output for a file in one of FORMATS. `data`: what all its answers have (ids, path, links)."""
  form = FORMATS[kind.kind]
  context = Context(path, kind.declared)
  try:
    new = parsed(api, form, run, context)
  except ValueError as e:
    raise Unreadable(f"{code(path)} isn't a {form.label} file QA-Board reads ({one_line(str(e), 200)})") from e
  ref = None
  if ref_run:
    try:
      ref = parsed(api, form, ref_run, context)
    except (EvidenceError, ValueError) as e:
      notes = [*notes, f"Can't read the reference's file ({one_line(str(e), 200)}): no comparison."]
  table = form.compare(new, ref, context)
  links = data['links']
  shown = table.rows[:top]
  lines = [f"{header}: {table.title}", *notes, *table.summary]
  if shown:
    lines += markdown_table(table.columns, shown)
  if len(table.rows) > len(shown):
    lines.append(f"… and {len(table.rows) - len(shown)} more (`top` shows more).")
  lines += table.footer
  lines.append(f"File: {links['file']}" + (f" — in the web app: {links['run']}" if links.get('run') else ''))
  charts = [{**chart, 'new': thinned(chart['new']), 'reference': thinned(chart['reference'])} for chart in table.charts[:MAX_CHARTS]]
  return Answer('\n'.join(lines), {
    **data, 'view': 'table', 'kind': kind.kind, 'title': table.title, 'summary': [*notes, *table.summary, *table.footer],
    'columns': table.columns, 'rows': [{'cells': r.cells, 'verdict': r.verdict} for r in table.rows[:MAX_ITEMS]],
    'more': max(0, len(table.rows) - MAX_ITEMS), 'charts': charts,
  })


class Expanded(TooLarge):
  """A gzipped file larger than FORMAT_BYTES once decompressed"""


def parsed(api: Api, form: Format, run: Dict[str, Any], context: Context) -> Any:
  """
  A run's file in its format. Raises ValueError when it isn't in it (whatever the parser raised on content it didn't
  expect: compare() gets only what parsers checked), EvidenceError when it can't be read.
  """
  path = context.path
  try:
    return form.parse(api.fetch(api.file_path(run['output_dir_url'], path), max_bytes=FORMAT_BYTES, truncate=False), context)
  except TooLarge as e:
    size = f"larger than {format_bytes(FORMAT_BYTES)}" + (' once decompressed' if isinstance(e, Expanded) else '')
    raise EvidenceError(f"{code(path)} of run {run.get('id')} is {size}: too large to compare here, open it from "
                        f"{api.file_url(run['output_dir_url'], path)}") from e
  except ResultsError as e:
    raise EvidenceError(f"Can't read {path} of run {run.get('id')}: {e}") from e
  except MALFORMED as e:
    raise ValueError(f"{type(e).__name__}: {e}") from e


def gunzipped(content: bytes) -> bytes:
  """A file's content, decompressed if it's gzipped. Never more than FORMAT_BYTES: a few kB can expand to gigabytes"""
  if content[:2] != b'\x1f\x8b':
    return content
  with gzip.GzipFile(fileobj=io.BytesIO(content)) as file:
    data = file.read(FORMAT_BYTES + 1)
  if len(data) > FORMAT_BYTES:
    raise Expanded(f"larger than {format_bytes(FORMAT_BYTES)} once decompressed")
  return data


def markdown_table(columns: Sequence[str], rows: Sequence[Row]) -> List[str]:
  def cell(text: str) -> str:
    return text.replace('|', '\\|')  # in a table, even in code
  lines = ['| ' + ' | '.join(columns) + ' |', '|' + '---|' * len(columns)]
  lines += ['| ' + ' | '.join([cell(code(row.cells[0])), *map(cell, row.cells[1:])]) + ' |' for row in rows]
  return lines


def thinned(points: List[List[float]], most: int = CHART_POINTS) -> List[List[float]]:
  """At most `most` points of a curve, evenly spread, with the last"""
  if len(points) <= most:
    return points
  stride = math.ceil(len(points) / most)
  return points[::stride] + ([points[-1]] if (len(points) - 1) % stride else [])


def relative_change(new: Optional[float], ref: Optional[float]) -> Optional[float]:
  if new is None or ref is None or not math.isfinite(new) or not math.isfinite(ref):
    return None
  if ref == 0:
    return 0.0 if new == 0 else None
  return (new - ref) / abs(ref)


def is_number(value: Any) -> bool:
  return isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(value)


def by_change(change: Optional[float]) -> float:
  """Sort key: the biggest changes first, unknown ones last"""
  return -abs(change) if change is not None and math.isfinite(change) else math.inf


def names(items: Sequence[str]) -> str:
  more = len(items) - MAX_NAMES
  return ', '.join(code(item) for item in items[:MAX_NAMES]) + (f", … and {more} more" if more > 0 else '')


def arrow(ref: Optional[float], new: Optional[float]) -> str:
  return f"{format_number(ref)} → {format_number(new)}"


def together(better: int, worse: int, what: str) -> str:
  """Whether things moved one way, said honestly: with a sign test, and only when there are enough of them"""
  moved, p = consistency(better, worse)
  return f"too few {what} to tell whether they moved one way" if p is None else f"{moved} (sign test p={p:.2g})"


# ---------------------------------------------------------------------------- benchmarks

def parse_benchmark_file(content: bytes, context: Context) -> Benchmarks:
  benchmarks = parse_benchmarks(content.decode('utf-8', errors='replace'), context.format)
  # the parsers take names and units as files give them
  for b in benchmarks:
    if not (isinstance(b.name, str) and isinstance(b.unit, str) and is_number(b.value)):
      raise ValueError(f"a benchmark without a name, a unit or a value: {one_line(repr(b.name), 60)}")
  return benchmarks


def format_measure(value: float, unit: str) -> str:
  """Times in a unit that fits them, like the benchmark viewer; other units as the file says them"""
  if unit != 's':
    return format_number(value, f" {one_line(unit, 20)}")
  for name, limit in (('ns', 1e-6), ('us', 1e-3), ('ms', 1.0)):
    if abs(value) < limit:
      return format_number(value, *DISPLAY_UNITS[name])
  return format_number(value, ' s')


# The harness's own measures of noise, the first a file has: what a change should be compared with
SPREADS = ('stddev', 'iqr', 'mad', 'error')


def relative_spread(benchmark: Benchmark) -> Optional[Tuple[str, float]]:
  stat = next((s for s in SPREADS if s in benchmark.stats), None)
  return (stat, abs(benchmark.stats[stat] / benchmark.value)) if stat and benchmark.value else None


def spread(benchmark: Benchmark) -> str:
  found = relative_spread(benchmark)
  return f"±{found[1] * 100:.1f}% {found[0]}" if found else '-'


def compare_benchmarks(new: Benchmarks, ref: Optional[Benchmarks], context: Context) -> Table:
  title = f"benchmarks ({code(new.source)})" if new.source else 'benchmarks'
  if ref is None:
    rows = [Row([b.name, format_measure(b.value, b.unit), spread(b)]) for b in sorted(new, key=lambda b: b.name)]
    return Table(title, [f"{len(new)} benchmarks."], ['benchmark', 'value', 'spread'], rows)
  ref_by_name = {b.name: b for b in ref}
  pairs = [(b, ref_by_name[b.name]) for b in new if b.name in ref_by_name]
  rows, verdicts, within_spread, speedups = [], [], 0, []
  for b, r in sorted(pairs, key=lambda pair: by_change(relative_change(pair[0].value, pair[1].value))):
    change = relative_change(b.value, r.value)
    is_better = verdict(change, b.smaller_is_better)
    verdicts.append(is_better)
    noise = max((found[1] for found in (relative_spread(b), relative_spread(r)) if found), default=None)
    if is_better in ('better', 'worse') and noise is not None and abs(change or 0) < noise:
      within_spread += 1
    if b.value > 0 and r.value > 0:
      speedups.append(r.value / b.value if b.smaller_is_better else b.value / r.value)
    rows.append(Row([b.name, format_measure(r.value, r.unit), format_measure(b.value, b.unit), f"{format_change(change)} {is_better}", spread(b)], is_better))
  better, worse = verdicts.count('better'), verdicts.count('worse')
  summary = [f"{len(new)} benchmarks, {len(pairs)} also in the reference: {better} better, {worse} worse, {verdicts.count('same')} same; "
             f"{together(better, worse, 'benchmarks')}."]
  if speedups:
    geomean = math.exp(sum(math.log(s) for s in speedups) / len(speedups))
    summary.append(f"Geometric mean speedup: {geomean:.3f}x (above 1: better). {within_spread} of the changes are smaller than their spread (the harness's stddev, IQR...): noise, maybe.")
  added = [b.name for b in new if b.name not in ref_by_name]
  new_names = {b.name for b in new}
  removed = [b.name for b in ref if b.name not in new_names]
  if added:
    summary.append(f"Only in this run: {names(added)}.")
  if removed:
    summary.append(f"Only in the reference: {names(removed)}.")
  machines = new.context.get('machine') or new.context.get('cpu'), ref.context.get('machine') or ref.context.get('cpu')
  if all(machines) and machines[0] != machines[1]:
    summary.append(f"Measured on different machines ({code(machines[1])} for the reference, {code(machines[0])} here): times may not compare.")
  return Table(title, summary, ['benchmark', 'reference', 'new', 'change', 'spread (new)'], rows)


# ---------------------------------------------------------------------------- flame graphs

# What qaboard/flamegraphs.py reads; the web app's viewer also reads speedscope, Chrome's .cpuprofile, *flame.json
READ_PROFILES = ('folded', 'perf-script', 'pprof')
# Shares of the total that moved less than this (half a point) didn't move
SHARE_MOVED = 0.005


def parse_profile(content: bytes, context: Context) -> Tuple[str, Optional[Profile]]:
  """The profile's format, and the profile if we read that format"""
  # detect_format reads the first kB of gzipped files, read_profile all of them: we decompress, with a limit
  profile_format = detect_format(content, context.path)
  return profile_format, read_profile(gunzipped(content), profile_format) if profile_format in READ_PROFILES else None


def function_shares(profile: Profile) -> Dict[str, Tuple[float, float]]:
  """{function: (its own share of the total, the share of the stacks it is in)}"""
  total = profile.total or 1.0
  own: Dict[str, float] = {}
  inside: Dict[str, float] = {}
  for stack, value in profile.stacks.items():
    frames = stack.split(';')
    own[frames[-1]] = own.get(frames[-1], 0.0) + value
    for frame in set(frames):
      inside[frame] = inside.get(frame, 0.0) + value
  return {frame: (own.get(frame, 0.0) / total, value / total) for frame, value in inside.items()}


def percent(share: Optional[float]) -> str:
  return '-' if share is None else f"{share * 100:.1f}%"


def compare_profiles(new: Tuple[str, Optional[Profile]], ref: Optional[Tuple[str, Optional[Profile]]], context: Context) -> Table:
  profile_format, profile = new
  if profile is None:
    return Table(f"flame graph ({profile_format})", [f"Only the web app's flame graph viewer reads {profile_format} profiles: open the file there."])
  unit = code(one_line(profile.unit, 40))
  title = f"flame graph ({profile_format}, {unit})"
  shares = function_shares(profile)
  ref_profile = ref[1] if ref else None
  if ref_profile is None:
    ranked = sorted(shares.items(), key=lambda item: -item[1][0])
    rows = [Row([frame, percent(own), percent(inside)]) for frame, (own, inside) in ranked]
    return Table(title, [f"{len(profile.stacks)} stacks, {len(shares)} functions, total {format_number(profile.total)} {unit}. The functions that take the most time themselves:"],
                 ['function', 'self', 'total'], rows)
  ref_shares = function_shares(ref_profile)
  def moved(frame: str) -> float:
    (own, inside), (ref_own, ref_inside) = shares.get(frame, (0.0, 0.0)), ref_shares.get(frame, (0.0, 0.0))
    return max(abs(own - ref_own), abs(inside - ref_inside))
  rows = []
  for frame in sorted(set(shares) | set(ref_shares), key=lambda f: -moved(f)):
    own, inside = shares.get(frame, (None, None))
    ref_own, ref_inside = ref_shares.get(frame, (None, None))
    delta = (own or 0.0) - (ref_own or 0.0)
    rows.append(Row([frame, percent(ref_own), percent(own), f"{delta * 100:+.1f} pts", percent(ref_inside), percent(inside)],
                    'changed' if moved(frame) > SHARE_MOVED else 'same'))
  summary = [f"Total: {format_number(ref_profile.total)} → {format_number(profile.total)} {unit} ({format_change(relative_change(profile.total, ref_profile.total))})"
             + ("; samples depend on how long the profiler ran: compare the shares." if profile.unit == 'samples' else '.'),
             f"{len(shares)} functions ({len(set(shares) - set(ref_shares))} new, {len(set(ref_shares) - set(shares))} gone). "
             "The functions whose share of the total changed most, by the time they take themselves (self) or with what they call (total):"]
  if ref_profile.unit != profile.unit:
    summary.insert(0, f"The reference's unit is {code(ref_profile.unit)}: totals don't compare.")
  return Table(title, summary, ['function', 'self (ref)', 'self (new)', 'self change', 'total (ref)', 'total (new)'], rows)


# ---------------------------------------------------------------------------- series

def parse_series_file(content: bytes, context: Context) -> Dict[str, Any]:
  return parse_series(content.decode('utf-8', errors='replace'))['series']


def curve(series: Dict[str, Any]) -> List[List[float]]:
  return [[step, value] for step, value in zip(series['step'], series['value']) if value is not None and is_number(step)]


def extremes(series: Dict[str, Any]) -> Tuple[Optional[float], Optional[float]]:
  """The lowest and highest values: series.json keeps them per bucket of points when it keeps means"""
  lows = [v for v in series.get('min', series['value']) if v is not None]
  highs = [v for v in series.get('max', series['value']) if v is not None]
  return (min(lows) if lows else None), (max(highs) if highs else None)


def last(series: Optional[Dict[str, Any]]) -> Optional[float]:
  values = [v for v in series['value'] if v is not None] if series else []
  return values[-1] if values else None


def compare_series(new: Dict[str, Any], ref: Optional[Dict[str, Any]], context: Context) -> Table:
  title = 'series'
  summary = [f"{len(new)} series" + (f", {len([k for k in new if k in ref])} also in the reference." if ref is not None else '.')]
  if any('min' in s for s in new.values()):
    summary.append("Long series are kept as means of buckets of points: the last value is the last bucket's mean; min and max are exact.")
  if ref is None:
    rows = [Row([key, format_number(last(s)), *map(format_number, extremes(s)), str(s['points'])]) for key, s in new.items()]
    return Table(title, summary, ['series', 'last', 'min', 'max', 'points'], rows,
                 charts=[{'name': key, 'new': curve(s), 'reference': []} for key, s in new.items()])
  keys = sorted(new, key=lambda key: by_change(relative_change(last(new[key]), last(ref.get(key)))))
  rows, charts = [], []
  for key in keys:
    s, r = new[key], ref.get(key)
    change = relative_change(last(s), last(r))
    (low, high), (ref_low, ref_high) = extremes(s), extremes(r) if r else (None, None)
    rows.append(Row([key, format_number(last(r)), format_number(last(s)), format_change(change), arrow(ref_low, low), arrow(ref_high, high),
                     f"{r['points'] if r else 0} → {s['points']}"], verdict(change, None)))
    charts.append({'name': key, 'new': curve(s), 'reference': curve(r) if r else []})
  gone = [key for key in ref if key not in new]
  if gone:
    summary.append(f"Only in the reference: {names(gone)}.")
  summary.append("QA-Board doesn't know which way is better for series: lower or higher, say it from what they measure.")
  return Table(title, summary, ['series', 'last (ref)', 'last (new)', 'change', 'min (ref → new)', 'max (ref → new)', 'points'], rows, charts=charts)


# ---------------------------------------------------------------------------- JUnit test reports

def parse_report(content: bytes, context: Context) -> List[Dict[str, Any]]:
  return parse_junit(content, context.path)


# What changed for a test, the most important first, and how the app colors it
TEST_CHANGES = (('broken', 'worse'), ('new and failing', 'worse'), ('still failing', 'worse'), ('fixed', 'better'),
                ('flaky', 'changed'), ('skipped now', 'changed'), ('removed', 'changed'), ('new', None))


def status_change(status: str, ref_status: Optional[str]) -> Optional[str]:
  """What changed for a test, None if nothing worth saying"""
  failing = status in FAILING
  if ref_status is None:
    return 'new and failing' if failing else 'new'
  if failing:
    return 'still failing' if ref_status in FAILING else 'broken'
  if status == 'flaky':
    return 'flaky'
  if ref_status in FAILING:
    return 'fixed' if status == 'passed' else 'skipped now'
  return 'skipped now' if status == 'skipped' != ref_status else None


def status_counts(cases: List[Dict[str, Any]]) -> str:
  counts: Dict[str, int] = {}
  for case in cases:
    counts[case['status']] = counts.get(case['status'], 0) + 1
  return ', '.join(f"{n} {status}" for status, n in sorted(counts.items(), key=lambda item: -item[1]))


def compare_reports(new: List[Dict[str, Any]], ref: Optional[List[Dict[str, Any]]], context: Context) -> Table:
  title = 'test report (JUnit)'
  if ref is None:
    failing = [c for c in new if c['status'] in (*FAILING, 'flaky')]
    rows = [Row([c['id'], c['status'], format_number(c['time'], ' s')], 'worse' if c['status'] in FAILING else 'changed') for c in failing]
    return Table(title, [f"{len(new)} tests: {status_counts(new)}. The tests that failed or were flaky:"], ['test', 'status', 'time'], rows)
  ref_status = {c['id']: c['status'] for c in ref}
  changes: Dict[str, List[Tuple[Dict[str, Any], Optional[str]]]] = {name: [] for name, _ in TEST_CHANGES}
  for case in new:
    change = status_change(case['status'], ref_status.get(case['id']))
    if change:
      changes[change].append((case, ref_status.get(case['id'])))
  new_ids = {c['id'] for c in new}
  changes['removed'] = [({'id': c['id'], 'status': '-', 'time': None}, c['status']) for c in ref if c['id'] not in new_ids]
  colors = dict(TEST_CHANGES)
  rows = [Row([case['id'], before or '-', case['status'], name, format_number(case['time'], ' s')], colors[name])
          for name, _ in TEST_CHANGES for case, before in changes[name]]
  summary = [f"{len(new)} tests: {status_counts(new)}. In the reference, {len(ref)}: {status_counts(ref)}.",
             (', '.join(f"{len(changes[name])} {name}" for name, _ in TEST_CHANGES if changes[name]) or 'No test changed status') + '.']
  return Table(title, summary, ['test', 'reference', 'new', 'change', 'time'], rows)


# ---------------------------------------------------------------------------- Plotly figures

def parse_figure(content: bytes, context: Context) -> Dict[str, Any]:
  figure = json.loads(content)
  if not isinstance(figure, dict) or not isinstance(figure.get('data'), list):
    raise ValueError("not a Plotly figure: no list of traces in `data`")
  return figure


# Plotly's typed arrays (`{"dtype": "f8", "bdata": <base64>}`, what plotly.py 6 writes for numpy arrays), as Python's
TYPED_ARRAYS = {'f8': 'd', 'f4': 'f', 'i1': 'b', 'u1': 'B', 'u1c': 'B', 'i2': 'h', 'u2': 'H', 'i4': 'i', 'u4': 'I'}


def plotly_array(value: Any) -> Optional[List[Any]]:
  """A trace's values: a list, or a typed array (little-endian, flattened), None otherwise"""
  if isinstance(value, list):
    return value
  if not isinstance(value, dict) or value.get('dtype') not in TYPED_ARRAYS or not isinstance(value.get('bdata'), str):
    return None
  values = array.array(TYPED_ARRAYS[value['dtype']])
  try:
    values.frombytes(base64.b64decode(value['bdata']))
  except (binascii.Error, ValueError):  # not base64, or not a whole number of items
    return None
  if sys.byteorder == 'big':
    values.byteswap()
  return values.tolist()


def trace_points(trace: Dict[str, Any]) -> List[List[float]]:
  """The numbers of a trace's `y`, against its `x` when they are numbers too"""
  ys, xs = plotly_array(trace.get('y')), plotly_array(trace.get('x'))
  if ys is None:
    return []
  xs = xs if isinstance(xs, list) and len(xs) == len(ys) and all(is_number(x) for x in xs) else list(range(len(ys)))
  return [[x, y] for x, y in zip(xs, ys) if is_number(y)]


def traces(figure: Dict[str, Any]) -> Dict[str, Dict[str, Any]]:
  """Traces by name, else by their position"""
  return {str(t.get('name') or f"trace {i}"): t for i, t in enumerate(t for t in figure['data'] if isinstance(t, dict))}


def last_y(trace: Optional[Dict[str, Any]]) -> Optional[float]:
  points = trace_points(trace) if trace else []
  return points[-1][1] if points else None


def compare_figures(new: Dict[str, Any], ref: Optional[Dict[str, Any]], context: Context) -> Table:
  layout = new.get('layout')
  layout_title = layout.get('title') if isinstance(layout, dict) else None
  layout_title = layout_title.get('text') if isinstance(layout_title, dict) else layout_title
  new_traces, ref_traces = traces(new), traces(ref) if ref else {}
  summary = [(f"Title: {code(one_line(str(layout_title), 120))}. " if layout_title else '') + f"{len(new_traces)} traces"
             + (f", {len(set(new_traces) & set(ref_traces))} also in the reference's figure (by name)." if ref else '.')]
  if ref is None:
    rows = [Row([name, one_line(str(t.get('type') or 'scatter'), 20), str(len(trace_points(t))), format_number(last_y(t))]) for name, t in new_traces.items()]
    charts = [{'name': name, 'new': trace_points(t), 'reference': []} for name, t in new_traces.items() if trace_points(t)]
    return Table('Plotly figure', summary, ['trace', 'type', 'points', 'last y'], rows, charts=charts)
  rows, charts = [], []
  for name in sorted(new_traces, key=lambda n: by_change(relative_change(last_y(new_traces[n]), last_y(ref_traces.get(n))))):
    trace, ref_trace = new_traces[name], ref_traces.get(name)
    points, ref_points = trace_points(trace), trace_points(ref_trace) if ref_trace else []
    change = relative_change(last_y(trace), last_y(ref_trace))
    rows.append(Row([name, one_line(str(trace.get('type') or 'scatter'), 20), f"{len(ref_points)} → {len(points)}", format_number(last_y(ref_trace)),
                     format_number(last_y(trace)), format_change(change)], verdict(change, None)))
    if points:
      charts.append({'name': name, 'new': points, 'reference': ref_points})
  return Table('Plotly figure', summary, ['trace', 'type', 'points (ref → new)', 'last y (ref)', 'last y (new)', 'change'], rows, charts=charts)


# ---------------------------------------------------------------------------- logged media

def parse_media(content: bytes, context: Context) -> List[Dict[str, Any]]:
  return parse_media_index(content.decode('utf-8', errors='replace').splitlines())


# Logged media look_at_output can compare
LOOKABLE_MEDIA = ('image', 'text', 'table', 'plotly', 'html')


def media_keys(items: List[Dict[str, Any]]) -> Dict[str, Dict[str, Any]]:
  """Per key: its kind, the steps it was logged at, and its items at each step"""
  keys: Dict[str, Dict[str, Any]] = {}
  for item in items:
    key = keys.setdefault(item['key'], {'kind': item['kind'], 'steps': {}})
    key['steps'].setdefault(item['_step'], []).append(item)
  return keys


def compare_media(new: List[Dict[str, Any]], ref: Optional[List[Dict[str, Any]]], context: Context) -> Table:
  keys, ref_keys = media_keys(new), media_keys(ref or [])
  rows, files = [], []
  for name, key in sorted(keys.items()):
    steps, ref_steps = key['steps'], ref_keys.get(name, {}).get('steps', {})
    shared = sorted(set(steps) & set(ref_steps))
    # the latest step both runs logged: their files compare
    step = shared[-1] if shared else max(steps)
    cells = [name, key['kind'], f"{len(ref_steps)} → {len(steps)}" if ref is not None else str(len(steps)),
             f"{max(ref_steps) if ref_steps else '-'} → {max(steps)}" if ref is not None else str(max(steps)), str(len(steps[max(steps)]))]
    rows.append(Row(cells, 'changed' if ref is not None and set(steps) != set(ref_steps) else None))
    if key['kind'] in LOOKABLE_MEDIA:
      files += [item['path'] for item in steps[step] if isinstance(item.get('path'), str) and item['path'] not in files]
  summary = [f"{len(new)} items logged under {len(keys)} keys" + (f"; the reference logged {len(ref or [])} under {len(ref_keys)}." if ref is not None else '.')]
  gone = sorted(set(ref_keys) - set(keys))
  if gone:
    summary.append(f"Only in the reference: {names(gone)}.")
  footer = [f"Logged files look_at_output shows (its `path`), at the latest step both runs logged, else this run's last: {names(files)}."] if files else []
  columns = ['key', 'kind', 'steps (ref → new)' if ref is not None else 'steps', 'last step (ref → new)' if ref is not None else 'last step', 'items at the last step']
  return Table('logged media', summary, columns, rows, footer)


FORMATS: Dict[str, Format] = {
  'benchmarks': Format('benchmarks', parse_benchmark_file, compare_benchmarks),
  'flame': Format('flame graph', parse_profile, compare_profiles),
  'series': Format('series', parse_series_file, compare_series),
  'junit': Format('test report (JUnit)', parse_report, compare_reports),
  'plotly': Format('Plotly figure', parse_figure, compare_figures),
  'media': Format('logged media', parse_media, compare_media),
}
