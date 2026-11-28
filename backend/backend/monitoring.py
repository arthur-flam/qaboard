"""
Metrics for Prometheus: `GET /metrics` on the backend's own HTTP port, off unless $QABOARD_METRICS is set.
Docs, with the list of metrics and the alerts: website/docs/backend-admin/monitoring.mdx.

- **Who can read it** (refusal()): nobody through nginx, which doesn't route /metrics anyway. Clients that connect to
  the backend itself (uwsgi's http-socket, :3001) from a private network; or, with $QABOARD_METRICS_TOKEN, whoever
  sends it as a bearer token. It's answered before the requests' other hooks: no session, no database.
- **Several processes**: uWSGI's workers, and the jobs worker next to them, each write their counters in their own
  memory-mapped files in metrics_dir(), and whoever answers /metrics adds them up (prometheus_client's multiprocess
  mode). Files are named after the uWSGI *worker id* (1..processes), not the pid: workers are recycled every hour
  or 1000 requests (uwsgi.ini), and one file per pid that ever lived would make every scrape slower. A respawned
  worker carries on with its predecessor's counters; gauges of what happens now (requests in progress, database
  connections) start again. Processes that serve nothing (the uWSGI master, `qaboard-admin` commands, migrations)
  keep their values in memory and write no file.
- **What is counted where**: requests in register(); sign-ins and batches from the audit events (audit_event());
  runs when they're flushed to the database (count_runs()); jobs in jobs/queue.py; what needs a query (runs running,
  jobs waiting, storage) in Stats, at most every $QABOARD_METRICS_STATS_SECONDS (60) per container, in the
  background: a scrape never waits for the database nor for a storage that hangs.
- **Labels are bounded**: route templates, never paths; no users, no run ids. Projects only on a few low-rate
  metrics, capped (project_label()).
- Metrics never fail a request: what goes wrong here is logged, once.
"""
import fcntl
import hmac
import ipaddress
import json
import logging
import os
import threading
import time
from pathlib import Path
from typing import Callable, Dict, Iterable, List, Optional, Tuple

from prometheus_client import CollectorRegistry, Counter, Gauge, Histogram, generate_latest, values
from prometheus_client import CONTENT_TYPE_LATEST
from prometheus_client.core import GaugeMetricFamily
from prometheus_client.mmap_dict import MmapedDict, mmap_key
from prometheus_client.multiprocess import MultiProcessCollector, mark_process_dead

from .settings import env_flag, env_list

logger = logging.getLogger(__name__)

try:
  import uwsgi as _uwsgi
except ImportError:
  _uwsgi = None


# ---------------------------------------------------------------------------------------------------------------------
# Settings: only from the environment. Who reads the metrics is a boundary that an admin session must not move.
# ---------------------------------------------------------------------------------------------------------------------
PRIVATE_NETWORKS = (
  '127.0.0.0/8', '10.0.0.0/8', '172.16.0.0/12', '192.168.0.0/16', '169.254.0.0/16',
  # shared address space: the pods of some managed Kubernetes clusters
  '100.64.0.0/10',
  '::1/128', 'fc00::/7', 'fe80::/10',
)
OTHER = '(other)'


def enabled() -> bool:
  return env_flag(os.environ.get('QABOARD_METRICS', ''))


def token() -> Optional[str]:
  return os.environ.get('QABOARD_METRICS_TOKEN', '').strip() or None


def metrics_dir() -> Path:
  """Where the processes of this container share their metrics: a local, private folder, emptied when the server starts"""
  return Path(os.environ.get('QABOARD_METRICS_DIR', '').strip() or '/tmp/qaboard-metrics')


def stats_seconds() -> float:
  try:
    return max(1.0, float(os.environ.get('QABOARD_METRICS_STATS_SECONDS', '') or 60))
  except ValueError:
    return 60.0


def max_projects() -> int:
  try:
    return max(0, int(os.environ.get('QABOARD_METRICS_MAX_PROJECTS', '') or 200))
  except ValueError:
    return 200


def allowed_networks() -> Optional[list]:
  """
  The networks that may read the metrics. None: any. $QABOARD_METRICS_ALLOWED_NETWORKS lists CIDRs or addresses,
  `private` (PRIVATE_NETWORKS) or `any`. Default: `private` without a token, `any` with one.
  """
  raw = os.environ.get('QABOARD_METRICS_ALLOWED_NETWORKS', '').strip()
  names = env_list(raw) if raw else (['any'] if token() else ['private'])
  networks = []
  for name in names:
    if name.lower() == 'any':
      return None
    for cidr in (PRIVATE_NETWORKS if name.lower() == 'private' else (name,)):
      try:
        networks.append(ipaddress.ip_network(cidr, strict=False))
      except ValueError:
        _warn_once(f'network:{cidr}', "QABOARD_METRICS_ALLOWED_NETWORKS: %r is not a network (e.g. 10.0.0.0/8): ignored", cidr)
  return networks


_warned = set()

def _warn_once(key: str, message: str, *args, exc_info=False) -> None:
  if key not in _warned:
    _warned.add(key)
    logger.warning(message, *args, exc_info=exc_info)


# ---------------------------------------------------------------------------------------------------------------------
# Who can read /metrics
# ---------------------------------------------------------------------------------------------------------------------
# What a request that came through a proxy carries. Our nginx always sends the first ones (services/nginx/uwsgi_params)
PROXY_KEYS = ('DOCUMENT_ROOT', 'HTTP_X_FORWARDED_PROTO', 'HTTP_X_FORWARDED_HOST', 'HTTP_X_FORWARDED_FOR',
              'HTTP_X_REAL_IP', 'HTTP_FORWARDED', 'HTTP_VIA')


def through_proxy(environ: dict) -> bool:
  return any(key in environ for key in PROXY_KEYS)


def in_networks(address: Optional[str], networks: Optional[list]) -> bool:
  if networks is None:
    return True
  try:
    ip = ipaddress.ip_address((address or '').split('%')[0])
  except ValueError:
    return False
  if getattr(ip, 'ipv4_mapped', None) is not None:
    ip = ip.ipv4_mapped
  return any(ip.version == network.version and ip in network for network in networks)


def refusal(environ: dict) -> Optional[Tuple[int, str]]:
  """None if the WSGI request may read the metrics, else the status and the reason to answer"""
  if not enabled():
    return 404, "Not found"
  expected = token()
  if expected:
    header = environ.get('HTTP_AUTHORIZATION', '')
    sent = header[7:].strip() if header[:7].lower() == 'bearer ' else ''
    if not sent or not hmac.compare_digest(sent.encode('utf-8'), expected.encode('utf-8')):
      return 401, "Send the metrics token: Authorization: Bearer $QABOARD_METRICS_TOKEN"
  elif through_proxy(environ):
    # the address would be the proxy's client, and anyone reaches the proxy
    return 403, "Metrics aren't served through the proxy without QABOARD_METRICS_TOKEN: scrape the backend's own port"
  if not in_networks(environ.get('REMOTE_ADDR'), allowed_networks()):
    return 403, "This address may not read the metrics: see QABOARD_METRICS_ALLOWED_NETWORKS"
  return None


def security_item() -> Optional[dict]:
  """For the admin panel's security checklist (api/admin.py): None when metrics are off, there's nothing to review"""
  if not enabled():
    return None
  if token():
    return {"ok": True, "title": "Metrics for Prometheus need a token",
            "detail": "GET /metrics on the backend's own port answers who sends $QABOARD_METRICS_TOKEN."}
  networks = os.environ.get('QABOARD_METRICS_ALLOWED_NETWORKS', '').strip() or 'private'
  open_to_all = 'any' in [n.lower() for n in env_list(networks)]
  return {"ok": False, "severity": 'warning' if open_to_all else 'info',
          "title": "Metrics for Prometheus are served without a token",
          "detail": f"Whoever reaches the backend's own port (3001, not through the proxy) from {'any address' if open_to_all else 'a private network' if networks == 'private' else networks} reads /metrics: "
                    "request rates per endpoint, the projects' names, jobs. Keep that port inside your cluster or host, or set QABOARD_METRICS_TOKEN."}


# ---------------------------------------------------------------------------------------------------------------------
# Values shared by the processes of a container
# ---------------------------------------------------------------------------------------------------------------------
_role: Optional[Tuple[str, int]] = None  # (name, pid) of a process that isn't a uWSGI worker and exports metrics
_lock = threading.Lock()
_files: Dict[str, MmapedDict] = {}
_files_identity: Optional[str] = None
_generation = 0
_values: list = []


def process_identity() -> Optional[str]:
  """
  What names this process's files, unique among the processes running now: the uWSGI worker id, or the role and
  pid of a process that said it exports metrics (serve_as). None: the process keeps its values in memory.
  """
  if not enabled():
    return None
  if _role is not None and _role[1] == os.getpid():
    return _role[0]
  if _uwsgi is not None:
    worker = _uwsgi.worker_id()
    return str(worker) if worker else None
  return None


def serve_as(role: str) -> Optional[str]:
  """
  This process, not a uWSGI worker, exports its metrics: `jobs` (the jobs worker), `app` (a server without uWSGI,
  e.g. `flask run`). Its files carry its pid: those such processes left when they died are removed.
  """
  global _role
  if not enabled() or process_identity() is not None:
    return process_identity()
  _role = (f"{role}{os.getpid()}", os.getpid())
  try:
    directory = _ensure_dir()
    for path in directory.glob('*.db'):
      identity = path.stem.rsplit('_', 1)[-1]
      if not identity.startswith(role) or not identity[len(role):].isdigit() or _alive(int(identity[len(role):])):
        continue
      path.unlink(missing_ok=True)
  except OSError as e:
    _warn_once('serve_as', "metrics: could not prepare %s: %s", metrics_dir(), e)
  return _role[0]


def _alive(pid: int) -> bool:
  try:
    os.kill(pid, 0)
  except ProcessLookupError:
    return False
  except OSError:
    return True
  return True


def _ensure_dir() -> Path:
  directory = metrics_dir()
  if not directory.is_dir():
    directory.mkdir(parents=True, exist_ok=True)
    try:
      # uWSGI's workers and the jobs worker may run as different users (UWSGI_UID): each writes its own files, and
      # only removes its own (the sticky bit), like in /tmp
      directory.chmod(0o1777)
    except OSError:
      pass
  return directory


def _file(prefix: str, identity: str) -> MmapedDict:
  """Called with _lock. The file of this process for a kind of metrics, e.g. counter_3.db"""
  global _files_identity
  if identity != _files_identity:
    # another process now: a worker forked from the master, or a test
    for f in _files.values():
      f.close()
    _files.clear()
    _files_identity = identity
  if prefix not in _files:
    _files[prefix] = MmapedDict(str(_ensure_dir() / f'{prefix}_{identity}.db'))
  return _files[prefix]


def reset_files() -> None:
  """Forget the open files: the next change opens them again. For tests, and after the folder was emptied"""
  global _files_identity, _generation
  with _lock:
    for f in _files.values():
      f.close()
    _files.clear()
    _files_identity = None
    _generation += 1


class Value:
  """
  A metric's value, as prometheus_client wants it. In the processes that export metrics (process_identity) it's also
  written to the process's file; elsewhere it stays in memory. Unlike prometheus_client's own multiprocess value, the
  file is chosen when the value changes, not when the metric is defined: the uWSGI master defines the metrics,
  its workers write them.
  """
  _multiprocess = True

  def __init__(self, typ, metric_name, name, labelnames, labelvalues, help_text, multiprocess_mode='', **kwargs):
    self._prefix = f'{typ}_{multiprocess_mode}' if typ == 'gauge' else typ
    self._key = mmap_key(metric_name, name, labelnames, labelvalues, help_text)
    self._value = 0.0
    self._timestamp = 0.0
    self._file: Optional[MmapedDict] = None
    self._bound: Optional[Tuple[Optional[str], int]] = None
    with _lock:
      _values.append(self)
      self._sync()

  def _sync(self) -> None:
    """
    Called with _lock. Binds the values to the files of the process we're now in. All of them, also those that never
    change: a histogram needs all its buckets in the file, a rate needs the counters that are still 0.
    """
    identity = process_identity()
    if self._bound == (identity, _generation):
      return
    for value in _values:
      value._bind(identity)

  def _bind(self, identity: Optional[str]) -> None:
    if self._bound == (identity, _generation):
      return
    self._bound = (identity, _generation)
    self._file = None
    if identity is None:
      return
    try:
      self._file = _file(self._prefix, identity)
      # a respawned worker carries on from the counters of the worker it replaces
      self._value, self._timestamp = self._file.read_value(self._key)
    except Exception as e:
      self._file = None
      _warn_once('file', "metrics: could not open the metrics files in %s, this process's metrics aren't exported: %s", metrics_dir(), e)

  def _write(self) -> None:
    if self._file is not None:
      try:
        self._file.write_value(self._key, self._value, self._timestamp)
      except Exception as e:
        _warn_once('write', "metrics: could not write in %s: %s", metrics_dir(), e)

  def inc(self, amount):
    with _lock:
      self._sync()
      self._value += amount
      self._timestamp = 0.0
      self._write()

  def set(self, value, timestamp=None):
    with _lock:
      self._sync()
      self._value = value
      self._timestamp = timestamp or 0.0
      self._write()

  def set_exemplar(self, exemplar):
    return

  def get(self):
    with _lock:
      self._sync()
      return self._value

  def get_exemplar(self):
    return None


# The metrics below, and their labels' children when they're first used, get our values. Nothing else in the backend's
# processes uses prometheus_client (flower has its own process).
values.ValueClass = Value


def process_started() -> None:
  """
  Called when the app is loaded, and again in each uWSGI worker after it forks.
  - The uWSGI master (the server starts, or reloads): removes the files of the previous workers.
  - A uWSGI worker: removes the gauges its predecessor left, e.g. requests in progress when it was killed, and
    the files of workers beyond `processes` (lazy-apps has no master to do it).
  """
  if not enabled() or _uwsgi is None:
    return
  try:
    directory = _ensure_dir()
    worker = _uwsgi.worker_id()
    if not worker:
      for path in directory.glob('*.db'):
        if path.stem.rsplit('_', 1)[-1].isdigit():
          path.unlink(missing_ok=True)
      (directory / Stats.FILE).unlink(missing_ok=True)
      return
    mark_process_dead(worker, str(directory))
    for path in directory.glob('*.db'):
      identity = path.stem.rsplit('_', 1)[-1]
      if identity.isdigit() and int(identity) > _uwsgi.numproc:
        path.unlink(missing_ok=True)
    _on_worker_exit()
  except Exception as e:
    _warn_once('start', "metrics: could not clean %s: %s", metrics_dir(), e)


_exit_hooked_in: Optional[int] = None

def _on_worker_exit() -> None:
  """When the worker stops: its gauges of what happens now are gone, not left at their last value"""
  global _exit_hooked_in
  if _exit_hooked_in == os.getpid():
    return
  _exit_hooked_in = os.getpid()
  previous = getattr(_uwsgi, 'atexit', None)
  def worker_exit():
    try:
      mark_process_dead(_uwsgi.worker_id(), str(metrics_dir()))
    except Exception:
      pass
    if callable(previous):
      previous()
  _uwsgi.atexit = worker_exit


# ---------------------------------------------------------------------------------------------------------------------
# The metrics. registry=None: they're read from the processes' files (render()), not from a registry.
# ---------------------------------------------------------------------------------------------------------------------
HTTP_REQUESTS = Counter('qaboard_http_requests', "Requests answered, by method, route template and status class (2xx, 4xx...)",
                        ['method', 'route', 'status'], registry=None)
HTTP_DURATION = Histogram('qaboard_http_request_duration_seconds', "Time to answer requests, in the backend (not nginx, not the queue before it), by method and route template",
                          ['method', 'route'], registry=None,
                          # API calls answer in milliseconds, exports and uploads in minutes (harakiri: 300 s)
                          buckets=(0.01, 0.025, 0.05, 0.1, 0.25, 0.5, 1, 2.5, 5, 10, 30, 120))
HTTP_IN_PROGRESS = Gauge('qaboard_http_requests_in_progress', "Requests being answered now", registry=None, multiprocess_mode='livesum')

RUNS_CREATED = Counter('qaboard_runs_created', "Runs first sent to the server (not their redos), by project", ['project'], registry=None)
RUNS_FINISHED = Counter('qaboard_runs_finished', "Runs that ended, redos included, by project and status (success, failed)", ['project', 'status'], registry=None)
UPLOAD_BYTES = Counter('qaboard_upload_bytes', "Bytes of the files runs and `qa save-artifacts` sent to the server over HTTP", registry=None)
UPLOAD_FILES = Counter('qaboard_upload_files', "Files, or parts of files that grow, sent to the server over HTTP", registry=None)
BATCHES_STARTED = Counter('qaboard_batches_started', "Batches the server was asked to start, from the web app or `qa batch --commit`, by project", ['project'], registry=None)
SIGN_INS = Counter('qaboard_sign_ins', "Sign-in attempts, by result (success, failure)", ['result'], registry=None)
TOKEN_AUTHENTICATIONS = Counter('qaboard_token_authentications', "Requests that came with an API token, by result (success, failure: unknown, revoked or expired token)", ['result'], registry=None)
AUDIT_EVENTS = Counter('qaboard_audit_events', "Audit log events recorded, by action (backend/audit_actions.py)", ['action'], registry=None)

JOBS_FINISHED = Counter('qaboard_jobs_finished', "Server jobs (Admin > Storage) that ended on this host's jobs worker, by kind and status (done, done_with_errors, failed, cancelled, requeued)",
                        ['kind', 'status'], registry=None)
JOB_DURATION = Histogram('qaboard_job_duration_seconds', "How long server jobs ran on this host's jobs worker, by kind", ['kind'], registry=None,
                         buckets=(1, 10, 60, 300, 900, 3600, 3 * 3600, 6 * 3600, 24 * 3600))

DB_POOL_CONNECTIONS = Gauge('qaboard_db_pool_connections', "Database connections of the pools of the backend's processes, by state (checked_out: in use, e.g. by a request)",
                            ['state'], registry=None, multiprocess_mode='livesum')
DB_POOL_LIMIT = Gauge('qaboard_db_pool_limit', "Most database connections the backend's running processes can open (pool size + overflow, each)",
                      registry=None, multiprocess_mode='livesum')

METHODS = {'GET', 'HEAD', 'POST', 'PUT', 'PATCH', 'DELETE', 'OPTIONS'}
UNMATCHED = '(unmatched)'


_projects: set = set()

def project_label(project_id: Optional[str]) -> str:
  """
  The `project` label: the first $QABOARD_METRICS_MAX_PROJECTS (200) projects each process sees keep their name, the
  others are counted together as "(other)", so that thousands of projects can't make millions of series.
  """
  if not project_id:
    return ''
  if project_id in _projects:
    return project_id
  if len(_projects) >= max_projects():
    return OTHER
  _projects.add(project_id)
  return project_id


def route_label(url_rule) -> str:
  """The route's template, e.g. /api/v1/output/<int:output_id>: never the path, which has ids"""
  rule = getattr(url_rule, 'rule', None)
  if not rule:
    return UNMATCHED
  return rule.rstrip('/') or '/'


def observe_request(method: str, url_rule, status_code: int, seconds: Optional[float]) -> None:
  method = method if method in METHODS else 'OTHER'
  route = route_label(url_rule)
  HTTP_REQUESTS.labels(method, route, f'{status_code // 100}xx').inc()
  if seconds is not None:
    HTTP_DURATION.labels(method, route).observe(seconds)


# ---------------------------------------------------------------------------------------------------------------------
# What the rest of the backend calls. Always safe to call: without QABOARD_METRICS they count in memory, for nobody.
# ---------------------------------------------------------------------------------------------------------------------
def audit_event(action: str, project_id: Optional[str] = None) -> None:
  """Called by audit(): the events we also count on their own are the ones operators alert on"""
  try:
    AUDIT_EVENTS.labels(action).inc()
    if action == 'auth.login':
      SIGN_INS.labels('success').inc()
    elif action == 'auth.login_failed':
      SIGN_INS.labels('failure').inc()
    elif action == 'batch.start':
      BATCHES_STARTED.labels(project_label(project_id)).inc()
  except Exception as e:
    _warn_once('audit_event', "metrics: could not count an audit event: %s", e)


def token_authentication(success: bool) -> None:
  TOKEN_AUTHENTICATIONS.labels('success' if success else 'failure').inc()


def uploaded(size: int) -> None:
  UPLOAD_FILES.inc()
  UPLOAD_BYTES.inc(max(0, size or 0))


def job_finished(kind: str, status: str, seconds: Optional[float] = None) -> None:
  """A job ended on this worker. `queued`: its worker is stopping, another one resumes it"""
  try:
    JOBS_FINISHED.labels(kind, 'requeued' if status == 'queued' else status).inc()
    if seconds is not None and status != 'queued':
      JOB_DURATION.labels(kind).observe(seconds)
  except Exception as e:
    _warn_once('job_finished', "metrics: could not count a job: %s", e)


ENDED = '_metrics_ended'

def run_ended(output) -> None:
  """Called by Output.set_status() when a run ends: counted when it's flushed, once we know whether it failed"""
  setattr(output, ENDED, True)


def _run_project(output) -> str:
  try:
    return project_label(output.batch.ci_commit.project_id)
  except Exception:
    return ''


def count_runs(session, flush_context=None, is_run: Optional[Callable] = None) -> None:
  """After the session flushed: the runs it created, and those that ended (run_ended)"""
  try:
    if is_run is None:
      from .models import Output
      is_run = lambda o: isinstance(o, Output)
    for output in session.new:
      if is_run(output):
        RUNS_CREATED.labels(_run_project(output)).inc()
    for output in [*session.new, *session.dirty]:
      if getattr(output, ENDED, False) and is_run(output):
        delattr(output, ENDED)
        RUNS_FINISHED.labels(_run_project(output), 'failed' if output.is_failed else 'success').inc()
  except Exception as e:
    _warn_once('count_runs', "metrics: could not count runs: %s", e, exc_info=True)


def install_database(engine) -> None:
  """Called by database.py: counts runs as they're saved, and the connections the pool lends"""
  try:
    from sqlalchemy import event
    from sqlalchemy.orm import Session
    if not event.contains(Session, 'after_flush', count_runs):
      event.listen(Session, 'after_flush', count_runs)
    pool = engine.pool
    limit = pool.size() + max(0, getattr(pool, '_max_overflow', 0))
    def checked_out(*args):
      DB_POOL_LIMIT.set(limit)
      DB_POOL_CONNECTIONS.labels('checked_out').set(pool.checkedout())
    def checked_in(*args):
      # the connection is counted until the pool has it back, right after this event
      DB_POOL_CONNECTIONS.labels('checked_out').set(max(0, pool.checkedout() - 1))
    event.listen(engine, 'checkout', checked_out)
    event.listen(engine, 'checkin', checked_in)
  except Exception as e:
    _warn_once('install_database', "metrics: could not watch the database: %s", e)


# ---------------------------------------------------------------------------------------------------------------------
# What needs a query: computed in the background, shared by the processes of the container
# ---------------------------------------------------------------------------------------------------------------------
Sample = Tuple[str, str, Dict[str, str], float]  # name, help, labels, value


def database_samples(execute: Callable) -> List[Sample]:
  """`execute(sql)` returns rows. Each query is cheap (indexes, small tables) and on its own: a failure loses its metrics only"""
  samples: List[Sample] = []
  def add(name, help_text, value, **labels):
    if value is not None:
      samples.append((name, help_text, labels, float(value)))
  def query(name, sql):
    try:
      return list(execute(sql))
    except Exception as e:
      _warn_once(f'query:{name}', "metrics: the query for %s failed: %s", name, e)
      return None

  up = query('database', "SELECT 1")
  add('qaboard_database_up', "Whether the backend reached its database when it last looked (see qaboard_stats_last_refresh_timestamp_seconds)", 1 if up else 0)
  if not up:
    return samples

  # the partial index on pending runs (ix_outputs_active)
  rows = query('runs', "SELECT count(*) FILTER (WHERE is_running), count(*) FROM outputs WHERE is_pending")
  if rows:
    running, pending = rows[0]
    add('qaboard_runs_active', "Runs that didn't end yet, by state (queued, running)", running or 0, state='running')
    add('qaboard_runs_active', "Runs that didn't end yet, by state (queued, running)", (pending or 0) - (running or 0), state='queued')

  rows = query('projects', "SELECT count(*) FROM projects")
  if rows:
    add('qaboard_projects', "Projects", rows[0][0])

  rows = query('jobs', "SELECT status, count(*), extract(epoch FROM min(created_at)) FROM jobs WHERE status IN ('queued', 'running') GROUP BY status")
  if rows is not None:
    active = {status: (count, oldest) for status, count, oldest in rows}
    for status in ('queued', 'running'):
      add('qaboard_jobs_active', "Server jobs (Admin > Storage) waiting for a worker or running, by status", active.get(status, (0, None))[0], status=status)
    add('qaboard_jobs_oldest_queued_timestamp_seconds', "When the job that waits for a worker the longest was created (absent: none waits)", active.get('queued', (0, None))[1])

  rows = query('jobs finished', "SELECT kind, status, extract(epoch FROM max(finished_at)) FROM jobs WHERE finished_at IS NOT NULL GROUP BY kind, status")
  for kind, status, finished in rows or []:
    add('qaboard_job_last_finished_timestamp_seconds', "When a job of this kind last ended with this status (done, done_with_errors, failed, cancelled), on any host", finished, kind=kind, status=status)

  rows = query('job schedules', "SELECT kind, count(*), extract(epoch FROM min(next_run_at)) FROM job_schedules WHERE enabled GROUP BY kind")
  for kind, count, next_run in rows or []:
    add('qaboard_job_schedules', "Enabled schedules, by kind of job", count, kind=kind)
    add('qaboard_job_schedule_next_run_timestamp_seconds', "When the next job of this kind is due. In the past for more than a few minutes: no jobs worker starts it", next_run, kind=kind)

  rows = query('jobs workers', "SELECT count(*) FROM job_workers WHERE seen_at > (now() AT TIME ZONE 'utc') - interval '2 minutes'")
  if rows:
    add('qaboard_jobs_workers', "Jobs workers that reported in the last 2 minutes, on any host", rows[0][0])

  rows = query('storage usage', "SELECT project_id, sum(bytes), sum(nb_runs) FROM storage_usage GROUP BY project_id ORDER BY 2 DESC NULLS LAST")
  if rows is not None:
    limit, usage = max_projects(), {}
    for index, (project_id, size, runs) in enumerate(rows):
      label = project_id if index < limit else OTHER
      previous = usage.get(label, (0, 0))
      usage[label] = (previous[0] + float(size or 0), previous[1] + float(runs or 0))
    for label, (size, runs) in usage.items():
      add('qaboard_storage_used_bytes', "Size of the runs' files, as Admin > Storage knows it (updated by the jobs worker), by project: the largest ones, the others together as \"(other)\"", size, project=label)
      add('qaboard_storage_runs', "Runs that have files, by project", runs, project=label)
  return samples


def disk_samples(paths: Iterable) -> List[Sample]:
  """The filesystems of the storage roots and of the data folder. A storage that hangs only holds the background thread"""
  samples: List[Sample] = []
  seen = set()
  for path in paths:
    try:
      stats = os.statvfs(path)
      device = os.stat(path).st_dev
    except OSError:
      continue
    if device in seen:
      continue
    seen.add(device)
    samples.append(('qaboard_storage_free_bytes', "Free space of the filesystems of the storage roots and of the data folder, as the backend can use it", {"path": str(path)}, float(stats.f_bavail * stats.f_frsize)))
    samples.append(('qaboard_storage_size_bytes', "Size of the filesystems of the storage roots and of the data folder", {"path": str(path)}, float(stats.f_blocks * stats.f_frsize)))
  return samples


def uwsgi_samples() -> List[Sample]:
  """uWSGI's workers by status, read now from its shared memory: "all busy" means requests wait"""
  if _uwsgi is None:
    return []
  samples: List[Sample] = []
  try:
    counts: Dict[str, int] = {}
    for worker in _uwsgi.workers():
      status = worker.get('status')
      status = status.decode() if isinstance(status, bytes) else str(status)
      counts[status] = counts.get(status, 0) + 1
    for status in {'idle', 'busy', *counts}:
      samples.append(('qaboard_uwsgi_workers', "uWSGI workers by status (idle, busy, cheap: stopped until the load needs it). No idle worker: requests wait", {"status": status}, float(counts.get(status, 0))))
    samples.append(('qaboard_uwsgi_processes', "Most workers uWSGI may run (UWSGI_PROCESSES)", {}, float(_uwsgi.numproc)))
  except Exception as e:
    _warn_once('uwsgi', "metrics: could not read uWSGI's workers: %s", e)
  return samples


class Stats:
  """
  The samples that need the database or the storage, refreshed in a background thread at most every `seconds`, by
  one process of the container at a time: the others read its file. collect() never waits for more than `wait`,
  and only when there's nothing yet: it serves what it has, however old (qaboard_stats_last_refresh_timestamp_seconds says).
  """
  FILE = 'stats.json'

  def __init__(self, fetch: Callable[[], List[Sample]], seconds: Optional[float] = None, directory: Optional[Path] = None, wait: float = 3.0):
    self.fetch = fetch
    self.seconds = seconds
    self.directory = directory
    self.wait = wait
    self.cached: Optional[dict] = None
    self.lock = threading.Lock()
    self.thread: Optional[threading.Thread] = None
    self.thread_started = 0.0
    self.abandoned = 0

  def ttl(self) -> float:
    return self.seconds if self.seconds is not None else stats_seconds()

  def path(self) -> Optional[Path]:
    return self.directory / self.FILE if self.directory is not None else None

  def fresh(self, cached: Optional[dict], now: float) -> bool:
    return cached is not None and 0 <= now - cached['at'] < self.ttl()

  def load(self) -> Optional[dict]:
    """What another process of the container computed, if it's newer than what we have"""
    path = self.path()
    if path is None:
      return self.cached
    try:
      shared = json.loads(path.read_text())
      if self.cached is None or shared['at'] > self.cached['at']:
        self.cached = shared
    except (OSError, ValueError, KeyError, TypeError):
      pass
    return self.cached

  def refresh(self) -> None:
    """Computes the samples, unless another process is at it. In a thread: see get()"""
    path = self.path()
    lock_file = None
    try:
      if path is not None:
        lock_file = open(path.with_suffix('.lock'), 'a')
        try:
          fcntl.flock(lock_file, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError:
          return
        if self.fresh(self.load(), time.time()):
          return
      started = time.time()
      samples = self.fetch()
      cached = {"at": time.time(), "seconds": round(time.time() - started, 3), "samples": samples}
      self.cached = cached
      if path is not None:
        temporary = path.with_suffix(f'.{os.getpid()}.tmp')
        temporary.write_text(json.dumps(cached))
        os.replace(temporary, path)
    except Exception as e:
      _warn_once('stats', "metrics: could not refresh the statistics: %s", e, exc_info=True)
    finally:
      if lock_file is not None:
        lock_file.close()

  def get(self) -> Optional[dict]:
    now = time.time()
    with self.lock:
      cached = self.cached
      if not self.fresh(cached, now):
        cached = self.load()
      if self.fresh(cached, now):
        return cached
      running = self.thread is not None and self.thread.is_alive()
      if running and now - self.thread_started > max(300.0, 5 * self.ttl()) and self.abandoned < 3:
        # e.g. a storage that doesn't answer: we try again a few times, then wait for it
        self.abandoned += 1
        running = False
      if not running:
        self.thread = threading.Thread(target=self.refresh, name='metrics-stats', daemon=True)
        self.thread_started = now
        self.thread.start()
      thread = self.thread
    if cached is None:
      thread.join(self.wait)
      cached = self.cached
    return self.cached if self.fresh(self.cached, time.time()) else cached

  def collect(self):
    cached = self.get()
    if cached is None:
      return
    families: Dict[str, GaugeMetricFamily] = {}
    def add(name, help_text, labels, value):
      family = families.get(name)
      if family is None:
        family = families[name] = GaugeMetricFamily(name, help_text, labels=sorted(labels))
      family.add_metric([str(labels[key]) for key in sorted(labels)], value)
    for name, help_text, labels, value in cached['samples']:
      add(name, help_text, labels, value)
    add('qaboard_stats_last_refresh_timestamp_seconds', "When the metrics that need the database or the storage were last computed", {}, cached['at'])
    add('qaboard_stats_refresh_duration_seconds', "How long computing them took", {}, cached.get('seconds', 0))
    yield from families.values()


def fetch_stats() -> List[Sample]:
  """The real thing: the database, then the disks (they can hang: the database's samples are computed first)"""
  from sqlalchemy import text
  from .database import engine
  def execute(sql):
    with engine.connect() as connection:
      return connection.execute(text(sql)).fetchall()
  samples = database_samples(execute)
  try:
    from .config import storage_roots, qaboard_data_dir
    samples += disk_samples([qaboard_data_dir, *storage_roots])
  except Exception as e:
    _warn_once('disks', "metrics: could not measure the storage: %s", e)
  return samples


_stats: Optional[Stats] = None

def stats() -> Stats:
  global _stats
  if _stats is None or _stats.directory != metrics_dir():
    _stats = Stats(fetch_stats, directory=metrics_dir())
  return _stats


class Live:
  """Samples read when Prometheus asks"""
  def collect(self):
    version = GaugeMetricFamily('qaboard_build_info', "The version of the backend's image (QABOARD_VERSION)", labels=['version'])
    version.add_metric([os.environ.get('QABOARD_VERSION') or 'unknown'], 1)
    yield version
    families: Dict[str, GaugeMetricFamily] = {}
    for name, help_text, labels, value in uwsgi_samples():
      family = families.get(name)
      if family is None:
        family = families[name] = GaugeMetricFamily(name, help_text, labels=sorted(labels))
      family.add_metric([labels[key] for key in sorted(labels)], value)
    yield from families.values()


def render() -> bytes:
  """The metrics of the container's processes, in Prometheus's text format"""
  registry = CollectorRegistry()
  MultiProcessCollector(registry, path=str(_ensure_dir()))
  registry.register(Live())
  registry.register(stats())
  return generate_latest(registry)


def wsgi_response(environ: dict) -> Tuple[int, list, bytes]:
  """status, headers, body of GET /metrics"""
  refused = refusal(environ)
  if refused is not None:
    status, reason = refused
    headers = [('Content-Type', 'text/plain; charset=utf-8')]
    if status == 401:
      headers.append(('WWW-Authenticate', 'Bearer realm="metrics"'))
    return status, headers, (reason + '\n').encode('utf-8')
  try:
    return 200, [('Content-Type', CONTENT_TYPE_LATEST), ('Cache-Control', 'no-store')], render()
  except Exception as e:
    logger.exception("metrics: could not render the metrics: %s", e)
    return 500, [('Content-Type', 'text/plain; charset=utf-8')], b"Could not read the metrics: see the backend's logs\n"


# ---------------------------------------------------------------------------------------------------------------------
# The web server
# ---------------------------------------------------------------------------------------------------------------------
PATH = '/metrics'


def register(app) -> None:
  """
  Counts and times the app's requests, and serves /metrics. Register it right after the requests' logs
  (logs.register_requests), before the hooks that authenticate: a bearer token there is ours, not a user's.
  Without QABOARD_METRICS, nothing is registered: /metrics is a 404 like any unknown page.
  """
  if not enabled():
    return
  from flask import g, request, Response

  def metrics():
    status, headers, body = wsgi_response(request.environ)
    return Response(body, status=status, headers=headers)
  app.add_url_rule(PATH, 'metrics', metrics, methods=['GET'])

  @app.before_request
  def start_request():
    # a server without uWSGI (flask run): this process serves, so it exports
    if process_identity() is None:
      serve_as('app')
    g.metrics_started = time.perf_counter()
    g.metrics_in_progress = True
    HTTP_IN_PROGRESS.inc()
    if request.path == PATH and request.method in ('GET', 'HEAD'):
      return metrics()

  @app.after_request
  def count_request(response):
    try:
      started = g.get('metrics_started')
      observe_request(request.method, request.url_rule, response.status_code,
                      time.perf_counter() - started if started is not None else None)
    except Exception as e:
      _warn_once('count_request', "metrics: could not count a request: %s", e, exc_info=True)
    return response

  @app.teardown_request
  def end_request(exception=None):
    if g.pop('metrics_in_progress', False):
      HTTP_IN_PROGRESS.dec()


# ---------------------------------------------------------------------------------------------------------------------
# The jobs worker
# ---------------------------------------------------------------------------------------------------------------------
def jobs_port() -> Optional[int]:
  try:
    return int(os.environ.get('QABOARD_METRICS_JOBS_PORT', '') or 0) or None
  except ValueError:
    _warn_once('jobs_port', "QABOARD_METRICS_JOBS_PORT must be a port number: ignored")
    return None


def start_jobs_worker():
  """
  Called by `qaboard-admin jobs worker`. Next to the web server (the image's default), its metrics are in the
  same folder: the backend's /metrics has them, there's nothing more to scrape. A worker on its own
  (QABOARD_JOBS_WORKER=0 for the backend) serves them itself on $QABOARD_METRICS_JOBS_PORT, with the same rules.
  Returns the HTTP server, if any.
  """
  if not enabled():
    return None
  serve_as('jobs')
  port = jobs_port()
  if port is None:
    return None
  from socketserver import ThreadingMixIn
  from wsgiref.simple_server import make_server, WSGIServer, WSGIRequestHandler

  class Server(ThreadingMixIn, WSGIServer):
    daemon_threads = True

  class Quiet(WSGIRequestHandler):
    def log_message(self, format, *args):
      pass

  def app(environ, start_response):
    if environ.get('PATH_INFO') != PATH:
      start_response('404 Not Found', [('Content-Type', 'text/plain; charset=utf-8')])
      return [b'Not found\n']
    status, headers, body = wsgi_response(environ)
    start_response(f"{status} {'OK' if status == 200 else 'Refused'}", headers)
    return [body]

  try:
    server = make_server(os.environ.get('QABOARD_METRICS_JOBS_ADDRESS', '') or '0.0.0.0', port, app, server_class=Server, handler_class=Quiet)
  except OSError as e:
    logger.error("metrics: could not listen on port %s (QABOARD_METRICS_JOBS_PORT): %s", port, e)
    return None
  threading.Thread(target=server.serve_forever, name='metrics-http', daemon=True).start()
  logger.info("metrics: the jobs worker serves /metrics on port %s", port)
  return server
