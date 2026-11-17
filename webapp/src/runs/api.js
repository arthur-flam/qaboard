// The Runs page's data: a project's experiments, and its runs across commits (backend/backend/api/runs.py)
import { infiniteQueryOptions, queryOptions } from "@tanstack/react-query";

import { http } from "../api/http";
import { normalize_output } from "../api/normalize";

// What both endpoints select, from the page's state (state.js)
export const selection_params = ({ project, state }) => ({
  project,
  scope: state.scope === 'mine' ? 'mine' : undefined,
  since_days: state.days,
  // the CI's runs are not experiments: left out unless asked
  origin: state.ci ? undefined : 'webapp,local',
  user: state.user || undefined,
});

// While runs go on, the page follows them
const LIVE_INTERVAL = 15 * 1000;

export const experimentsQuery = ({ project, state }) => queryOptions({
  queryKey: ['experiments', selection_params({ project, state })],
  queryFn: async ({ signal }) => {
    const { data, headers } = await http.get('/api/v1/experiments', { params: selection_params({ project, state }), signal });
    return { experiments: data, has_more: headers?.get('X-Has-More') === 'true' };
  },
  enabled: !!project,
  staleTime: 15 * 1000,
  placeholderData: (previous, previous_query) => previous_query?.queryKey[1]?.project === project ? previous : undefined,
  refetchInterval: query => query.state.data?.experiments?.some(e => e.running > 0 || e.pending > 0) ? LIVE_INTERVAL : false,
});

// Runs come with their batch and commit: { ...output, batch: { label, experiment, origin, user_name }, commit: {...} }
export const runs_page_size = 100;
export const runsPagesQuery = ({ project, experiment, state }) => {
  const params = {
    ...selection_params({ project, state }),
    experiment: experiment || undefined,
    status: state.status.length > 0 ? state.status.join(',') : undefined,
    q: state.q || undefined,
  };
  return infiniteQueryOptions({
    queryKey: ['runs', params],
    queryFn: async ({ signal, pageParam }) => {
      const { data, headers } = await http.get('/api/v1/runs', { params: { ...params, limit: runs_page_size, offset: pageParam }, signal });
      return {
        runs: data.map(normalize_output),
        has_more: headers?.get('X-Has-More') === 'true',
        // the filter only looked at the latest runs
        scanned: headers?.get('X-Scanned') ? Number(headers.get('X-Scanned')) : null,
      };
    },
    initialPageParam: 0,
    // a page without runs never says there are more: asking again would get the same
    getNextPageParam: (last_page, pages, last_offset) => last_page.has_more && last_page.runs.length > 0 ? last_offset + last_page.runs.length : undefined,
    enabled: !!project,
    staleTime: 15 * 1000,
    // the table stays while another node or filter loads
    placeholderData: (previous, previous_query) => previous_query?.queryKey[1]?.project === project ? previous : undefined,
    refetchInterval: query => (query.state.data?.pages ?? []).some(page => page.runs.some(run => run.is_pending)) ? LIVE_INTERVAL : false,
  });
};

export const runs_of = data => data?.pages?.flatMap(page => page.runs) ?? [];
