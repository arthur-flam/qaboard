#!/usr/bin/env python3
"""
Writes grafana-dashboard.json, next to this file: the dashboard of QA-Board's Prometheus metrics
(backend/backend/monitoring.py, website/docs/backend-admin/monitoring.mdx). Edit the panels here, then run it:

  python3 charts/qaboard/files/grafana-dashboard.py

tests/test_monitoring_files.py checks that the JSON is up to date and only uses metrics the backend exports.
"""
import json
from pathlib import Path

DATASOURCE = {"type": "prometheus", "uid": "${datasource}"}
JOB = 'job=~"$job"'
panels = []
_y = 0


def row(title):
  global _y
  panels.append({"type": "row", "title": title, "collapsed": False, "gridPos": {"h": 1, "w": 24, "x": 0, "y": _y}, "id": len(panels) + 1})
  _y += 1


def panel(kind, title, queries, x, w, h=8, unit='short', description=None, **options):
  """queries: [(PromQL, legend)]. Panels of a row are placed by their x; the row ends with end_row()"""
  targets = [{"datasource": DATASOURCE, "expr": expr, "legendFormat": legend, "refId": chr(65 + i),
              **({"instant": True, "range": False} if kind in ('stat', 'bargauge', 'table') else {})}
             for i, (expr, legend) in enumerate(queries)]
  field = {"defaults": {"unit": unit, **options.pop('defaults', {})}, "overrides": options.pop('overrides', [])}
  panels.append({
    "type": kind, "title": title, "datasource": DATASOURCE, "targets": targets, "fieldConfig": field,
    "gridPos": {"h": h, "w": w, "x": x, "y": _y}, "id": len(panels) + 1,
    **({"description": description} if description else {}), **options,
  })


def end_row(h=8):
  global _y
  _y += h


def thresholds(*steps):
  """(value, color)..., the first value is None"""
  return {"thresholds": {"mode": "absolute", "steps": [{"value": value, "color": color} for value, color in steps]},
          "color": {"mode": "thresholds"}}


def stat(title, expr, x, w=4, unit='short', description=None, **defaults):
  panel('stat', title, [(expr, '')], x, w, h=4, unit=unit, description=description, defaults=defaults,
        options={"reduceOptions": {"calcs": ["lastNotNull"]}, "colorMode": "value", "graphMode": "none"})


def timeseries(title, queries, x, w=12, unit='short', description=None, stack=False, **defaults):
  custom = {"fillOpacity": 20 if stack else 5, "showPoints": "never", **({"stacking": {"mode": "normal"}} if stack else {})}
  panel('timeseries', title, queries, x, w, unit=unit, description=description, defaults={"custom": custom, "min": 0, **defaults},
        options={"legend": {"displayMode": "table", "placement": "bottom", "calcs": ["mean", "max"]}, "tooltip": {"mode": "multi", "sort": "desc"}})


def bars(title, expr, legend, x, w=12, unit='short', description=None):
  panel('bargauge', title, [(expr, legend)], x, w, unit=unit, description=description, defaults={"min": 0, "color": {"mode": "continuous-BlPu"}},
        options={"orientation": "horizontal", "displayMode": "gradient", "reduceOptions": {"calcs": ["lastNotNull"]}})


RATE = '[$__rate_interval]'
requests = f'sum(rate(qaboard_http_requests_total{{{JOB}}}{RATE}))'
errors = f'sum(rate(qaboard_http_requests_total{{{JOB}, status="5xx"}}{RATE}))'
# API calls: not the routes that send or receive files, nor the scrapes
api = f'{JOB}, route!~".*(/files|/export|/artifacts).*|/metrics"'

row("Overview")
stat("Version", f'count by (version) (qaboard_build_info{{{JOB}}})', 0, w=3, description="Backends per version: two during a rollout",
     **{"displayName": "${__field.labels.version}"})
stat("Database", f'min(qaboard_database_up{{{JOB}}})', 3, w=3, mappings=[{"type": "value", "options": {"0": {"text": "Down", "color": "red"}, "1": {"text": "Up", "color": "green"}}}])
stat("Requests", requests, 6, w=3, unit='reqps')
stat("5xx answers", f'{errors} / {requests}', 9, w=3, unit='percentunit', **thresholds((None, 'green'), (0.01, 'orange'), (0.05, 'red')))
stat("p95 of API requests", f'histogram_quantile(0.95, sum by (le) (rate(qaboard_http_request_duration_seconds_bucket{{{api}}}{RATE})))', 12, w=3, unit='s',
     **thresholds((None, 'green'), (1, 'orange'), (2.5, 'red')))
stat("Runs running", f'max(qaboard_runs_active{{{JOB}, state="running"}})', 15, w=3)
stat("Runs queued", f'max(qaboard_runs_active{{{JOB}, state="queued"}})', 18, w=3)
stat("Jobs workers", f'max(qaboard_jobs_workers{{{JOB}}})', 21, w=3, description="Jobs workers that reported in the last 2 minutes. None: deletions, schedules and the storage usage stop",
     **thresholds((None, 'red'), (1, 'green')))
end_row(4)

row("Requests")
timeseries("Requests by status", [(f'sum by (status) (rate(qaboard_http_requests_total{{{JOB}}}{RATE}))', '{{status}}')], 0, unit='reqps', stack=True)
timeseries("Time to answer API requests", [
  (f'histogram_quantile({q}, sum by (le) (rate(qaboard_http_request_duration_seconds_bucket{{{api}}}{RATE})))', f'p{int(q * 100)}') for q in (0.5, 0.95, 0.99)
], 12, unit='s', description="In the backend: not nginx, not the wait for a free worker. Without the routes that send or receive files")
end_row()
bars("Busiest routes", f'topk(10, sum by (method, route) (rate(qaboard_http_requests_total{{{JOB}}}{RATE})))', '{{method}} {{route}}', 0, unit='reqps')
bars("Slowest routes (p95)", f'topk(10, histogram_quantile(0.95, sum by (method, route, le) (rate(qaboard_http_request_duration_seconds_bucket{{{JOB}}}{RATE}))))', '{{method}} {{route}}', 12, unit='s')
end_row()
timeseries("5xx answers by route", [(f'topk(10, sum by (method, route) (rate(qaboard_http_requests_total{{{JOB}, status="5xx"}}{RATE})))', '{{method}} {{route}}')], 0, unit='reqps')
timeseries("Time spent by route", [(f'topk(10, sum by (route) (rate(qaboard_http_request_duration_seconds_sum{{{JOB}}}{RATE})))', '{{route}}')], 12, unit='s',
           description="Seconds of work per second: what keeps the workers busy", stack=True)
end_row()

row("Capacity")
timeseries("Requests in progress", [(f'sum by (instance) (qaboard_http_requests_in_progress{{{JOB}}})', '{{instance}}')], 0, w=8)
timeseries("uWSGI workers", [(f'sum by (status) (qaboard_uwsgi_workers{{{JOB}}})', '{{status}}')], 8, w=8, stack=True,
           description="At the time of each scrape. No idle worker: requests wait. cheap: stopped until the load needs them")
timeseries("Database connections", [
  (f'sum(qaboard_db_pool_connections{{{JOB}, state="checked_out"}})', 'in use'),
  (f'sum(qaboard_db_pool_limit{{{JOB}}})', 'limit'),
], 16, w=8, description="Of the backend's pools. PostgreSQL's max_connections must cover the limit of all the replicas, twice during rollouts")
end_row()

row("Runs")
timeseries("Runs sent and ended", [
  (f'sum(rate(qaboard_runs_created_total{{{JOB}}}{RATE}))', 'created'),
  (f'sum by (status) (rate(qaboard_runs_finished_total{{{JOB}}}{RATE}))', 'ended: {{status}}'),
], 0, w=8, unit='ops')
timeseries("Runs that didn't end yet", [(f'max by (state) (qaboard_runs_active{{{JOB}}})', '{{state}}')], 8, w=8, stack=True)
timeseries("Runs ended by project", [(f'topk(10, sum by (project) (rate(qaboard_runs_finished_total{{{JOB}}}{RATE})))', '{{project}}')], 16, w=8, unit='ops', stack=True)
end_row()
timeseries("Files uploaded", [
  (f'sum(rate(qaboard_upload_bytes_total{{{JOB}}}{RATE}))', 'bytes'),
], 0, w=8, unit='Bps', description="Files that runs without a shared storage, and `qa save-artifacts`, send over HTTP")
timeseries("Batches started by the server", [(f'sum by (project) (increase(qaboard_batches_started_total{{{JOB}}}[$__interval]))', '{{project}}')], 8, w=8,
           description="From the web app (Run Tests, tuning, redo) and `qa batch --commit`", stack=True)
timeseries("Sign-ins and tokens", [
  (f'sum by (result) (rate(qaboard_sign_ins_total{{{JOB}}}{RATE}))', 'sign-in: {{result}}'),
  (f'sum by (result) (rate(qaboard_token_authentications_total{{{JOB}}}{RATE}))', 'token: {{result}}'),
], 16, w=8, unit='ops')
end_row()

row("Jobs (Admin > Storage)")
bars("Time since each kind of job last succeeded", f'time() - max by (kind) (qaboard_job_last_finished_timestamp_seconds{{{JOB}, status=~"done|done_with_errors"}})', '{{kind}}', 0, w=8, unit='s',
     description="Kinds with a schedule should stay under their period, e.g. a day for a nightly cleanup")
timeseries("Jobs waiting and running", [(f'max by (status) (qaboard_jobs_active{{{JOB}}})', '{{status}}')], 8, w=8, stack=True)
timeseries("Jobs ended", [(f'sum by (kind, status) (increase(qaboard_jobs_finished_total{{{JOB}}}[$__interval]))', '{{kind}}: {{status}}')], 16, w=8, stack=True)
end_row()

row("Storage")
timeseries("Free space", [(f'max by (path) (qaboard_storage_free_bytes{{{JOB}}} / qaboard_storage_size_bytes{{{JOB}}})', '{{path}}')], 0, w=8, unit='percentunit', max=1)
bars("Largest projects", f'topk(15, max by (project) (qaboard_storage_used_bytes{{{JOB}}}))', '{{project}}', 8, w=8, unit='bytes',
     description="As Admin > Storage knows it")
timeseries("Runs' files", [(f'sum(max by (project) (qaboard_storage_used_bytes{{{JOB}}}))', 'used')], 16, w=8, unit='bytes')
end_row()

dashboard = {
  "title": "QA-Board",
  "uid": "qaboard",
  "tags": ["qaboard"],
  "description": "QA-Board's backend: requests, capacity, runs, jobs and storage. https://samsung.github.io/qaboard/docs/backend-admin/monitoring",
  "schemaVersion": 39,
  "version": 1,
  "editable": True,
  "graphTooltip": 1,
  "time": {"from": "now-6h", "to": "now"},
  "refresh": "1m",
  "templating": {"list": [
    {"name": "datasource", "label": "Data source", "type": "datasource", "query": "prometheus", "current": {}},
    {"name": "job", "label": "Job", "type": "query", "datasource": DATASOURCE, "query": {"query": "label_values(qaboard_build_info, job)", "refId": "job"},
     "definition": "label_values(qaboard_build_info, job)", "refresh": 2, "includeAll": True, "allValue": ".*", "multi": True, "current": {"text": "All", "value": "$__all"}},
  ]},
  "panels": panels,
}


def render() -> str:
  return json.dumps(dashboard, indent=2) + '\n'


if __name__ == '__main__':
  Path(__file__).with_name('grafana-dashboard.json').write_text(render())
