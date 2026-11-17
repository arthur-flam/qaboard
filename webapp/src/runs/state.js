// What the Runs page shows, from its address: /<project>/runs/<experiment>?scope=mine&days=7&status=running...
// The address is the only source of truth, like on the other pages (src/selection.js): links can be shared and
// reloads keep everything, down to the runs hidden and selected. qaboard.runs(url) in Python reads the same keys
// (qaboard/results.py _runs_page): keep them in sync.
import { history } from "../router";
import { parse_query, path_segment, stringify_query } from "../selection";
import { default_options } from "../viewers/series/series";

// The time windows offered, in days. Any number in the URL works.
export const WINDOWS = [1, 7, 30, 90, 365];
export const DEFAULT_DAYS = 30;
export const STATUSES = ['running', 'pending', 'failed', 'finished'];

const ids = value => String(value ?? '').split(',').map(Number).filter(id => Number.isInteger(id) && id > 0);
const list = value => String(value ?? '').split(',').map(s => s.trim()).filter(Boolean);
const text = value => typeof value === 'string' ? value : '';

// The page's address for a project and an experiment (a node of the tree, '' for all)
export const runs_path = (project, experiment = '') => {
  const node = experiment.split('/').filter(Boolean).map(path_segment).join('/');
  return `/${path_segment(project)}/runs${node ? `/${node}` : ''}`;
};

// { scope, days, status, ci, q, user, hidden, selected, group_by, lines, metrics, sort, run, compare, series }
export const runs_state = search => {
  const query = parse_query(search);
  const days = Number(query.days);
  const sort = text(query.sort);
  const smoothing = Number(query.smoothing);
  return {
    scope: query.scope === 'mine' ? 'mine' : 'all',
    days: Number.isInteger(days) && days > 0 ? days : DEFAULT_DAYS,
    status: list(query.status).filter(s => STATUSES.includes(s)),
    // the CI's runs are left out unless asked: they are not experiments
    ci: query.ci === '1',
    q: text(query.q),
    user: text(query.user),
    // the eye: runs left out of the plots
    hidden: new Set(ids(query.hidden)),
    // selected rows: the plots show only them
    selected: ids(query.selected),
    group_by: text(query.group_by),
    // grouped: each run, or the mean and range of each group
    lines: query.lines === 'mean' ? 'mean' : 'runs',
    // the metrics shown as columns, undefined: the defaults
    metrics: query.metrics === undefined ? undefined : list(query.metrics),
    // "metrics.val/loss" or "-started", the minus for descending
    sort: sort ? { key: sort.replace(/^-/, ''), desc: sort.startsWith('-') } : null,
    // the run open in the panel, and the run it is compared with
    run: ids(query.run)[0] ?? null,
    // not ?compare=: the commit page reads it as more references
    compare: ids(query.vs)[0] ?? null,
    series: {
      smoothing: Number.isFinite(smoothing) && smoothing >= 0 && smoothing < 1 ? smoothing : default_options.smoothing,
      log: query.log === '1',
      x: query.x === 'time' ? 'time' : 'step',
    },
  };
};

// The URL values of changes to the state: undefined removes a key, defaults are left out so that links stay short
const as_query = changes => {
  const query = {};
  for (const [key, value] of Object.entries(changes)) {
    if (key === 'series') {
      Object.assign(query, {
        smoothing: value.smoothing ? String(value.smoothing) : undefined,
        log: value.log ? '1' : undefined,
        x: value.x === 'time' ? 'time' : undefined,
      });
    } else if (key === 'hidden' || key === 'selected' || key === 'status') {
      const values = [...(value ?? [])];
      query[key] = values.length > 0 ? values.join(',') : undefined;
    } else if (key === 'metrics') {
      query.metrics = value === undefined ? undefined : value.join(',');
    } else if (key === 'sort') {
      query.sort = value ? `${value.desc ? '-' : ''}${value.key}` : undefined;
    } else if (key === 'ci') {
      query.ci = value ? '1' : undefined;
    } else if (key === 'scope') {
      query.scope = value === 'mine' ? 'mine' : undefined;
    } else if (key === 'days') {
      query.days = value && value !== DEFAULT_DAYS ? String(value) : undefined;
    } else if (key === 'compare') {
      query.vs = value ? String(value) : undefined;
    } else if (key === 'lines') {
      query.lines = value === 'mean' ? 'mean' : undefined;
    } else {
      query[key] = value === null || value === '' || value === undefined ? undefined : String(value);
    }
  }
  return query;
};

// The address with changes to the state: { pathname, search }. `experiment` changes the path.
export const runs_url = ({ pathname, search }, changes, { project } = {}) => {
  const { experiment: new_experiment, ...rest } = changes;
  const query = { ...parse_query(search), ...as_query(rest) };
  for (const key of Object.keys(query))
    if (query[key] === undefined) delete query[key];
  const path = new_experiment !== undefined && project ? runs_path(project, new_experiment) : pathname;
  const qs = stringify_query(query);
  return { pathname: path, search: qs ? `?${qs}` : '' };
};

// Changes the page's state. Filters and the node are new history entries (Back goes to the previous view); what
// changes as people look (hidden, hovered, smoothing...) replaces the current one.
export const updateRuns = (changes, { replace = true, project } = {}) => {
  const url = runs_url(window.location, changes, { project });
  if (url.pathname === window.location.pathname && url.search === window.location.search) return;
  (replace ? history.replace : history.push)(url);
};

// What a level's links keep: the filters and the view, not what was opened or selected in the current level
export const level_search = location => runs_url(location, { run: null, compare: null, selected: [], hidden: [] }).search;

// The project's code creates runs (the server says so), or the project says it trains: the page is in the sidebar
// and the command palette
export const project_home = project_data => project_data?.data?.qatools_config?.project?.home === 'runs' ? 'runs' : 'commits';
export const has_runs_page = project_data => !!project_data?.data?.has_runs_from_code || project_home(project_data) === 'runs';
