// Hooks that components use to read the app's state: server data from TanStack Query,
// the selection from the URL, preferences from the zustand store.
import { useEffect, useMemo, useRef, useState } from "react";
import { useInfiniteQuery, useQuery, useQueryClient } from "@tanstack/react-query";

import {
  siteConfigQuery, default_site_config, userQuery, logged_out_user,
  projectsQuery, projectQuery, branchesQuery, commitsQuery, commitsPagesQuery, commits_of, commits_page_size, commitQuery,
  findCachedCommit, invalidateCommit,
} from "./api/queries";
import { usePrefsStore, usePrivateMilestones } from "./stores/prefs";
import { useSelected, updateSelected } from "./selection";
import { compute_batches, compute_config } from "./selectors/batches";
import { default_project } from "./defaults";

export { useSelected, updateSelected };


export const useSiteConfig = () => useQuery(siteConfigQuery).data ?? default_site_config;

export const useUser = () => useQuery(userQuery).data ?? logged_out_user;

export const useProjects = () => useQuery(projectsQuery);

// The project's most recently active branches matching `search`
export const useBranches = (project, search, { enabled = true } = {}) => useQuery({ ...branchesQuery(project, search), enabled: !!project && enabled });


// A project's metadata and configuration, with the user's preferences
export function useProjectData(project) {
  const { data: from_list } = useQuery({ ...projectsQuery, select: projects => projects?.[project] });
  const { data: from_project } = useQuery(projectQuery(project));
  const is_favorite = usePrefsStore(state => !!state.favorites[project]);
  const milestones = usePrivateMilestones(project);
  return useMemo(() => {
    // The list has dates and counts, the project its whole configuration
    return {
      ...default_project,
      ...from_list,
      ...from_project,
      data: { ...default_project.data, ...from_list?.data, ...from_project?.data },
      is_favorite,
      milestones,
    };
  }, [from_list, from_project, is_favorite, milestones]);
}


// We give the same object to all components for the same commit data, which keeps memoization working
const decorated = new WeakMap();
const decorate_commit = (data, is_loaded, error) => {
  const key = `${is_loaded}|${error}`;
  let by_state = decorated.get(data);
  if (!by_state) decorated.set(data, by_state = new Map());
  if (!by_state.has(key)) by_state.set(key, { ...data, is_loaded, error });
  return by_state.get(key);
};

const commit_error = error => {
  if (!error) return undefined;
  const { status, data } = error.response ?? {};
  if (status === undefined) return error.message ?? String(error);
  return `${status} ${typeof data === 'string' ? data : JSON.stringify(data)}`;
};


// A commit and its batches. Without an id: the latest commit on the branch (by default the project's reference branch).
// While it loads, we show what we know about it from lists of commits.
// Returns undefined when there is nothing to show, null when id is '' (meaning: nothing selected).
export function useCommit({ project, id, branch, enabled = true, placeholder }) {
  const queryClient = useQueryClient();
  const query = useQuery({
    ...commitQuery({ project, id, branch }),
    enabled: enabled && !!project && id !== '',
    placeholderData: () => placeholder ?? findCachedCommit(queryClient, project, id),
  });
  const { data, isPlaceholderData, error, isFetched } = query;
  const is_loaded = isFetched && !isPlaceholderData;
  const error_str = commit_error(error);
  return useMemo(() => {
    if (id === '') return null;
    if (data) return decorate_commit(data, is_loaded, error_str);
    if (error_str) return { id, batches: {}, is_loaded, error: error_str };
    if (enabled && project) return { id, batches: {}, is_loaded: false };
    return undefined;
  }, [id, data, is_loaded, error_str, enabled, project]);
}

// To refresh a commit after acting on it: refresh(project, id)
export function useRefreshCommit() {
  const queryClient = useQueryClient();
  return (project, id) => invalidateCommit(queryClient, project, id);
}


const aggregation_metrics = (qatools_metrics, { dashboard }) => {
  const { available_metrics = {}, main_metrics = [], dashboard_metrics } = qatools_metrics ?? {};
  const keys = dashboard ? (dashboard_metrics ?? main_metrics) : main_metrics;
  return Object.fromEntries(keys
    .filter(m => available_metrics[m] !== undefined)
    .map(m => [m, available_metrics[m].target ?? 0]));
};

const has_batches = c => Object.keys(c.batches ?? {}).length > 0;

// Pages can overlap when new commits arrive between their requests
const unique_commits = commits => {
  const seen = new Set();
  return commits.filter(c => !seen.has(c.id) && seen.add(c.id));
};

// The commits shown on the current page: the project's latest, a branch's or a committer's,
// in the date range from the URL. On the History page they come with all their outputs.
// On lists of commits they come in pages (fetchNextPage, hasNextPage...), and the `search` from the URL
// searches all the commits on the server. `ignore_search` gives the list without the search,
// e.g. for the commits selected by default.
export function useCommitsList({ enabled = true, refetchInterval, ignore_search = false } = {}) {
  const selected = useSelected();
  const { project, branch, committer, date_range, route, selected_batch_new } = selected;
  const paginated = !route.is_history;
  const search = paginated && !ignore_search ? selected.search.trim() : '';
  const project_data = useProjectData(project);
  const dashboard = route.is_history;
  // On branches, metrics can differ from the project's: once we know the latest commit, we ask for its metrics.
  const [branch_metrics, setBranchMetrics] = useState({ key: null, qatools_metrics: undefined });
  const list_key = `${project}|${branch}`;
  const qatools_metrics = (branch && branch_metrics.key === list_key && branch_metrics.qatools_metrics) || project_data.data?.qatools_metrics;
  const metrics = useMemo(
    () => aggregation_metrics(qatools_metrics, { dashboard }),
    [qatools_metrics, dashboard],
  );
  const params = {
    project,
    branch: branch ?? undefined,
    committer: committer ?? undefined,
    // searches look at all the commits
    from: search ? undefined : date_range[0].toISOString(),
    to: search ? undefined : date_range[1].toISOString(),
    metrics,
    ...(dashboard ? { with_outputs: true, only_ci_batches: selected_batch_new === 'default' } : {}),
  };
  const all_query = useQuery({ ...commitsQuery(params), enabled: enabled && !!project && !paginated, refetchInterval });
  // Branch pages show their metrics over time: we get the whole date range at once
  const page_size = branch && !search ? 1000 : commits_page_size;
  // Infinite queries refetch all their pages one after the other: we only refresh lists users didn't scroll through
  const pages_refetch_interval = refetchInterval && (query => (query.state.data?.pages?.length ?? 0) > 1 ? false : refetchInterval);
  const pages_query = useInfiniteQuery({ ...commitsPagesQuery({ ...params, q: search || undefined, page_size }), enabled: enabled && !!project && paginated, refetchInterval: pages_refetch_interval });
  const query = paginated ? pages_query : all_query;

  const default_branch = project_data.data?.git?.default_branch ?? project_data.data?.qatools_config?.project?.reference_branch;
  const { data: query_data, dataUpdatedAt, isPlaceholderData } = query;
  const data = useMemo(() => paginated ? unique_commits(commits_of(query_data)) : query_data, [paginated, query_data]);
  const latest_metrics = branch && !search && !isPlaceholderData ? data?.[0]?.data?.qatools_metrics : undefined;
  if (latest_metrics && (branch_metrics.key !== list_key || branch_metrics.qatools_metrics !== latest_metrics))
    setBranchMetrics({ key: list_key, qatools_metrics: latest_metrics });
  const derived = useMemo(() => {
    const all = data ?? [];
    // Hide commits without results, except recent ones: their CI may still be running
    const commits = all.some(has_batches)
      ? all.filter(c => dataUpdatedAt - new Date(c.authored_datetime) < 15 * 1000 || has_batches(c))
      : all;
    const latest_commit = branch ? all[0] : all.find(c => c.branch === default_branch);
    return { commits, ids: all.map(c => c.id), latest_commit };
  }, [data, dataUpdatedAt, branch, default_branch]);

  // The project's latest commit on its default branch may not be in the pages we loaded:
  // other branches can have many more recent commits. We then ask for it.
  const wants_latest = enabled && !!project && paginated && !branch && !committer && !!default_branch && query.isSuccess && !derived.latest_commit;
  const { data: latest_on_default } = useQuery({
    ...commitsQuery({ project, branch: default_branch, from: params.from, to: params.to, metrics, limit: 1 }),
    enabled: wants_latest,
    refetchInterval,
  });
  const latest_commit = derived.latest_commit ?? (wants_latest ? latest_on_default?.[0] : undefined);

  return { ...query, data, ...derived, latest_commit, date_range, project, search, paginated };
}


// The commits we compare: "new" and "reference".
// On the commit page they are fetched with all their outputs, elsewhere they come from the list of commits.
export function useSelectedCommits() {
  const selected = useSelected();
  const on_commit_page = selected.route.is_commit;
  const list = useCommitsList({ enabled: !on_commit_page, ignore_search: true });
  const pick = (id, index) => {
    if (id === '') return '';
    if (on_commit_page) return id ?? undefined;
    return id ?? list.ids[index];
  };
  const new_id = pick(selected.new_commit_id, 0);
  const ref_id = pick(selected.ref_commit_id, 1);
  const from_list = id => on_commit_page ? undefined : list.data?.find(c => c.id === id);
  const new_commit = useCommit({ project: selected.new_project, id: new_id, enabled: on_commit_page, placeholder: from_list(new_id) });
  const ref_commit = useCommit({ project: selected.ref_project, id: ref_id, enabled: on_commit_page, placeholder: from_list(ref_id) });
  return { new_commit, ref_commit };
}


// Commits are often selected by a short id, or as "the latest on a branch".
// Once we know their full id, we put it in the URL, so that the page can be shared.
export function useCommitIdsInUrl({ new_commit, ref_commit }) {
  const queryClient = useQueryClient();
  const selected = useSelected();
  const { project, new_project, ref_project, new_commit_id, ref_commit_id } = selected;
  const sync = (commit, commit_project, selected_id, attribute) => {
    if (!commit?.is_loaded || commit.error || !commit.id) return;
    if (selected_id === '' || selected_id === commit.id) return;
    if (selected_id && !commit.id.startsWith(selected_id)) return;
    const { is_loaded: _is_loaded, error: _error, ...data } = commit;
    queryClient.setQueryData(commitQuery({ project: commit_project, id: commit.id }).queryKey, data);
    updateSelected(project, { [attribute]: commit.id }, { replace: true });
  };
  useEffect(() => sync(new_commit, new_project, new_commit_id, 'new_commit_id'));
  useEffect(() => sync(ref_commit, ref_project, ref_commit_id, 'ref_commit_id'));
}


// Remembers the last result, shared by all components: computing batches is costly with many outputs.
const memoize_last = fn => {
  let last_args, last_result;
  return (...args) => {
    if (last_args && args.length === last_args.length && args.every((a, i) => Object.is(a, last_args[i])))
      return last_result;
    last_args = args;
    return last_result = fn(...args);
  };
};

const memo_batches = memoize_last((new_commit, ref_commit, selected_batch_new, selected_batch_ref, filter_batch_new, filter_batch_ref, sort_by, sort_order, default_metric) =>
  compute_batches({
    new_commit, ref_commit,
    selected: { selected_batch_new, selected_batch_ref, filter_batch_new, filter_batch_ref, sort_by, sort_order },
    metrics: { default_metric },
  })
);


// Everything about the comparison of the new and reference commits: commits, batches, configuration and metrics
export function useComparison() {
  const selected = useSelected();
  const { project, new_project } = selected;
  const project_data = useProjectData(project);
  const new_project_data = useProjectData(new_project);
  const { new_commit, ref_commit } = useSelectedCommits();

  const pre_config = compute_config({ new_batch: undefined, new_commit, project_data: new_project_data, selected });
  const batches = memo_batches(
    new_commit || undefined, ref_commit || undefined,
    selected.selected_batch_new, selected.selected_batch_ref,
    selected.filter_batch_new, selected.filter_batch_ref,
    selected.sort_by, selected.sort_order, pre_config.metrics.default_metric,
  );
  const config = useMemo(
    () => compute_config({ new_batch: batches.new_batch, new_commit, project_data: new_project_data, selected }),
    [batches.new_batch, new_commit, new_project_data, selected],
  );
  const selected_views = useMemo(() => {
    const views = selected.selected_views ?? [].concat(config.config.outputs?.default_tab_details ?? project_data.data?.qatools_config?.outputs?.default_tab_details ?? 'summary');
    return views.map(v => v.replace('_', '-'));
  }, [selected.selected_views, config.config, project_data]);

  return { selected, project, project_data, new_commit, ref_commit, ...batches, ...config, selected_views };
}


// For text inputs that edit the URL, e.g. filters: [text, onChange].
// Navigation happens in a React transition: an input bound to the URL directly would lose keystrokes.
// Instead, we keep what users type, and update the URL when they pause.
export function useUrlText(url_value = '', commit, delay = 200) {
  const [text, setText] = useState(url_value);
  // the last value from the URL we know about, to catch changes made elsewhere (back button, links...)
  const [known, setKnown] = useState(url_value);
  // what we wrote in the URL, until it gets there
  const [pending, setPending] = useState(null);
  if (url_value !== known) {
    setKnown(url_value);
    // While our own updates are on their way, users' text wins
    if (pending === null) setText(url_value);
    else if (url_value === pending) setPending(null);
  }
  const timer = useRef(null);
  useEffect(() => () => clearTimeout(timer.current), []);
  const onChange = event => {
    const value = event?.target ? event.target.value : event;
    setText(value);
    clearTimeout(timer.current);
    timer.current = setTimeout(() => {
      // when the URL already has it, nothing will change
      setPending(value === known ? null : value);
      commit(value);
    }, delay);
  };
  return [text, onChange];
}
