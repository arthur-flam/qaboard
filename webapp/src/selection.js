// What the user is looking at: the project, the commits compared, batches, filters, views...
//
// The URL is the only source of truth: we derive the selection from it, and we "select" by navigating.
// Links can be shared, reloads keep the state, and the browser's back/forward buttons just work.
import { useMemo } from "react";
import qs from "qs";
import { DateTime } from "luxon";

import { history, useRouter, matchRoutes } from "./router";
import { route_paths } from "./route_paths";


// Names in the URL's query string
const url_keys = {
  ref_project: "ref_project",
  new_project: "new_project",
  ref_commit_id: "reference",
  selected_batch_new: "batch",
  selected_batch_ref: "batch_ref",
  filter_batch_new: "filter",
  filter_batch_ref: "filter_ref",
};

export const route_info = path => {
  const is_committer = path.startsWith('/:project_id+/committer');
  const is_commits = path.startsWith('/:project_id+/commits') || path === '/:project_id+';
  const is_commit = path.startsWith('/:project_id+/commit') && !is_commits && !is_committer;
  const is_history = path.startsWith('/:project_id+/history') || path.startsWith('/:project_id+/dashboard');
  return { is_committer, is_commits, is_commit, is_history, is_list: is_commits || is_committer };
};

const decode = value => {
  if (value === undefined || value === null) return value;
  try {
    return decodeURIComponent(value);
  } catch {
    return value;
  }
};

const as_list = value => {
  if (value === undefined || value === null) return undefined;
  if (value === '') return [];
  return Object.values([].concat(value)).flat();
};

const day = /^\d{4}-\d{2}-\d{2}$/;

// The commits we show are in this date range: ?from=YYYY-MM-DD&to=YYYY-MM-DD, by default the last 3 days
export const date_range_from_query = query => {
  const from = day.test(query.from ?? '') ? DateTime.fromISO(query.from) : DateTime.now().minus({ days: 3 });
  const to = day.test(query.to ?? '') ? DateTime.fromISO(query.to) : DateTime.now();
  return [from.startOf('day').toJSDate(), to.endOf('day').toJSDate()];
};

export const to_day = date => DateTime.fromJSDate(date).toISODate();


// Pure, so it's easy to test: (route match, location.search) => selection
export const selection_from_url = (match, search) => {
  const { path, params } = match;
  const query = qs.parse(search.replace(/^\?/, ''));
  const route = route_info(path);
  const project = decode(params.project_id) ?? query.project ?? null;

  // '' is a special value: nothing should be selected
  let new_commit_id;
  if (route.is_commit)
    new_commit_id = query.new_commit_id === '' ? '' : (decode(params.name) ?? null);
  else
    new_commit_id = query.commit ?? null;

  const selected_views = as_list(query.selected_views)?.map(v => v.replace('_', '-'));
  return {
    project,
    route,
    // the project page shows all the latest commits, or those from a branch or committer
    branch: ((route.is_commits || route.is_history) ? decode(params.name) : query.branch) || null,
    committer: decode(params.committer) || query.committer || null,

    // users can compare results from different projects
    new_project: query.new_project || project,
    ref_project: query.ref_project || project,

    new_commit_id,
    ref_commit_id: query.reference ?? query.commit_ref_folder ?? null,

    selected_batch_new: query.batch || query.batch_new || "default",
    selected_batch_ref: query.batch_ref || "default",
    filter_batch_new: query.filter ?? "",
    filter_batch_ref: query.filter_ref ?? "",

    // filter the list of commits
    search: query.search ?? "",
    sort_order: Number(query.sort_order ?? -1),
    sort_by: query.sort_by || undefined, // undefined: we use the default metric
    selected_views,
    selected_metrics: as_list(query.selected_metrics),
    date_range: date_range_from_query(query),
  };
};


export function useSelected() {
  const { match, location } = useRouter();
  return useMemo(() => selection_from_url(match, location.search), [match, location.search]);
}


const page_routes = Object.values(route_paths).map(path => ({ path }));

// Where to go to apply changes to the selection.
// Pure as well, takes the current URL: (project, changes, {pathname, search}) => {pathname, search}
export const url_for_selection = (project, selected, { pathname, search }) => {
  let query = qs.parse(search.replace(/^\?/, ''));
  const route = route_info(matchRoutes(page_routes, pathname)?.route.path ?? '');
  const on_commit_page = route.is_commit;
  // the project page lists commits, but selecting one opens it
  const on_list_page = route.is_history || (route.is_list && pathname.replace(/\/$/, '') !== `/${project}`);

  // Branches and committers have their own pages
  if ('branch' in selected || 'committer' in selected) {
    const { branch, committer, ...rest } = selected;
    delete query.branch;
    delete query.committer;
    if (committer)
      pathname = `/${project}/committer/${committer}`;
    else if (branch)
      pathname = `/${project}/commits/${branch}`;
    else
      pathname = `/${project}`;
    selected = rest;
  }

  if ('new_commit_id' in selected) {
    const { new_commit_id } = selected;
    delete query.commit;
    delete query.new_commit_id;
    if (new_commit_id === '') {
      if (on_commit_page) {
        pathname = `/${project}/commit`;
        query.new_commit_id = '';
      }
    } else if (new_commit_id) {
      // On lists of commits we stay there: e.g. hovering commits in plots selects them
      if (on_list_page && !on_commit_page)
        query.commit = new_commit_id;
      else
        pathname = `/${project}/commit/${new_commit_id}`;
    }
  }

  for (const [key, value] of Object.entries(selected)) {
    if (key === 'new_commit_id') continue;
    const url_key = url_keys[key] ?? key;
    if (value === undefined || value === null)
      delete query[url_key];
    else if (Array.isArray(value))
      query[url_key] = value.length === 0 ? '' : value;
    else if (value instanceof Date)
      query[url_key] = to_day(value);
    else
      query[url_key] = value;
  }
  // the page's project is implicit. We only look at what changes: components showing
  // another project's data (e.g. ?new_project=) may call us with that project.
  if ('new_project' in selected && query.new_project === project) delete query.new_project;
  if ('ref_project' in selected && query.ref_project === project) delete query.ref_project;
  return { pathname, search: qs.stringify(query, { arrayFormat: 'repeat' }) };
};


// Changes the selection, by navigating to a new URL.
// By default it adds a browser history entry, use {replace: true} for changes users don't want to "go back" to.
export const updateSelected = (project, selected = {}, { replace = false } = {}) => {
  const url = url_for_selection(project, selected, window.location);
  if (url.pathname === window.location.pathname && `?${url.search}` === (window.location.search || '?'))
    return;
  (replace ? history.replace : history.push)(url);
};
