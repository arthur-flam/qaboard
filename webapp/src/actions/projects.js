import { get } from "axios";

import {
  FETCH_PROJECT,
  FETCH_PROJECTS,
  UPDATE_PROJECTS,
  FETCH_BRANCHES,
  UPDATE_BRANCHES,
  FETCH_COMMITS,
  UPDATE_COMMITS,
  UPDATE_FAVORITE,
  UPDATE_MILESTONES,
} from "./constants";
import { metrics_fill_defaults } from "../utils"


export const updateProjects = (projects, error, pagination) => ({
  type: UPDATE_PROJECTS,
  projects,
  error: error,
  ...(pagination || {}),
})

// Paginated: pass eg {limit: 50, offset: 0, search: "group/"}
// Without params the server answers with every project (legacy behavior).
export const fetchProjects = params => {
  return dispatch => {
    dispatch({ type: FETCH_PROJECTS })
    const paginated = !!params && params.limit !== undefined
    get("/api/v1/projects", { params })
      .then(response => {
        if (paginated) {
          const { projects, total, limit, offset } = response.data
          let projects_map = {}
          projects.forEach(({id, ...details}) => { projects_map[id] = details })
          dispatch(updateProjects(projects_map, null, {
            ordering: projects.map(p => p.id),
            total,
            limit,
            offset,
            search: params.search || '',
          }))
        } else {
          dispatch(updateProjects(response.data))
        }
      })
      .catch(error => {
        dispatch(updateProjects(null, error))
      });
  }
}



export const fetchProject = project => {
  return dispatch => {
    dispatch({ type: FETCH_PROJECT })
    get(`/api/v1/project?project=${project}`)
      .then(response => {
        dispatch(updateProjects({project: response.data}))
      })
      .catch(error => {
        dispatch(updateProjects(null, error))
      });
  }
}


// The server answers with the most-recently-active branches first;
// pass {filter, limit} to search instead of downloading every branch.
export const fetchBranches = (project, {filter, limit} = {}) => {
  return dispatch => {
    dispatch({
      type: FETCH_BRANCHES,
      project,
    })
    get("/api/v1/project/branches", { params: { project, filter, limit } })
      .then(response => {
        dispatch(updateBranches(project, response.data.map(branch => branch.replace('origin/', '')).filter((v, i, a) => a.indexOf(v) === i)))
      })
      .catch(error => {
        dispatch(updateBranches(project, null, error))
      });
  }
}

export const updateBranches = (project, branches, error) => ({
  type: UPDATE_BRANCHES,
  project,
  branches,
  error: error,
})


// date_range is optional: without it the server just returns the latest commits.
// extra_params can include pagination ({limit, offset}) and a server-side
// search over hash/branch/message/committer/batch-labels ({search}).
export const fetchCommits = (project, branch, date_range, aggregation_metrics, extra_params, fetch_once) => {
  return dispatch => {
    dispatch({ type: FETCH_COMMITS, project, branch, date_range })
    var url
    if (branch.committer)
      url = `/api/v1/commits/?committer=${branch.committer}`;
    else {
      let branch_ = "/";
      if (branch.name) branch_ = `/${branch.name}`;
      url = `/api/v1/commits${branch_}`;
    }
    get(url, {
      params: {
        project,
        ...(!!date_range ? {from: date_range[0], to: date_range[1]} : {}),
        metrics: JSON.stringify(aggregation_metrics),
        ...extra_params,
      }
    })
      .then(response => {
        // paginated requests are answered with an envelope, legacy ones with an array
        const is_paginated = !Array.isArray(response.data)
        const commits = is_paginated ? response.data.commits : response.data
        const has_more = is_paginated ? response.data.has_more : false
        const append = (extra_params?.offset ?? 0) > 0
        dispatch({ type: UPDATE_COMMITS, project, branch, commits, has_more, append })
        if (!!branch.name && commits && commits.length > 0 && !fetch_once) {
          const latest_commit = commits[0]
          const available_metrics = metrics_fill_defaults(latest_commit.data.qatools_metrics?.available_metrics);
          let aggregated_metrics = {};
          (latest_commit.data.qatools_metrics?.main_metrics || []).forEach(m => {
            if (available_metrics[m] !== undefined)
              aggregated_metrics[m] = available_metrics[m].target ?? 0
          });
          const metrics_changed = JSON.stringify(aggregated_metrics) !== JSON.stringify(aggregation_metrics)
          if (metrics_changed)
            dispatch(fetchCommits(project, branch, date_range, aggregated_metrics, extra_params, true))
        }
      })
      .catch(error => {
        dispatch({ type: UPDATE_COMMITS, project, branch, error, commits: [] })
      });
  }
}


export const updateFavorite = (project, is_favorite) => ({
  type: UPDATE_FAVORITE,
  project,
  is_favorite,
})

export const updateMilestones = (project, milestones, storage /*"local" | "shared"*/) => ({
  type: UPDATE_MILESTONES,
  project,
  milestones,
})
