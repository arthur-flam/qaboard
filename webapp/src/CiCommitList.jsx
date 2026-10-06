import { useEffect, useMemo, useState } from "react";
import styled from "styled-components";

import { DateTime } from 'luxon';


import {
  Classes,
  NonIdealState,
  Spinner,
  Card,
} from "@blueprintjs/core";

import CommitRow from "./components/CommitRow";
import { Container, Section } from "./components/layout";
import CommitsEvolution from "./CommitsEvolution";
import { groupBy, match_query } from "./utils";
import { toaster } from "./toaster"

import { useCommitsList, useProjectData, useSelected } from './hooks'



const WrapperCommitRows = styled.ul`
  list-style: none;
  margin: 0;
  padding: 0;
`;

const CommitRows = ({ commits, project, project_data, className }) => (
  <div className={className}>
    <li>
      <WrapperCommitRows>
        {commits.map(commit => (
          <CommitRow
            commit={commit}
            project={project}
            project_data={project_data}
            key={commit.id}
            toaster={toaster}
          />
        ))}
      </WrapperCommitRows>
    </li>
  </div>
);

const commit_search = c => {
  const batches = Object.keys(c.batches).join('|')
  return `${c.committer_name} ${c.message} ${c.branch} ${batches}`
}

const error_msg = error => {
  if (error.response) // The server responded with a status other than 2xx
    return error.message + "\n" + (typeof error.response.data === 'string' ? error.response.data : JSON.stringify(error.response.data));
  return "Error: " + error.message;
}


const CiCommitList = () => {
  const { project, branch, committer, search } = useSelected();
  const project_data = useProjectData(project);
  const { commits: all_commits, error, isPending, isFetching, isSuccess, date_range } = useCommitsList({ refetchInterval: 60 * 1000 });
  const [now] = useState(() => new Date());

  useEffect(() => {
    let name = project.split('/').slice(-1)[0];
    document.title = `${branch || committer || project} - ${name}`;
  }, [project, branch, committer]);

  const commits = useMemo(() => {
    const matcher = match_query(search)
    return all_commits.filter(c => matcher(commit_search(c)))
  }, [all_commits, search]);
  const commits_by_day = useMemo(() => groupBy(commits, "authored_date"), [commits]);

  const is_branch = !!branch;
  const some_commits_loaded = commits.length > 0;
  const show_metrics_over_time = (isSuccess || some_commits_loaded) && !error && is_branch;
  // On branches, the metrics configuration comes from the latest commit
  const evolution_project_data = useMemo(() => {
    if (!is_branch || !some_commits_loaded) return project_data;
    return { ...commits[0], data: { ...commits[0].data, git: project_data.data?.git } };
  }, [is_branch, some_commits_loaded, commits, project_data]);

  return (
    <Container style={{paddingTop: '50px'}}>
      {show_metrics_over_time && <Section>
        <Card>
          <CommitsEvolution
            project={project}
            project_data={evolution_project_data}
            commits={commits}
            per_output_granularity={false}
            style={{ marginTop: "20px" }}
          />
        </Card>
      </Section>}
      {error && <NonIdealState description={<pre>{error_msg(error)}</pre>} icon="error" />}
      {isPending && <NonIdealState title="Loading" icon={<Spinner />} />}
      {isSuccess && !isFetching && !error && !some_commits_loaded &&
        <NonIdealState
          title="Could not find a commit with results"
          description={<span>Searched {" "}
            <strong>from <span title={date_range[0]}>{DateTime.fromJSDate(date_range[0]).toRelativeCalendar({unit: "days"})}</span></strong>
            {" "}to{" "}
            {date_range[1] > now ? "today" : <strong>
              <span title={date_range[1]}>{DateTime.fromJSDate(date_range[1]).toRelativeCalendar({unit: "days"})}</span>
            </strong>}
          </span>}
          icon="search"
      />}
      {Object.keys(commits_by_day).map(day => (
        <Card key={day} elevation={0} style={{marginBottom: '15px'}}>
          <h4 className={Classes.HEADING} style={{textTransform: 'capitalize'}}>
            {(!!day && day !== "undefined") ? <>
                {DateTime.fromISO(day, { zone: 'utc' }).toRelativeCalendar({unit: "days"})}
                {" "}
                &#8212; {commits_by_day[day].length} commits
              </>
             : `${commits_by_day[day].length} commits`}
          </h4>
          <CommitRows project={project} project_data={project_data} commits={commits_by_day[day]} />
        </Card>
      ))}
    </Container>
  );
}

export default CiCommitList;
