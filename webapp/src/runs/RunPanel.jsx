// A run of the Runs page, opened in place: the same card as on its commit's page, with its metrics, its series and
// visualizations with every viewer, its files and logs. "Compare with…" picks another run as its reference: two runs
// as new and reference, whatever their commits. From there, the run's commit page has the rest.
import { useMemo, useState } from "react";
import { useQuery } from "@tanstack/react-query";
import { AnchorButton, Button, Callout, Classes, Drawer, Icon, MenuItem, NonIdealState, Spinner, Tag, Tooltip } from "@blueprintjs/core";
import { Select } from "@blueprintjs/select";

import { Link, history } from "../router";
import { commitSummaryQuery } from "../api/queries";
import { compute_config } from "../selectors/batches";
import { controls_defaults } from "../viewers/controls";
import { OutputCard } from "../viewers/OutputCard";
import { StatusTag } from "../components/tags";
import { SectionErrorBoundary } from "../components/ErrorFallback";
import { copyLink } from "../utils/clipboard";
import { side_color } from "../sideColors";
import { commit_page_url, duration_seconds, format_duration, started } from "./table";
import { absolute } from "./format";


const renderRun = labels => (run, { handleClick, handleFocus, modifiers, ref }) => {
  if (!modifiers.matchesPredicate) return null;
  return <MenuItem
    key={run.id}
    ref={ref}
    roleStructure="listoption"
    active={modifiers.active}
    text={labels[run.id]}
    label={`${run.batch?.experiment ?? ''} · ${run.commit?.id?.slice(0, 7) ?? ''}`}
    onClick={handleClick}
    onFocus={handleFocus}
  />;
};

// The run it is compared with: any other run of the table
const CompareWith = ({ runs, run, compare, labels, onCompare }) => {
  const others = runs.filter(r => r.id !== run.id);
  if (compare) return <Tag large minimal interactive={false} onRemove={() => onCompare(null)} icon="comparison" title="Compared with this run: its series dashed, its images next to this run's">
    vs {labels[compare.id] ?? `run ${compare.id}`}
  </Tag>;
  return <Select
    items={others}
    itemRenderer={renderRun(labels)}
    itemPredicate={(query, item) => `${labels[item.id]} ${item.batch?.experiment} ${item.commit?.id}`.toLowerCase().includes(query.toLowerCase())}
    onItemSelect={item => onCompare(item.id)}
    noResults={<MenuItem disabled roleStructure="listoption" text="No other run in the table"/>}
    popoverProps={{ minimal: true, placement: 'bottom-start' }}
    inputProps={{ placeholder: 'Filter the runs…' }}
  >
    <Button icon="comparison" text="Compare with…" disabled={others.length === 0} title="Pick another run of the table as the reference: two runs side by side, with every viewer"/>
  </Select>;
};

export const RunPanel = ({ project, project_data, run, compare, runs, labels, onClose, onCompare }) => {
  // the run's configuration (qaboard.yaml's visualizations, metrics) is its commit's, or its batch's
  const { data: commit, isPending, error } = useQuery({ ...commitSummaryQuery({ project, id: run?.commit?.id }), enabled: !!run?.commit?.id });
  const batch = commit?.batches?.[run?.batch?.label];
  const config = useMemo(() => compute_config({ new_batch: batch, new_commit: commit, project_data }), [batch, commit, project_data]);
  // the view options: this panel's own, like History's previews
  const [controls, setControls] = useState(() => controls_defaults(config.config, ''));
  const [controls_for, setControlsFor] = useState(config.config);
  if (controls_for !== config.config) {
    setControlsFor(config.config);
    setControls(controls_defaults(config.config, ''));
  }
  const labels_card = useMemo(() => ({
    new: labels?.[run?.id] ?? 'this run',
    reference: compare ? (labels?.[compare.id] ?? `run ${compare.id}`) : 'reference',
    colors: { new: side_color(0), reference: side_color(1) },
  }), [labels, run?.id, compare]);
  if (!run) return null;

  const duration = duration_seconds(run);
  const link = `${window.location.origin}${window.location.pathname}${window.location.search}`;
  const title = <span style={{ display: 'inline-flex', alignItems: 'center', gap: 8, minWidth: 0 }}>
    <StatusTag output={run}/>
    <span style={{ overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>{labels?.[run.id] ?? `Run ${run.id}`}</span>
    {run.is_running && <Tag minimal intent="primary">live</Tag>}
  </span>;

  return <Drawer isOpen onClose={onClose} title={title} icon="lab-test" size="min(1180px, 82%)" position="right" hasBackdrop={false} canOutsideClickClose={false} canEscapeKeyClose={false} style={{ overflow: 'auto' }}>
    <div className={Classes.DRAWER_BODY} style={{ padding: 16 }}>
      <div style={{ display: 'flex', flexWrap: 'wrap', alignItems: 'center', gap: 8, marginBottom: 12 }}>
        <CompareWith runs={runs} run={run} compare={compare} labels={labels} onCompare={onCompare}/>
        {/* a link: it opens in a new tab too */}
        <AnchorButton
          icon="git-commit" text="Open on the commit page" title="The run on its commit's page: every view, the reference bar, the batch's other runs"
          href={commit_page_url(project, run, compare)}
          onClick={event => {
            if (event.metaKey || event.ctrlKey || event.shiftKey || event.button !== 0) return;
            event.preventDefault();
            history.push(commit_page_url(project, run, compare));
          }}
        />
        <Tooltip content="Copy a link to this run, open in this page">
          <Button variant="minimal" icon="link" aria-label="Copy a link to this run" onClick={() => copyLink({ url: link, text: labels?.[run.id] ?? `run ${run.id}` }, { what: 'the link to the run' })}/>
        </Tooltip>
      </div>
      <dl style={{ display: 'grid', gridTemplateColumns: 'max-content 1fr', gap: '4px 14px', margin: '0 0 12px', fontSize: 13 }}>
        <dt className={Classes.TEXT_MUTED}>Experiment</dt><dd style={{ margin: 0 }}><code>{run.batch?.experiment}</code></dd>
        <dt className={Classes.TEXT_MUTED}>Commit</dt>
        <dd style={{ margin: 0 }}>
          <Link to={commit_page_url(project, run)} className={Classes.MONOSPACE_TEXT}>{run.commit?.id?.slice(0, 8)}</Link>
          {run.commit?.branch && <> on <strong>{run.commit.branch}</strong></>}
          <span className={Classes.TEXT_MUTED}> · {(run.commit?.message ?? '').split('\n')[0]}</span>
          {run.data?.dirty && <Tooltip content="The code had changes that are not in the commit: the commit alone may not give the same results">
            <Tag minimal intent="warning" icon="warning-sign" style={{ marginLeft: 6 }}>changes not committed</Tag>
          </Tooltip>}
        </dd>
        <dt className={Classes.TEXT_MUTED}>Run by</dt>
        <dd style={{ margin: 0 }}>{run.batch?.user_name ?? '—'}, {absolute(started(run))}{duration !== null && <>, {run.is_running ? 'running for' : 'ran'} {format_duration(duration)}</>}</dd>
        {run.data?.sdk?.script && <><dt className={Classes.TEXT_MUTED}>Script</dt><dd style={{ margin: 0 }}><code>{run.data.sdk.script}</code></dd></>}
      </dl>
      {compare && <Callout compact icon="comparison" style={{ marginBottom: 12 }}>
        Compared with <strong>{labels?.[compare.id]}</strong> ({compare.batch?.experiment}, {compare.commit?.id?.slice(0, 7)}): its series are dashed, its images next to this run's.
      </Callout>}
      {error && <NonIdealState icon="error" title="Could not load the run's commit" description="Its visualizations need the commit's configuration. Open it on the commit page."/>}
      {!error && isPending && <NonIdealState icon={<Spinner size={24}/>} title="Loading the run…"/>}
      {!error && !isPending && <SectionErrorBoundary>
        <OutputCard
          viewable
          project={project}
          commit={commit}
          config={config.config}
          metrics={config.metrics}
          output_new={run}
          output_ref={compare ?? undefined}
          controls={controls}
          onControlsChange={changes => setControls(controls => ({ ...controls, ...changes }))}
          labels={labels_card}
          style={{ width: '100%' }}
        />
      </SectionErrorBoundary>}
      <p className={Classes.TEXT_MUTED} style={{ marginTop: 12, fontSize: 12 }}>
        <Icon icon="info-sign" size={12}/> <kbd className={Classes.KEY}>Esc</kbd> closes the run, Back too. Its logs, files and attempts are on <Link to={commit_page_url(project, run)}>its commit's page</Link>.
      </p>
    </div>
  </Drawer>;
};

export default RunPanel;
