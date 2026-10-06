import { lazy } from "react";
import AppNavbar from "./AppNavbar";
import AppSider from "./AppSider";
import { route_paths } from "./route_paths";

// Pages are loaded on demand: each pulls heavy dependencies (plotly, monaco...)
const CiCommitList = lazy(() => import("./CiCommitList"));
const CiCommitResults = lazy(() => import("./CiCommitResults"));
const Dashboard = lazy(() => import("./Dashboard"));


export const routes = [
  {
    path: route_paths.committer,
    main: CiCommitList,
    sider: AppSider,
    navbar: AppNavbar,
  },
  {
    path: route_paths.branch,
    main: CiCommitList,
    sider: AppSider,
    navbar: AppNavbar,
  },
  {
    path: route_paths.commits,
    main: CiCommitList,
    sider: AppSider,
    navbar: AppNavbar,
  },
  {
    path: route_paths.commit,
    main: CiCommitResults,
    sider: AppSider,
    navbar: AppNavbar,
  },
  {
    path: route_paths.latest_commit,
    main: CiCommitResults,
    sider: AppSider,
    navbar: AppNavbar,
  },
  {
    path: route_paths.dashboard_branch,
    main: Dashboard,
    sider: AppSider,
    navbar: AppNavbar,
  },
  {
    path: route_paths.dashboard,
    main: Dashboard,
    sider: AppSider,
    navbar: AppNavbar,
  },
  {
    path: route_paths.history_branch,
    main: Dashboard,
    sider: AppSider,
    navbar: AppNavbar,
  },
  {
    path: route_paths.history,
    main: Dashboard,
    sider: AppSider,
    navbar: AppNavbar,
  },
  {
    path: route_paths.project,
    main: CiCommitList,
    sider: AppSider,
    navbar: AppNavbar,
  },
];
