"""
What the answers of `qa mcp` (qaboard/evidence.py) and its comparisons of outputs by type (qaboard/evidence_outputs.py)
both build on: QA-Board's API, the answers and their errors, how names and numbers are written, and verdicts.
Nothing here depends on MCP.
"""
import math
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Tuple
from urllib.parse import quote, urlencode, urlsplit

from .credentials import default_server, same_server, read_hosts, find_login
from .http_client import CHUNK, REQUEST_TIMEOUT, request_json
from .results import parse_url, ResultsError, _token_for
from .site_config import site_config, as_requests_verify


# Outputs the model can read as they are, when the image servers can't convert them
DIRECT_IMAGES = ('png', 'jpg', 'jpeg', 'webp', 'gif')
IMAGES = (*DIRECT_IMAGES, 'bmp', 'tif', 'tiff', 'dng', 'raw', 'hex', 'exr', 'pgm', 'ppm', 'jp2')
TEXTS = ('txt', 'log', 'json', 'jsonl', 'yaml', 'yml', 'csv', 'tsv', 'md', 'xml', 'ini', 'cfg', 'toml', 'py', 'sh', 'html', 'svg', 'out', 'err')
# A relative change below this is no change: floating point noise
EPSILON = 1e-9
# Fewer tests than this, and a sign test can't reach p < 0.05: it says nothing
MIN_SIGN_TEST = 6
# The lists in `data` are cut like the text: some hosts give it to the model too
MAX_ITEMS = 50


class EvidenceError(Exception):
  """A question we can't answer: what's missing, said for the person or agent who asked"""


class TooLarge(ResultsError):
  """A file larger than we read: truncating it would make a corrupt image"""


@dataclass
class Answer:
  text: str
  data: Dict[str, Any] = field(default_factory=dict)
  # (caption, PNG/JPEG bytes, mime type)
  images: List[Tuple[str, bytes, str]] = field(default_factory=list)


# ---------------------------------------------------------------------------- talking to the server

class Api:
  """
  QA-Board's server, with the token qa uses for it (qa auth login, QABOARD_TOKEN), or the one given.
  `public_url`: where people and clients reach it, for links, when `server` is an internal address (e.g. `qa mcp
  --http` in a container next to the backend). By default, `server`.
  """
  # Whether the cookies the server sets are sent back with the next requests (qaboard/http_client.py)
  keep_cookies = True

  def __init__(self, server: Optional[str] = None, token: Optional[str] = None, public_url: Optional[str] = None):
    self.server = (server or default_server()[0]).rstrip('/')
    self.public_url = (public_url or self.server).rstrip('/')
    self._token = token
    self._config: Optional[Dict[str, Any]] = None
    self._projects: Dict[str, Dict[str, Any]] = {}

  def is_our_page(self, url: str) -> bool:
    """Whether a web app URL is a page of this server, by its internal address or its public one"""
    server = parse_url(url).server.rstrip('/')
    return any(same_server(server, ours) or url.startswith(ours + '/') for ours in (self.server, self.public_url))

  def for_url(self, url: Optional[str]) -> 'Api':
    """
    The API of the server a web app URL names: ours, or one the person signed in to with `qa auth login`. Never
    another: credentials (e.g. a CI job's ID token, exchanged for a token) would go to whatever server a URL names.
    """
    if not url or self.is_our_page(url):
      return self
    server = parse_url(url).server.rstrip('/')
    if find_login(read_hosts(), server)[1]:
      return Api(server)
    raise EvidenceError(f"{url} is on {server}, not on {self.server}, the server `qa mcp` uses. Sign in to it with "
                        f"`qa auth login {server}`, or start `qa mcp --server {server}`.")

  def is_ours(self, url: str) -> bool:
    """Whether a URL is on this server's origin: only those get the token (image servers can be on other hosts)"""
    theirs = urlsplit(url)
    return any((theirs.scheme, theirs.netloc) == (ours.scheme, ours.netloc) for ours in (urlsplit(self.server), urlsplit(self.public_url)))

  def get(self, path: str, params: Optional[Dict[str, Any]] = None) -> Any:
    return request_json(self.server, path, params=params, token=self._token, keep_cookies=self.keep_cookies)

  def post(self, path: str, body: Dict[str, Any]) -> Any:
    # not a write: e.g. "where do these images differ" computes and caches (a "read" token is enough)
    return request_json(self.server, path, method='POST', json=body, token=self._token, keep_cookies=self.keep_cookies)

  def _headers(self, extra: Optional[Dict[str, str]] = None) -> Dict[str, str]:
    token = _token_for(self.server, self._token)
    return {**({'Authorization': f"Bearer {token}"} if token else {}), **(extra or {})}

  def fetch(self, url_path: str, tail: Optional[int] = None, max_bytes: Optional[int] = None, truncate: bool = True) -> bytes:
    """
    A file the server serves, e.g. /s/<run's folder>/log.txt or /iiif/2/…. `tail`: only the last bytes. `max_bytes`:
    only the first bytes, or raise TooLarge if not `truncate` (a cut image is a corrupt one).
    """
    import requests
    url = url_path if url_path.startswith('http') else f"{self.server}{url_path}"
    extra = {'Range': f"bytes=-{tail}"} if tail else ({'Range': f"bytes=0-{max_bytes}"} if max_bytes else {})
    headers = self._headers(extra) if self.is_ours(url) else extra
    try:
      # streamed: servers that ignore Range send the whole file, we keep no more of it than asked
      with requests.get(url, headers=headers, timeout=REQUEST_TIMEOUT, verify=as_requests_verify(site_config('QABOARD_API_VERIFY')), stream=True) as r:
        if r.status_code == 416:  # an empty file
          return b''
        if r.status_code >= 400:
          raise ResultsError(f"{url} answered {r.status_code}", status=r.status_code)
        content = last_bytes(r, tail) if tail and r.status_code == 200 else first_bytes(r, max_bytes)
    except requests.RequestException as e:
      raise ResultsError(f"Could not reach {url}: {e}") from e
    if max_bytes and len(content) > max_bytes:
      if not truncate:
        raise TooLarge(f"{url} is larger than {format_bytes(max_bytes)}")
      content = content[:max_bytes]
    return content

  def config(self) -> Dict[str, Any]:
    """The server's settings for clients. Failures aren't remembered: the server may be restarting"""
    if self._config is None:
      try:
        self._config = self.get('config') or {}
      except ResultsError:
        return {}
    return self._config

  def project(self, project: str) -> Dict[str, Any]:
    """The project's data: its qaboard.yaml (qatools_config), metrics (qatools_metrics, metrics_definitions)"""
    if project not in self._projects:
      try:
        self._projects[project] = self.get('project', {'project': project}) or {}
      except ResultsError:
        return {}
    return self._projects[project]

  # ---- the web app's pages, for people (webapp/src/selection.js, route_paths.js)
  def page(self, project: str, kind: str, name: Optional[str] = None, **query: Any) -> str:
    path = f"{self.public_url}/{quote(project, safe='/')}/{kind}"
    if name:
      path += f"/{quote(str(name), safe='/')}"
    query = {k: v for k, v in query.items() if v not in (None, '')}
    return f"{path}?{urlencode(query, doseq=True)}" if query else path

  def metrics_diff_url(self, project: str, commit: str, label: str, reference: Optional[str], reference_label: Optional[str]) -> str:
    return self.page(project, 'commit', commit, reference=reference, batch=label, batch_ref=reference_label or label, selected_views='table-compare')

  def run_url(self, project: str, commit: str, label: str, run_id: Any, reference: Optional[str] = None, reference_label: Optional[str] = None) -> str:
    # filters are regular expressions, the text searched ends with the run's id (like Run.url)
    return self.page(project, 'commit', commit, batch=label, filter=f"id:{run_id}$", reference=reference, batch_ref=reference_label)

  def file_path(self, output_dir_url: str, path: str) -> str:
    """A run's file, as a path on the server, to fetch()"""
    return f"{output_dir_url.rstrip('/')}/{quote(path)}"

  def file_url(self, output_dir_url: str, path: str) -> str:
    """A run's file, for people"""
    return f"{self.public_url}{self.file_path(output_dir_url, path)}"


def first_bytes(r: Any, most: Optional[int]) -> bytes:
  """A streamed answer's content, all of it, or one byte more than `most`: enough to tell it's larger"""
  if most is None:
    return r.content
  content = bytearray()
  for chunk in r.iter_content(CHUNK):
    content += chunk
    if len(content) > most:
      break
  return bytes(content[:most + 1])


def last_bytes(r: Any, most: int) -> bytes:
  """A streamed answer's last `most` bytes"""
  content = b''
  for chunk in r.iter_content(CHUNK):
    content = (content + chunk)[-most:]
  return content


# ---------------------------------------------------------------------------- writing names and numbers

def code(text: Any) -> str:
  """A name from runs (a test, a file) as inline code: it can't break out of it, nor span lines"""
  return '`' + ' '.join(str(text).replace('`', "'").split()) + '`'


def one_line(text: Optional[str], length: int = 90) -> str:
  """The first line of a text (e.g. a commit message's title), cut"""
  text = ' '.join(((text or '').strip().splitlines() or [''])[0].split())
  return text if len(text) <= length else text[:length - 1] + '…'


def format_number(value: Optional[float], suffix: str = '', scale: float = 1.0) -> str:
  if value is None or not isinstance(value, (int, float)) or isinstance(value, bool) or not math.isfinite(value):
    return '-'
  value = value * (scale or 1.0)
  magnitude = abs(value)
  if magnitude == 0:
    text = '0'
  elif magnitude >= 1000 or magnitude < 0.001:
    text = f"{value:.4g}"
  else:
    text = f"{value:.4g}" if magnitude < 1 else f"{value:.{max(0, 3 - int(math.log10(magnitude)))}f}"
  return f"{text}{suffix}"


def format_change(relative: Optional[float]) -> str:
  if relative is None or not math.isfinite(relative):
    return '-'
  return f"{relative * 100:+.2f}%" if abs(relative) < 10 else f"{relative:+.1f}x"


def format_bytes(size: Optional[int]) -> str:
  if size is None:
    return '?'
  for unit in ('B', 'kB', 'MB', 'GB'):
    if size < 1000 or unit == 'GB':
      return f"{size:.0f} {unit}" if unit == 'B' else f"{size:.1f} {unit}"
    size = size / 1000  # type: ignore[assignment]
  return f"{size}"


def extension(path: str) -> str:
  return path.rsplit('.', 1)[-1].lower() if '.' in path.rsplit('/', 1)[-1] else ''


# ---------------------------------------------------------------------------- verdicts

def sign_test(better: int, worse: int) -> Optional[float]:
  """
  Two-sided p-value that tests are as likely to get better as worse (ties left out). A small value says the change is
  consistent across tests; it says nothing of its size, and repeated runs of a noisy metric aren't independent tests.
  """
  n = better + worse
  if n == 0:
    return None
  k = min(better, worse)
  if n > 400:
    # normal approximation, with continuity correction: exact sums of big binomials take seconds
    z = (n / 2 - (k + 0.5)) / math.sqrt(n / 4)
    return min(1.0, math.erfc(z / math.sqrt(2)))
  tail = sum(math.comb(n, i) for i in range(k + 1)) / 2 ** n
  return min(1.0, 2 * tail)


def consistency(better: int, worse: int, threshold: float = 0.05) -> Tuple[str, Optional[float]]:
  """Whether tests moved consistently one way: consistently better/worse, mixed, or too few tests to say"""
  if better + worse < MIN_SIGN_TEST:
    return 'too few tests', None
  p = sign_test(better, worse)
  assert p is not None
  if p >= threshold:
    return 'mixed', p
  return ('consistently better' if better > worse else 'consistently worse'), p


def verdict(relative: Optional[float], smaller_is_better: Optional[bool]) -> str:
  """better, worse, same, changed (QA-Board doesn't know which way is better) or unknown"""
  if relative is None or not math.isfinite(relative):
    return 'unknown'
  if abs(relative) <= EPSILON:
    return 'same'
  if smaller_is_better is None:
    return 'changed'
  return 'better' if (relative < 0) == smaller_is_better else 'worse'
