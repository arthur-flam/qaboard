import {
  FETCH_PROJECTS,
  UPDATE_PROJECTS,
  FETCH_BRANCHES,
  UPDATE_BRANCHES,
  FETCH_COMMITS,
  UPDATE_COMMITS,
  UPDATE_FAVORITE,
  UPDATE_MILESTONES,
} from '../actions/constants'
import { default_project_id, default_project } from "../defaults"

import { metrics_fill_defaults } from "../utils"


function update_project(state = default_project, data) {
  /*
  // // A quick debug tool
  const debug_views = [{
        name: 'Frames',
        type: 'image/bmp',
        path: ':frame/output.bmp',
        // path: '(.*)/output.bmp',
        display: 'single',
        // display: 'all',
      },
      {
        name: 'Files',
        type: 'text/plain',
        // path: ':frame/(.*.txt)',
        path: '(.*.txt)',
        default_hidden: false,
      }
  ]
  data.data.qatools_config.outputs.visualizations = debug_views;
  console.log('WARNING: replaced the visualizations for debugging!')
  */
  if (data.data?.qatools_metrics) {
    data.data.qatools_metrics.available_metrics = metrics_fill_defaults(data.data.qatools_metrics.available_metrics  || {});
    data.data.qatools_metrics.main_metrics = (data.data.qatools_metrics.main_metrics || []).filter(m => !!data.data.qatools_metrics.available_metrics[m]);
  }
  return {
    ...state,
    ...data,
    // for some reason we get null for projects that are not configured with qatools
    data: {
      ...state.data,
      ...data.data
    },
  }
}


export const branch_key = branch => {
  if (branch === undefined || branch === null)
    return 'latests';
  return branch.name ?? branch.committer ?? 'latests';
}
export function projects(state = {
  data: !!default_project_id ?  {
    [default_project_id]: default_project,
  } : {},
  is_loaded: false,
  is_loading: false,
  error: null,
  is_favorite: false,
  milestones: [],
}, action) {
  var new_state;
  switch (action.type) {
    case FETCH_PROJECTS:
      return {
        ...state,
        is_loaded: false,
      }
    case UPDATE_PROJECTS:
      new_state = {
        ...state,
        is_loaded: true,
        error: action.error,
        data: {
          ...state.data,
        }
      };
      if (action.ordering !== undefined) {
        // paginated fetches also tell us how to order the current page(s)
        const append = (action.offset ?? 0) > 0
        const previous_ordering = (append && state.ordering) || []
        new_state.ordering = [...previous_ordering, ...action.ordering.filter(id => !previous_ordering.includes(id))]
        new_state.total = action.total
        new_state.search = action.search
      }
      if (!action.projects)
        return new_state;

      Object.entries(action.projects).forEach(([project, data]) => {
        new_state.data[project] = update_project(state.data[project], data)
      })
      return new_state;

    case UPDATE_COMMITS:
      var branch = branch_key(action.branch);
      let previous_ids = state.data[action.project].commits[branch]?.ids;
      let new_ids = action.commits && action.commits.map(c => c.id)
      if (new_ids && action.append && previous_ids)
        // "load more": append the next page, without duplicates
        new_ids = [...previous_ids, ...new_ids.filter(id => !previous_ids.includes(id))]
      new_state = {
        ...state,
        data: {
          ...state.data,
          [action.project]: {
            ...state.data[action.project],
            commits: {
              ...state.data[action.project].commits,
              [branch]: {
                ...state.data[action.project].commits[branch],
                is_loaded: true,
                is_loading: false,
                // in case of error, we keep the previous list of commits
                ids: new_ids || previous_ids,
                has_more: action.has_more ?? false,
                error: action.error,
              }
            }
          }
        }
      }
      if (action.commits.length > 0) {
        const first_commit = action.commits[action.commits.length - 1];
        const last_commit = action.commits[0];
        let date_range = [
          new Date(first_commit.authored_datetime),
          new Date(last_commit.authored_datetime),
        ]
        const previous_date_range = state.data[action.project].commits[branch]?.date_range;
        if (action.append && !!previous_date_range)
          date_range = [
            new Date(Math.min(new Date(previous_date_range[0]), date_range[0])),
            new Date(Math.max(new Date(previous_date_range[1]), date_range[1])),
          ]
        new_state.data[action.project].commits[branch].date_range = date_range
        // We keep track of the latest commit on each branch, hopping for no git tricks..
        if (branch !== 'latests') {
          var branch_last_commit = last_commit
        } else {
          const default_branch = state.data[action.project].data?.git?.default_branch ??
                                 state.data[action.project].data?.qatools_config?.project?.reference_branch;
          branch_last_commit = action.commits.filter(c => c.branch === default_branch )[0];
        }
        const last_commit_authored_datetime = new Date(branch_last_commit?.authored_datetime);
        // console.log("branch_last_commit", branch_last_commit, last_commit_authored_datetime)
        let had_latest_commit = new_state.data[action.project].commits[branch].latest_commit !== undefined;
        // console.log("had_latest_commit", had_latest_commit)
        let previous_latest_authored_datetime = had_latest_commit && new Date(new_state.data[action.project].commits[branch].latest_commit.authored_datetime)
        // console.log('newer?', previous_latest_authored_datetime, previous_latest_authored_datetime < last_commit_authored_datetime)
        if ( (!!branch_last_commit && !had_latest_commit) || previous_latest_authored_datetime < last_commit_authored_datetime) {
          // console.log('new latest')
          new_state.data[action.project].commits[branch].latest_commit = {
            id: branch_last_commit.id,
            authored_datetime: last_commit_authored_datetime,
          }  
        }
      }
      return new_state;


    case UPDATE_FAVORITE:
      return {
        ...state,
        data: {
          ...state.data,
          [action.project]: {
            ...state.data[action.project],
            is_favorite: action.is_favorite,
          }
        }
      }

    case UPDATE_MILESTONES:
      return {
        ...state,
        data: {
          ...state.data,
          [action.project]: {
            ...state.data[action.project],
            milestones: {...action.milestones},
          }
        }
      }


    case FETCH_COMMITS:
      branch = branch_key(action.branch);
      return {
        ...state,
        data: {
          ...state.data,
          [action.project]: {
            ...state.data[action.project],
            commits: {
              ...state.data[action.project].commits,
              [branch]: {
                ...state.data[action.project].commits[branch],
                ids: (state.data[action.project].commits[branch] && state.data[action.project].commits[branch].ids) || [],
                is_loading: true,
                error: null,
                // paginated fetches don't select dates: keep the known range
                date_range: action.date_range ?? state.data[action.project].commits[branch]?.date_range,
              }
            }
          }
        }
      }

    case UPDATE_BRANCHES:
      return {
        ...state,
        data: {
          ...state.data,
          [action.project]: update_project(state.data[action.project], {
            branches: action.branches,
            branches_loading: false,
          }),
        }
      }

    case FETCH_BRANCHES:
      return {
        ...state,
        data: {
          ...state.data,
          [action.project]: update_project(state.data[action.project], { branches_loading: true }),
        }
      }

    default:
      return state
  }
}


