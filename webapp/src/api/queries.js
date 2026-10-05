// Server state: what we fetch from the API, cached and kept fresh by TanStack Query.
// https://tanstack.com/query/latest/docs/framework/react/guides/query-options
//
// Query keys start with the resource, then the project, so that we can invalidate e.g. all the
// commit lists of a project with queryClient.invalidateQueries({ queryKey: ['commits', project] })
import { queryOptions, keepPreviousData } from "@tanstack/react-query";

import { http } from "./http";
import { normalize_projects, normalize_project, normalize_commit } from "./normalize";
import { setPathMappings, setDefaultGitHostname } from "../utils";


export const default_site_config = {
  image_servers: { default: '/iiif' },
  login_type: 'LOCAL',
  login_required: false,
  sentry_dsn: null,
  posthog_api_key: null,
  posthog_host: null,
  path_mappings: [],
  docs_root: '/',
  avatar_url_template: null,
  sentry_traces_sample_rate: 1.0,
  git_web_url: 'https://gitlab.com',
  quota_url_template: null,
  support_url: 'https://github.com/Samsung/qaboard/issues',
};

export const siteConfigQuery = queryOptions({
  queryKey: ['config'],
  queryFn: async ({ signal }) => {
    let config;
    try {
      config = { ...default_site_config, ...(await http.get('/api/v1/config', { signal })).data };
    } catch (error) {
      console.warn('Failed to fetch site config, using defaults', error);
      config = default_site_config;
    }
    setPathMappings(config.path_mappings);
    setDefaultGitHostname(config.git_web_url);
    return config;
  },
  staleTime: 5 * 60 * 1000,
});


export const logged_out_user = {
  is_logged: false,
  user_id: null,
  user_name: null,
  full_name: null,
  email: null,
};

export const userQuery = queryOptions({
  queryKey: ['me'],
  queryFn: async ({ signal }) => {
    const { is_authenticated, user_id, user_name, full_name, email, login_type } = (await http.get('/api/v1/user/me/', { signal })).data;
    return is_authenticated ? { is_logged: true, user_id, user_name, full_name, email, login_type } : logged_out_user;
  },
  staleTime: 5 * 60 * 1000,
});


export const projectsQuery = queryOptions({
  queryKey: ['projects'],
  queryFn: async ({ signal }) => normalize_projects((await http.get('/api/v1/projects', { signal })).data),
  staleTime: 60 * 1000,
});

export const projectQuery = project => queryOptions({
  queryKey: ['project', project],
  queryFn: async ({ signal }) => normalize_project((await http.get('/api/v1/project', { params: { project }, signal })).data),
  enabled: !!project,
  staleTime: 60 * 1000,
});

export const branchesQuery = project => queryOptions({
  queryKey: ['branches', project],
  queryFn: async ({ signal }) => {
    const { data } = await http.get('/api/v1/project/branches', { params: { project }, signal });
    return [...new Set(data.map(branch => branch.replace('origin/', '')))];
  },
  enabled: !!project,
  staleTime: 5 * 60 * 1000,
});


// The commits of a project, a branch or a committer, in a date range.
// `metrics` are aggregated over each commit's batches by the server.
export const commitsQuery = ({ project, branch, committer, from, to, metrics, ...extra_params }) => queryOptions({
  queryKey: ['commits', project, { branch, committer, from, to, metrics, ...extra_params }],
  queryFn: async ({ signal }) => {
    const url = committer ? '/api/v1/commits/' : `/api/v1/commits/${branch ?? ''}`;
    const { data } = await http.get(url, {
      params: { project, committer, from, to, metrics: JSON.stringify(metrics ?? {}), ...extra_params },
      signal,
    });
    return data.map(normalize_commit);
  },
  enabled: !!project,
  // when the date range or branch changes, keep showing the previous list until the new one is there
  placeholderData: keepPreviousData,
  staleTime: 30 * 1000,
});


// Without an id, the server answers with the latest commit on the branch (by default the project's reference branch)
export const commitQuery = ({ project, id, branch, batch }) => queryOptions({
  queryKey: ['commit', project, id ?? null, { branch, batch }],
  queryFn: async ({ signal }) => {
    const { data } = await http.get(`/api/v1/commit/${id ?? ''}`, { params: { project, branch, batch }, signal });
    return normalize_commit(data);
  },
  enabled: !!project && id !== '',
  staleTime: 10 * 1000,
  // keep up to date while outputs are running
  refetchInterval: query => {
    const batches = Object.values(query.state.data?.batches ?? {});
    return batches.some(b => b.pending_outputs > 0) ? 15 * 1000 : false;
  },
});

// Where to look for a commit in the cache: its own query, or else any list of commits that includes it
export const findCachedCommit = (queryClient, project, id) => {
  if (!id) return undefined;
  for (const [, commits] of queryClient.getQueriesData({ queryKey: ['commits', project] })) {
    const commit = commits?.find(c => c.id === id);
    if (commit) return commit;
  }
  return undefined;
};

// After acting on a commit (deleting a batch, a redo...), refresh everything that shows it
export const invalidateCommit = (queryClient, project, id) => Promise.all([
  queryClient.invalidateQueries({ queryKey: ['commit', project, id] }),
  queryClient.invalidateQueries({ queryKey: ['commits', project] }),
]);
