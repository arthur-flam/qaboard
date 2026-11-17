// A project's page, /<project>: its commits, or for projects that train, its runs. qaboard.yaml says so:
//   project:
//     home: runs
// Docs: /docs/user-guide/runs
import { lazy, useEffect } from "react";

import { history } from "../router";
import { useProjectData, useSelected } from "../hooks";
import EmptyLoading from "../components/EmptyLoading";
import { project_home, runs_path } from "./state";

const CiCommitList = lazy(() => import("../CiCommitList"));

const decoded = path => {
  try { return decodeURIComponent(path); } catch { return path; }
};

const ToRuns = ({ project }) => {
  useEffect(() => {
    history.replace({ pathname: runs_path(project), search: window.location.search });
  }, [project]);
  return <EmptyLoading/>;
};

const ProjectHome = () => {
  const { project, route, branch, committer } = useSelected();
  const project_data = useProjectData(project);
  // only the bare /<project>: /<project>/commits stays the commits
  const is_bare = decoded(window.location.pathname).replace(/\/+$/, '') === `/${project}` && !branch && !committer && route.is_commits;
  if (is_bare && !project_data.is_loaded) return <EmptyLoading/>;
  if (is_bare && project_home(project_data) === 'runs') return <ToRuns project={project}/>;
  return <CiCommitList/>;
};

export default ProjectHome;
