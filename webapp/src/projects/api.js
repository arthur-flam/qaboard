// Creating and archiving projects (backend/backend/api/projects.py; design/user-management.md, Appendix G)
import { queryOptions } from "@tanstack/react-query";

import { http } from "../api/http";


// Whether the user may create `project`, else why and what to do:
// {allowed, exists, reason, error, hint, namespace, new_namespace, namespace_kind, suggestion, personal_namespace, policy}
export const newProjectCheckQuery = project => queryOptions({
  queryKey: ['projects', 'check', project],
  queryFn: async ({ signal }) => (await http.get('/api/v1/projects/check', { params: { project }, signal })).data,
  enabled: !!project,
  staleTime: 10 * 1000,
});

// {project, display_name?, visibility?} => the project. Refusals are 403s with `hint`.
export const createProject = async (queryClient, body) => {
  const { data } = await http.post('/api/v1/projects', body);
  await queryClient.invalidateQueries({ queryKey: ['projects'] });
  return data;
};

// {id, display_name, namespace: {path, kind, legacy}, created_at, created_by, created_via, archived, archived_at,
// archived_by, repositories: [{git_host, repo_path, subdir}], owners, can_archive}
export const lifecycleQuery = project => queryOptions({
  queryKey: ['project-lifecycle', project],
  queryFn: async ({ signal }) => (await http.get('/api/v1/project/lifecycle', { params: { project }, signal })).data,
  enabled: !!project,
  staleTime: 30 * 1000,
});

export const setArchived = async (queryClient, project, archived) => {
  const { data } = await http.post('/api/v1/project/archive', { project, archived });
  await Promise.all([
    queryClient.invalidateQueries({ queryKey: ['project-lifecycle', project] }),
    queryClient.invalidateQueries({ queryKey: ['projects'] }),
  ]);
  return data;
};

// A project id people may type: namespace/name, no empty, "." or ".." parts
export const projectIdProblem = id => {
  const value = (id ?? '').trim();
  if (!value) return null;
  if (value.startsWith('/') || value.endsWith('/')) return "No / at the start or the end.";
  const parts = value.split('/');
  if (parts.length < 2) return "Name it namespace/name, e.g. your user name then the project's.";
  if (parts.some(part => part === '' || part === '.' || part === '..')) return "No empty, . or .. parts.";
  if (/\s/.test(value)) return "No spaces.";
  return null;
};
