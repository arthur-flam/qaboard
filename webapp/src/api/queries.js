// Server state: what we fetch from the API, cached and kept fresh by TanStack Query.
// https://tanstack.com/query/latest/docs/framework/react/guides/query-options
//
// Query keys start with the resource, then the project, so that we can invalidate e.g. all the
// commit lists of a project with queryClient.invalidateQueries({ queryKey: ['commits', project] })
import { queryOptions, infiniteQueryOptions, keepPreviousData } from "@tanstack/react-query";

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


// All the projects, with only what lists need from their data (git info, qaboard.yaml's project section):
// sites have hundreds of projects. The rest comes with projectQuery.
export const projectsQuery = queryOptions({
  queryKey: ['projects'],
  queryFn: async ({ signal }) => normalize_projects((await http.get('/api/v1/projects', { params: { summary: true }, signal })).data),
  staleTime: 60 * 1000,
});

export const projectQuery = project => queryOptions({
  queryKey: ['project', project],
  queryFn: async ({ signal }) => normalize_project((await http.get('/api/v1/project', { params: { project }, signal })).data),
  enabled: !!project,
  staleTime: 60 * 1000,
});

// The most recently active branches whose name contains `search`
export const branches_limit = 50;
export const branchesQuery = (project, search = '') => queryOptions({
  queryKey: ['branches', project, search],
  queryFn: async ({ signal }) => {
    const { data } = await http.get('/api/v1/project/branches', { params: { project, q: search, limit: branches_limit }, signal });
    return [...new Set(data.map(branch => branch.replace('origin/', '')))];
  },
  enabled: !!project,
  // while users type, keep showing the previous matches
  placeholderData: keepPreviousData,
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


// The same, one page at a time, newest first. With `q`, the server searches the whole history
// (unless from/to are given), e.g. "fix -wip branch:dev committer:alice" (see backend/backend/search.py).
// Pages are {commits, has_more}.
export const commits_page_size = 50;
const commits_url = ({ branch, committer }) => committer ? '/api/v1/commits/' : `/api/v1/commits/${branch ?? ''}`;
export const commitsPagesQuery = ({ project, branch, committer, from, to, metrics, q, page_size = commits_page_size }) => infiniteQueryOptions({
  queryKey: ['commits', project, { branch, committer, from, to, metrics, q, page_size, pages: true }],
  queryFn: async ({ signal, pageParam }) => {
    const { data, headers } = await http.get(commits_url({ branch, committer }), {
      params: { project, committer, from, to, q, metrics: JSON.stringify(metrics ?? {}), limit: page_size, offset: pageParam },
      signal,
    });
    // servers that predate pagination answer with everything at once, without the header
    return { commits: data.map(normalize_commit), has_more: headers?.get('X-Has-More') === 'true' };
  },
  initialPageParam: 0,
  getNextPageParam: (last_page, pages, last_offset) => last_page.has_more ? last_offset + last_page.commits.length : undefined,
  enabled: !!project,
  placeholderData: keepPreviousData,
  staleTime: 30 * 1000,
});

// Lists of commits, whether they are paginated or not
export const commits_of = data => data?.pages ? data.pages.flatMap(page => page.commits) : (data ?? []);


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
  for (const [, data] of queryClient.getQueriesData({ queryKey: ['commits', project] })) {
    const commit = commits_of(data).find(c => c.id === id);
    if (commit) return commit;
  }
  return undefined;
};

// After acting on a commit (deleting a batch, a redo...), refresh everything that shows it
export const invalidateCommit = (queryClient, project, id) => Promise.all([
  queryClient.invalidateQueries({ queryKey: ['commit', project, id] }),
  queryClient.invalidateQueries({ queryKey: ['commits', project] }),
]);
