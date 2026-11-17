// The Runs page: a project's runs across commits, by experiment. For people who train models, whose first questions
// are "where are my runs from this week?", "which of these is the best, and what did it use?" and "how does today's
// run compare with Tuesday's?", whatever the commit. Design: docs/rfcs/python-sdk-runs-follow-ups.md, part A.
//
// /<project>/runs/<experiment>: on the left the experiments as a tree, on the right the runs of the level selected
// and of everything under it: their logged series on the same plots, above a table that drives them. A run opens in
// a panel, with the card of its commit's page. Everything is in the address (state.js): links restore the view.
import { lazy, Suspense, useEffect, useMemo, useRef, useState } from "react";
import styled from "styled-components";
import { useInfiniteQuery, useQuery } from "@tanstack/react-query";
import {
  Breadcrumbs, Button, Callout, Card, Checkbox, Classes, Dialog, DialogBody, DialogFooter, HTMLSelect, Icon, InputGroup,
  Intent, NonIdealState, Popover, SegmentedControl, Spinner, Switch, Tag, Tooltip,
} from "@blueprintjs/core";

import { history, useRouter } from "../router";
import { useProjectData, useSelected, useSiteConfig, useUser, useUrlText } from "../hooks";
import { errorMessage } from "../api/http";
import { usePageTitle, join_title, project_name } from "../pageTitle";
import { CopyLinkButton } from "../components/CopyLinkButton";
import { LoadMore } from "../components/LoadMore";
import { SectionErrorBoundary } from "../components/ErrorFallback";
import { copyWithToast } from "../utils/clipboard";
import { useSeriesOptions } from "../viewers/series/SeriesOptions";
import { run_styles, varying_params } from "../viewers/series/comparison";
import { useHighlight } from "../viewers/series/plots";
import { MAX_RUNS } from "../viewers/SeriesComparison";
import { started_from_code } from "../viewers/runView";
import { experimentsQuery, runsPagesQuery, runs_of } from "./api";
import { build_tree, find_node, path_parts } from "./tree";
import { WINDOWS, has_runs_page, level_search, runs_path, runs_state, updateRuns } from "./state";
import { window_label } from "./format";
import { MAX_PARAM_COLUMNS, best_values, display_labels, first_sort, group_runs, metric_columns, metric_keys, param_columns, python_snippet, sort_runs, start_snippet } from "./table";
import { ExperimentTree, describe } from "./ExperimentTree";
import { RunsTable } from "./RunsTable";

// Plotly and the run's card load when there is something to show
const RunsPlots = lazy(() => import("./RunsPlots"));
const RunPanel = lazy(() => import("./RunPanel"));


const Page = styled.div`
  box-sizing: border-box;
  /* below the navbar */
  padding: 66px clamp(16px, 2%, 40px) 40px;
  .qa-runs-layout {
    display: grid;
    grid-template-columns: minmax(220px, 280px) minmax(0, 1fr);
    gap: 20px;
    align-items: start;
  }
  .qa-runs-tree {
    position: sticky;
    top: 66px;
    max-height: calc(100vh - 86px);
    overflow-y: auto;
  }
  @media (max-width: 900px) {
    .qa-runs-layout { grid-template-columns: minmax(0, 1fr); }
    .qa-runs-tree { position: static; max-height: 320px; }
  }
  .qa-toolbar {
    display: flex;
    flex-wrap: wrap;
    align-items: center;
    gap: 8px 12px;
    margin-bottom: 12px;
  }
  .qa-toolbar .${Classes.CONTROL} { margin-bottom: 0; }
  pre.qa-snippet {
    margin: 0;
    padding: 10px 12px;
    font-size: 12px;
    border-radius: 3px;
    background: var(--qa-surface-alt);
    border: 1px solid var(--qa-border);
    overflow-x: auto;
  }
`;

// With the CI's runs, the server answers for at most this many days
const MAX_DAYS_WITH_CI = 90;

// The quick filters on status: most of what people hide
const STATUS_CHIPS = [
  { key: 'running', label: 'Only running', icon: 'refresh', status: ['running', 'pending'], title: 'Runs that wait or run now' },
  { key: 'failed', label: 'Only failed', icon: 'error', status: ['failed'], title: 'Runs that failed: why did they crash?' },
  { key: 'hide-failed', label: 'Hide failed', icon: 'eye-off', status: ['running', 'pending', 'finished'], title: 'Leave out the runs that failed' },
];
const same_list = (a, b) => a.length === b.length && a.every(item => b.includes(item));

const Snippet = ({ code, what }) => <div style={{ position: 'relative' }}>
  <pre className="qa-snippet"><code>{code}</code></pre>
  <Button size="small" variant="minimal" icon="duplicate" aria-label={`Copy ${what}`} title="Copy" onClick={() => copyWithToast(code, { what })}
    style={{ position: 'absolute', top: 4, right: 4 }}/>
</div>;

// How to read the runs shown from Python: the call, or the page's address
const PythonDialog = ({ project, experiment, state, onClose }) => <Dialog isOpen onClose={onClose} title="Read these runs from Python" icon="code" style={{ width: 620 }}>
  <DialogBody>
    <p>The same runs, in a notebook or a script, as <code>Run</code> objects with their parameters, metrics, folder and logged series:</p>
    <Snippet code={python_snippet({ project, experiment, state })} what="the code"/>
    <p style={{ marginTop: 12 }}>Or give this page's address: <code>qb.runs(url)</code> reads what it shows, down to the selected rows. From a shell: <code>qa runs "&lt;url&gt;" --json</code>.</p>
  </DialogBody>
  <DialogFooter actions={<Button text="Close" onClick={onClose}/>}/>
</Dialog>;

// Which metrics are columns
const ColumnsMenu = ({ keys, shown, onChange }) => {
  const [search, setSearch] = useState('');
  const needle = search.trim().toLowerCase();
  const matching = keys.filter(key => key.toLowerCase().includes(needle));
  const toggle = key => onChange(shown.includes(key) ? shown.filter(k => k !== key) : [...shown, key]);
  return <Popover placement="bottom-end" content={<div style={{ padding: 8, width: 280, maxHeight: 420, overflowY: 'auto' }}>
    <InputGroup size="small" leftIcon="search" placeholder="Find a metric" value={search} onChange={e => setSearch(e.target.value)} autoFocus style={{ marginBottom: 6 }}/>
    {matching.map(key => <Checkbox key={key} checked={shown.includes(key)} label={key} onChange={() => toggle(key)} style={{ marginBottom: 4 }}/>)}
    {matching.length === 0 && <span className={Classes.TEXT_MUTED}>No metric matches.</span>}
    <Button size="small" variant="minimal" icon="reset" text="Back to the defaults" onClick={() => onChange(undefined)} style={{ marginTop: 6 }}/>
  </div>}>
    <Button icon="th-list" text={`Metrics (${shown.length})`} endIcon="caret-down" disabled={keys.length === 0} title="The metrics shown as columns"/>
  </Popover>;
};

// When the page has nothing to show: say why, and how to get runs here
const Empty = ({ state, experiment, docs_root, user, has_runs }) => {
  const when = state.days === 1 ? 'in the last 24 hours' : state.days === 365 ? 'in the last year' : `in the last ${state.days} days`;
  const filtered = state.status.length > 0 || !!state.q || !!state.user;
  if (filtered)
    return <NonIdealState icon="filter" title="No run matches the filters"
      action={<Button icon="cross" text="Clear the filters" onClick={() => updateRuns({ status: [], q: '', user: '' })}/>}/>;
  const wider = WINDOWS.find(days => days > state.days && (!state.ci || days <= MAX_DAYS_WITH_CI));
  return <NonIdealState
    icon="lab-test"
    title={`No runs${state.scope === 'mine' ? ' of yours' : ''}${experiment ? ` in ${experiment}` : ''} ${when}`}
    description={<div style={{ textAlign: 'left', maxWidth: 560 }}>
      {!has_runs && <p>Runs show here when your code creates them, e.g. a training loop. Each <code>run.log()</code> becomes a curve, next to the other runs of the experiment:</p>}
      {!has_runs && <Snippet code={start_snippet(experiment)} what="the code"/>}
      {!has_runs && <p style={{ marginTop: 8 }}>
        Name experiments with slashes, like <code>backbone/lr-sweep</code>: this page shows them as a tree.{" "}
        <a href={`${docs_root}docs/log-from-code`} target="_blank" rel="noopener noreferrer">Log from your code</a>
      </p>}
    </div>}
    action={<span style={{ display: 'flex', gap: 8 }}>
      {wider && <Button icon="history" text={window_label(wider)} onClick={() => updateRuns({ days: wider })}/>}
      {state.scope === 'mine' && user.is_logged && <Button icon="people" text="Everyone's" onClick={() => updateRuns({ scope: 'all' })}/>}
      {!state.ci && <Button icon="build" text="With the CI's runs" onClick={() => updateRuns({ ci: true, days: Math.min(state.days, MAX_DAYS_WITH_CI) })}/>}
    </span>}
  />;
};

const RunsPage = () => {
  const { project } = useSelected();
  const { match, location } = useRouter();
  const experiment = path_parts(match?.params?.name ? (() => { try { return decodeURIComponent(match.params.name); } catch { return match.params.name; } })() : '').join('/');
  const state = useMemo(() => runs_state(location.search), [location.search]);
  const project_data = useProjectData(project);
  const user = useUser();
  const { docs_root } = useSiteConfig();
  usePageTitle(join_title(experiment || 'Runs', project_name(project)));
  const [python, setPython] = useState(null);

  const experiments = useQuery(experimentsQuery({ project, state }));
  const tree = useMemo(() => build_tree(experiments.data?.experiments ?? []), [experiments.data]);
  const node = find_node(tree, experiment);
  const runs_query = useInfiniteQuery(runsPagesQuery({ project, experiment, state }));
  const loaded = useMemo(() => runs_of(runs_query.data), [runs_query.data]);
  const scanned = runs_query.data?.pages?.at(-1)?.scanned;

  // what tells the runs apart: their names, the parameters' columns, the groups
  const labels = useMemo(() => display_labels(loaded), [loaded]);
  const varying = useMemo(() => varying_params(loaded), [loaded]);
  const params = param_columns(varying, loaded).slice(0, MAX_PARAM_COLUMNS);
  const group_by = varying.includes(state.group_by) ? state.group_by : '';
  const runs = useMemo(() => sort_runs(loaded, state.sort, labels), [loaded, state.sort, labels]);

  // The plots: the runs started from code (only they log series), the selected ones if any, at most MAX_RUNS
  const selected = state.selected.filter(id => loaded.some(run => run.id === id));
  const candidates = runs.filter(run => started_from_code(run) && !run.deleted && run.output_dir_url);
  const pool = selected.length > 0 ? candidates.filter(run => selected.includes(run.id)) : candidates;
  const plottable = pool.slice(0, MAX_RUNS);
  const plotted = new Set(plottable.map(run => run.id));
  const { styles, groups: color_groups } = run_styles(plottable, group_by);
  const visible = plottable.filter(run => !state.hidden.has(run.id));
  const plot_runs = visible.map(run => ({ id: run.id, label: labels[run.id], style: styles[run.id], output: run }));
  const plot_groups = color_groups.map(group => ({ ...group, label: `${group_by}=${group.value}` }));
  const group_of = new Map(color_groups.flatMap(group => group.ids.map(id => [id, group.value])));
  const table_groups = group_by ? group_runs(runs, group_by).map(group => ({ ...group, style: color_groups.find(c => c.value === group.value)?.style })) : null;

  const [highlight, register] = useHighlight();
  const as_bands = !!group_by && state.lines === 'mean';
  const onHover = id => highlight(id === null ? null : as_bands ? `group:${group_of.get(id)}` : id);

  // metrics: the columns, the best values among the runs looked at
  const metrics_config = project_data.data?.qatools_metrics ?? {};
  const available_metrics = metrics_config.available_metrics ?? {};
  const all_metric_keys = metric_keys(loaded, metrics_config.main_metrics ?? []);
  const metrics = metric_columns(loaded, { chosen: state.metrics, main_metrics: metrics_config.main_metrics ?? [], params: varying });
  const looked_at = selected.length > 0 ? runs.filter(run => selected.includes(run.id)) : runs.filter(run => !state.hidden.has(run.id));
  const best = best_values(looked_at, metrics, available_metrics);
  // only the runs looked at are in bold, not others with the same value
  const contenders = new Set(looked_at.map(run => run.id));

  const [options, setOptions, preview] = useSeriesOptions(state.series, ({ series }) => updateRuns({ series: { ...state.series, ...series } }));
  const [q, onQ] = useUrlText(state.q, value => updateRuns({ q: value }));

  const toggle_in = (key, id) => {
    const values = new Set(key === 'hidden' ? state.hidden : state.selected);
    if (!values.delete(id)) values.add(id);
    updateRuns({ [key]: [...values] });
  };
  const open_run = runs.find(run => run.id === state.run) ?? null;
  const compare_run = runs.find(run => run.id === state.compare) ?? null;
  // two selected runs: compare them, the first ticked with the second
  const selected_runs = selected.map(id => runs.find(run => run.id === id)).filter(Boolean);
  // a run in the address that isn't in the runs loaded, e.g. a link to an older one
  const missing_run = state.run !== null && runs_query.isSuccess && !open_run;

  const live = loaded.some(run => run.is_running);
  const node_totals = node?.totals ?? tree.totals;
  const crumbs = experiment && [
    { text: 'All runs', href: runs_path(project), onClick: event => { event.preventDefault(); go_to(''); } },
    ...path_parts(experiment).map((part, index, parts) => ({
      text: part,
      href: runs_path(project, parts.slice(0, index + 1).join('/')),
      onClick: event => { event.preventDefault(); go_to(parts.slice(0, index + 1).join('/')); },
    })),
  ];
  // another level keeps the filters, not what was opened or selected in this one
  const tree_search = level_search(location);
  function go_to(path) {
    history.push({ pathname: runs_path(project, path), search: tree_search });
  }
  const active_chip = STATUS_CHIPS.find(chip => same_list(chip.status, state.status));
  // projects whose code never created runs get how to start, the others only what to widen
  const has_runs = has_runs_page(project_data) || (experiments.data?.experiments ?? []).length > 0;

  // The run's panel: opening it is a new entry in the history, closing it goes back (Back after closing would show
  // the same page). Esc closes it wherever the focus is: the panel has no backdrop, people click the table behind it.
  const opened_here = useRef(false);
  const open = id => {
    opened_here.current = state.run === null || opened_here.current;
    updateRuns({ run: id, compare: null }, { replace: state.run !== null });
  };
  const close = () => {
    if (opened_here.current) {
      opened_here.current = false;
      history.back();
    } else updateRuns({ run: null, compare: null });
  };
  const close_ref = useRef(close);
  useEffect(() => { close_ref.current = close; });
  const has_open_run = state.run !== null;
  useEffect(() => {
    if (!has_open_run) return;
    const onKeyDown = event => {
      if (event.key !== 'Escape' || event.defaultPrevented) return;
      // dialogs and menus on top close first (not tooltips: they show on hover)
      if (document.querySelector('.bp6-overlay-open .bp6-dialog, .bp6-popover:not(.bp6-tooltip) .bp6-popover-content')) return;
      close_ref.current();
    };
    window.addEventListener('keydown', onKeyDown);
    return () => window.removeEventListener('keydown', onKeyDown);
  }, [has_open_run]);

  return <Page>
    <div style={{ display: 'flex', alignItems: 'center', gap: 12, flexWrap: 'wrap', marginBottom: 6 }}>
      <h2 className={Classes.HEADING} style={{ margin: 0, fontSize: 22 }}>Runs</h2>
      {crumbs && <Breadcrumbs items={crumbs} minVisibleItems={2}/>}
      {live && <Tooltip content="Some runs run now: the page follows them">
        <Tag minimal intent={Intent.PRIMARY} icon={<Spinner size={10}/>}>Live</Tag>
      </Tooltip>}
      <span style={{ marginLeft: 'auto', display: 'flex', gap: 4, alignItems: 'center' }}>
        <Button variant="minimal" icon="code" text="Python" title="Read these runs from a notebook or a script" onClick={() => setPython(experiment)}/>
        <CopyLinkButton text={join_title(experiment || 'Runs', project_name(project))}/>
      </span>
    </div>
    <div className={Classes.TEXT_MUTED} style={{ marginBottom: 14, display: 'flex', alignItems: 'center', gap: 8, flexWrap: 'wrap' }}>
      {experiment
        ? <>Runs of <strong>{experiment}</strong> and of the experiments under it, whatever their commit{node ? <>: {describe(node_totals)}</> : null}.</>
        : <>Every run of the project, whatever its commit, grouped by experiment. Open an experiment to see its runs and those under it.</>}
    </div>

    <div className="qa-toolbar">
      <InputGroup leftIcon="filter" placeholder="Filter: lr=0.001 resnet -failed…" value={q} onChange={onQ} style={{ width: 220 }} aria-label="Filter the runs"
        rightElement={q ? <Button variant="minimal" icon="cross" aria-label="Clear the filter" onClick={() => onQ('')}/> : undefined}
        title="Words must all match the run's input, configurations, parameters, experiment, who ran it or its commit. -word excludes, id:123 a run"/>
      {user.is_logged && <SegmentedControl
        aria-label="Whose runs"
        size="small"
        options={[{ value: 'mine', label: 'Yours', icon: 'person' }, { value: 'all', label: 'Everyone', icon: 'people' }]}
        value={state.scope}
        onValueChange={scope => updateRuns({ scope }, { replace: false })}
      />}
      {state.user && <Tag large minimal icon="person" onRemove={() => updateRuns({ user: '' }, { replace: false })} title="Only this person's runs">Runs of {state.user}</Tag>}
      <HTMLSelect
        aria-label="Time window"
        value={WINDOWS.includes(state.days) ? state.days : ''}
        onChange={e => updateRuns({ days: Number(e.target.value) }, { replace: false })}
        options={[
          ...(WINDOWS.includes(state.days) ? [] : [{ value: '', label: window_label(state.days) }]),
          ...WINDOWS.map(days => ({ value: days, label: window_label(days), disabled: state.ci && days > MAX_DAYS_WITH_CI })),
        ]}
      />
      <span style={{ display: 'flex', gap: 4 }} role="group" aria-label="Quick filters">
        {STATUS_CHIPS.map(chip => <Button
          key={chip.key} size="small" icon={chip.icon} text={chip.label} title={chip.title}
          active={active_chip?.key === chip.key} aria-pressed={active_chip?.key === chip.key}
          intent={active_chip?.key === chip.key ? Intent.PRIMARY : undefined} variant={active_chip?.key === chip.key ? undefined : 'outlined'}
          onClick={() => updateRuns({ status: active_chip?.key === chip.key ? [] : chip.status }, { replace: false })}
        />)}
      </span>
      <Tooltip content="Leave out the runs sent by CI pipelines: experiments are what people run, from their terminal, a notebook or the web app." placement="bottom" openOnTargetFocus={false}>
        <Switch label="Hide the CI" checked={!state.ci} onChange={e => updateRuns({ ci: !e.target.checked, days: e.target.checked ? state.days : Math.min(state.days, MAX_DAYS_WITH_CI) }, { replace: false })}/>
      </Tooltip>
    </div>

    <div className="qa-runs-layout">
      <Card compact className="qa-runs-tree">
        <ExperimentTree
          key={project}
          project={project}
          tree={tree}
          selected={experiment}
          loading={experiments.isFetching && !experiments.data}
          has_more={experiments.data?.has_more}
          onPython={path => setPython(path)}
          search_params={tree_search}
        />
      </Card>
      <div style={{ minWidth: 0 }}>
        {[[experiments, 'the experiments'], [runs_query, 'the runs']].filter(([query]) => query.error).map(([query, what]) => <Callout key={what} intent={Intent.DANGER} icon="error" title={`Could not load ${what}`} style={{ marginBottom: 12 }}>
          <p><code>{errorMessage(query.error)}</code></p>
          {query.error.response?.status === 404 && <p>The server may be older than the web app: ask your admins to update it.</p>}
          <Button icon="refresh" text="Retry" onClick={() => query.refetch()}/>
        </Callout>)}
        {missing_run && <Callout icon="search" style={{ marginBottom: 12 }} title={`Run ${state.run} isn't in this view`}>
          <p>It may be older than the time window, in another experiment, or filtered out.</p>
          <Button text="Close" onClick={() => updateRuns({ run: null, compare: null })}/>
        </Callout>}
        <div aria-busy={runs_query.isPlaceholderData} style={{ opacity: runs_query.isPlaceholderData ? 0.6 : 1, transition: 'opacity 150ms' }}>
        {plottable.length > 0 && <SectionErrorBoundary title="The plots failed">
          <Suspense fallback={<Card compact style={{ marginBottom: 12, height: 80 }}><Spinner size={16}/></Card>}>
            <RunsPlots
              runs={plot_runs}
              plotted={plottable.length}
              total={pool.length}
              groups={plot_groups}
              group_by={group_by}
              params={varying}
              lines={state.lines}
              onState={changes => updateRuns(changes)}
              options={options}
              setOptions={setOptions}
              preview={preview}
              register={register}
              main_metrics={metrics_config.main_metrics}
              selected_count={selected.length}
              onClearSelection={() => updateRuns({ selected: [] })}
              onShowAll={() => updateRuns({ hidden: [] })}
            />
          </Suspense>
        </SectionErrorBoundary>}
        <Card compact style={{ padding: 0 }}>
          {runs.length > 0 && <div style={{ display: 'flex', alignItems: 'center', gap: 8, padding: '8px 12px', borderBottom: '1px solid var(--qa-border)', flexWrap: 'wrap' }}>
            <span className={Classes.TEXT_MUTED}>{runs.length}{runs_query.hasNextPage ? '+' : ''} run{runs.length === 1 ? '' : 's'}{selected.length > 0 ? `, ${selected.length} selected` : ''}</span>
            <span style={{ marginLeft: 'auto', display: 'flex', gap: 8 }}>
              {selected_runs.length === 2 && <Tooltip content={<span>Open <strong>{labels[selected_runs[0].id]}</strong> with <strong>{labels[selected_runs[1].id]}</strong>, ticked second, as its reference</span>}>
                <Button icon="comparison" intent={Intent.PRIMARY} text="Compare the 2 selected" onClick={() => { opened_here.current = true; updateRuns({ run: selected_runs[0].id, compare: selected_runs[1].id }, { replace: false }); }}/>
              </Tooltip>}
              <ColumnsMenu keys={all_metric_keys} shown={metrics} onChange={chosen => updateRuns({ metrics: chosen })}/>
            </span>
          </div>}
          {runs_query.isPending && <NonIdealState icon={<Spinner/>} title="Loading the runs…"/>}
          {runs_query.isSuccess && runs.length === 0 && <Empty state={state} experiment={experiment} docs_root={docs_root} user={user} has_runs={has_runs}/>}
          {runs.length > 0 && <RunsTable
            runs={runs}
            groups={table_groups}
            group_by={group_by}
            labels={labels}
            styles={styles}
            plotted={plotted}
            hidden={state.hidden}
            selected={selected}
            open_id={state.run}
            metrics={metrics}
            params={params}
            best={best}
            contenders={contenders}
            available_metrics={available_metrics}
            node={path_parts(experiment).length > 0 && node && node.children.length === 0 ? null : experiment}
            project={project}
            sort={state.sort}
            onSort={key => updateRuns({ sort: state.sort?.key === key ? (state.sort.desc === first_sort(key, available_metrics).desc ? { key, desc: !state.sort.desc } : null) : first_sort(key, available_metrics) })}
            onOpen={open}
            onUser={name => updateRuns({ user: name }, { replace: false })}
            onToggleHidden={id => toggle_in('hidden', id)}
            onToggleSelected={id => toggle_in('selected', id)}
            onSetHidden={ids => updateRuns({ hidden: ids })}
            onSetSelected={ids => updateRuns({ selected: ids })}
            onHover={onHover}
          />}
        </Card>
        </div>
        {runs_query.hasNextPage && !runs_query.error && <LoadMore
          onLoadMore={() => !runs_query.isFetchingNextPage && runs_query.fetchNextPage()}
          loading={runs_query.isFetchingNextPage}
          loaded={loaded.length}
          text="More runs"
        />}
        {scanned && <p className={Classes.TEXT_MUTED} style={{ marginTop: 8, fontSize: 12 }}>
          <Icon icon="info-sign" size={12}/> The filter looked at the {scanned} latest runs. Open an experiment or shorten the time window to search older ones.
        </p>}
        {runs.length > 0 && <p className={Classes.TEXT_MUTED} style={{ marginTop: 8, fontSize: 12 }}>
          Click a run to open it. On the focused row, <kbd className={Classes.KEY}>Space</kbd> selects it and <kbd className={Classes.KEY}>h</kbd> hides it from the plots. Sorting orders the runs loaded.
        </p>}
      </div>
    </div>

    {open_run && <Suspense fallback={null}>
      <RunPanel
        project={project}
        project_data={project_data}
        run={open_run}
        compare={compare_run}
        runs={runs}
        labels={labels}
        onClose={close}
        onCompare={id => updateRuns({ compare: id })}
      />
    </Suspense>}
    {python !== null && <PythonDialog project={project} experiment={python} state={state} onClose={() => setPython(null)}/>}
  </Page>;
};

export default RunsPage;
