// The runs of an experiment, across commits, the running first then the latest. A row is a run: its status, a name
// with what tells it apart, its experiment under the level shown, who ran it, when, how long, its commit, the
// parameters that vary and the metrics people chose. The table drives the plots above it:
// - the eye hides a run from the plots, and grays it out;
// - ticking rows plots only them, and the best values are those of the selected rows;
// - the row under the pointer comes forward in the plots.
// Clicking a row opens the run in a panel, with its visualizations (RunPanel.jsx).
import { Fragment, useEffect, useState } from "react";
import styled from "styled-components";
import { Button, Checkbox, Classes, HTMLTable, Icon, Intent, Tag, Tooltip } from "@blueprintjs/core";

import { Link } from "../router";
import { StatusTag } from "../components/tags";
import { format, metric_info } from "../utils";
import { run_params } from "../viewers/series/comparison";
import { started_from_code } from "../viewers/runView";
import { relative_experiment } from "./tree";
import { commit_page_url, duration_seconds, format_duration, metric_direction, started } from "./table";
import { absolute, relative, value_text } from "./format";


const Table = styled(HTMLTable)`
  width: 100%;
  font-size: 13px;
  font-variant-numeric: tabular-nums;
  th { white-space: nowrap; position: sticky; top: 0; background: var(--qa-surface); z-index: 1; }
  th button.qa-sort { all: unset; cursor: pointer; display: inline-flex; align-items: center; gap: 2px; }
  th button.qa-sort:focus-visible { outline: 2px solid var(--qa-intent-primary); outline-offset: 2px; }
  td { vertical-align: middle !important; }
  tbody tr.qa-run { cursor: pointer; }
  tbody tr.qa-run:hover td { background: var(--qa-hover); }
  tbody tr.qa-run.qa-open td { background: var(--qa-selected, rgba(45, 114, 210, 0.12)); }
  tbody tr.qa-run.qa-hidden td:not(.qa-keep) { opacity: 0.45; }
  tbody tr.qa-run:focus-visible { outline: 2px solid var(--qa-intent-primary); outline-offset: -2px; }
  .qa-name { max-width: 280px; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; font-weight: 600; }
  .qa-experiment, .qa-commit-message { max-width: 220px; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
  td.qa-number, th.qa-number { text-align: right; }
  .qa-best { font-weight: 700; color: var(--qa-intent-success); }
  tr.qa-group td { background: var(--qa-surface-alt); font-weight: 600; }
`;

const swatch = (style, shown) => ({
  display: 'inline-block', width: 16, height: 0, verticalAlign: 'middle',
  borderTop: `3px ${style?.dash && style.dash !== 'solid' ? 'dotted' : 'solid'} ${style?.color ?? 'transparent'}`,
  opacity: shown ? 1 : 0.3,
});


const MetricValue = ({ value, info, best }) => {
  if (value === undefined || value === null) return <span className={Classes.TEXT_MUTED}>—</span>;
  if (typeof value !== 'number') return <span>{String(value)}</span>;
  const scaled = value * (info?.scale ?? 1);
  return <span className={best ? 'qa-best' : undefined}>{format(scaled)}{info?.suffix ?? ''}</span>;
};

const SortHeader = ({ column, sort, onSort, children, className, title }) => {
  const active = sort?.key === column;
  return <th className={className} aria-sort={active ? (sort.desc ? 'descending' : 'ascending') : 'none'} title={title}>
    <button type="button" className="qa-sort" onClick={() => onSort(column)}>
      {children}
      <Icon icon={active ? (sort.desc ? 'caret-down' : 'caret-up') : 'double-caret-vertical'} size={12} className={active ? undefined : Classes.TEXT_MUTED} style={{ opacity: active ? 1 : 0.4 }}/>
    </button>
  </th>;
};

const MetricHeader = ({ metric, available_metrics }) => {
  const info = metric_info(available_metrics, metric);
  const { smaller, guessed } = metric_direction(metric, available_metrics?.[metric]);
  const direction = smaller === null ? "We don't know if lower is better: declare the metric to highlight the best runs."
    : `${smaller ? 'Lower' : 'Higher'} is better${guessed ? ' (guessed from its name: declare the metric to be sure)' : ''}. The best value is in bold.`;
  return <Tooltip content={<span><strong>{info.label ?? metric}</strong>{info.description ? <> {info.description}</> : null}<br/>{direction}</span>} hoverOpenDelay={400}>
    <span>{info.short_label ?? info.label ?? metric}{info.suffix ? <span className={Classes.TEXT_MUTED}> [{info.suffix}]</span> : null}</span>
  </Tooltip>;
};

// How long a run ran, going on while it runs: only its own cell renders every second
const Duration = ({ run }) => {
  const now = useNow(run.is_running ? 1000 : null);
  return <>
    {run.is_running && <Tag minimal intent={Intent.PRIMARY} style={{ marginRight: 4 }}>live</Tag>}
    {format_duration(duration_seconds(run, now))}
  </>;
};

const Row = ({ run, project, label, node, style, plotted, hidden, selected, open, best, contenders, metrics, params, available_metrics, onOpen, onToggleHidden, onToggleSelected, onHover, onUser }) => {
  const run_param_values = run_params(run);
  const experiment = relative_experiment(run.batch?.experiment, node);
  const when = started(run);
  const dirty = run.data?.dirty;
  return <tr
    className={`qa-run${hidden ? ' qa-hidden' : ''}${open ? ' qa-open' : ''}`}
    tabIndex={0}
    aria-label={`${label}: open the run`}
    // links, buttons and checkboxes in the row do their own thing
    onClick={event => !event.target.closest('a, button, input, label, .qa-keep') && onOpen(run.id)}
    onKeyDown={event => {
      if (event.target !== event.currentTarget) return;
      if (event.key === 'Enter') onOpen(run.id);
      else if (event.key === ' ') { event.preventDefault(); onToggleSelected(run.id); }
      else if (event.key === 'h') onToggleHidden(run.id);
    }}
    onMouseEnter={() => plotted && !hidden && onHover(run.id)}
    onMouseLeave={() => onHover(null)}
    onFocus={() => plotted && !hidden && onHover(run.id)}
    onBlur={() => onHover(null)}
  >
    <td className="qa-keep" style={{ width: 28 }}>
      <Checkbox checked={selected} onChange={() => onToggleSelected(run.id)} aria-label={`Select ${label}`} style={{ margin: 0 }}/>
    </td>
    <td className="qa-keep" style={{ width: 52, whiteSpace: 'nowrap' }}>
      <Tooltip content={!plotted ? (started_from_code(run) ? 'Not plotted: only the first runs are' : 'Logged no series: started by qa run') : hidden ? 'Show in the plots (h)' : 'Hide from the plots (h)'} hoverOpenDelay={400}>
        <Button
          size="small" variant="minimal" disabled={!plotted}
          icon={hidden ? 'eye-off' : 'eye-open'}
          aria-label={`${hidden ? 'Show' : 'Hide'} ${label} in the plots`}
          aria-pressed={!hidden}
          onClick={() => onToggleHidden(run.id)}
          style={{ minWidth: 22, minHeight: 22, padding: 2 }}
        />
      </Tooltip>
      <span style={swatch(style, plotted && !hidden)}/>
    </td>
    <td style={{ width: 24 }}><StatusTag output={run}/></td>
    <td><div className="qa-name" title={label}>{label}</div></td>
    {node !== null && <td><div className="qa-experiment" title={run.batch?.label}>{experiment || <span className={Classes.TEXT_MUTED}>—</span>}</div></td>}
    {metrics.map(metric => <td key={metric} className="qa-number">
      <MetricValue value={run.metrics?.[metric]} info={metric_info(available_metrics, metric)} best={contenders.has(run.id) && best[metric] !== undefined && run.metrics?.[metric] === best[metric]}/>
    </td>)}
    {params.map(param => <td key={param} style={{ whiteSpace: 'nowrap' }}>
      {param in run_param_values ? value_text(run_param_values[param]) : <span className={Classes.TEXT_MUTED}>—</span>}
    </td>)}
    <td style={{ whiteSpace: 'nowrap' }}>
      {run.batch?.user_name
        ? <Tooltip content={`Only ${run.batch.user_name}'s runs`} hoverOpenDelay={500}>
            <Button size="small" variant="minimal" text={run.batch.user_name} onClick={() => onUser(run.batch.user_name)} style={{ minHeight: 20, padding: '0 4px' }}/>
          </Tooltip>
        : <span className={Classes.TEXT_MUTED}>—</span>}
      {run.batch?.origin === 'ci' && <Tag minimal style={{ marginLeft: 4 }}>CI</Tag>}
    </td>
    <td style={{ whiteSpace: 'nowrap' }} title={absolute(when)}>{relative(when)}</td>
    <td className="qa-number" style={{ whiteSpace: 'nowrap' }}>
      <Duration run={run}/>
    </td>
    <td style={{ whiteSpace: 'nowrap' }}>
      <Tooltip content={<span>
        <strong>{run.commit?.branch || 'commit'}</strong> {run.commit?.id?.slice(0, 8)}<br/>{(run.commit?.message ?? '').split('\n')[0]}<br/>
        {run.commit?.committer_name}{dirty ? <><br/><Icon icon="warning-sign" size={12}/> Run with changes that are not in the commit</> : null}
        <br/><span className={Classes.TEXT_MUTED}>Open the run on its commit's page</span>
      </span>} hoverOpenDelay={300}>
        <Link to={commit_page_url(project, run)} className={Classes.MONOSPACE_TEXT} aria-label={`Commit ${run.commit?.id?.slice(0, 7)}: the run on its commit's page`}>
          {run.commit?.id?.slice(0, 7)}{dirty && <span title="Run with changes that are not in the commit">*</span>}
        </Link>
      </Tooltip>
    </td>
  </tr>;
};

// The time, every `interval` ms (null: once)
const useNow = interval => {
  const [now, setNow] = useState(() => Date.now());
  useEffect(() => {
    if (!interval) return;
    const timer = setInterval(() => setNow(Date.now()), interval);
    return () => clearInterval(timer);
  }, [interval]);
  return now;
};

// groups: [{ value, runs, style }] when grouped by a parameter, else null
export const RunsTable = props => {
  const { runs, groups, group_by, labels, styles, plotted, hidden, selected, open_id, metrics, params, node, sort, onSort, onSetHidden, onSetSelected } = props;
  const all_selected = runs.length > 0 && runs.every(run => selected.includes(run.id));
  const some_selected = selected.length > 0 && !all_selected;
  const row = run => <Row
    {...props}
    key={run.id}
    run={run}
    label={labels[run.id]}
    style={styles[run.id]}
    plotted={plotted.has(run.id)}
    hidden={hidden.has(run.id)}
    selected={selected.includes(run.id)}
    open={open_id === run.id}
  />;
  const columns = 8 + (node !== null ? 1 : 0) + params.length + metrics.length;
  return <div style={{ overflowX: 'auto' }}>
    <Table compact interactive aria-label="Runs">
      <thead>
        <tr>
          <th style={{ width: 28 }}>
            <Checkbox
              checked={all_selected} indeterminate={some_selected} style={{ margin: 0 }}
              aria-label={all_selected ? 'Unselect all the runs' : 'Select all the runs'}
              onChange={() => onSetSelected(all_selected || some_selected ? [] : runs.map(run => run.id))}
            />
          </th>
          <th style={{ width: 52 }}>
            <Tooltip content={hidden.size > 0 ? 'Show every run in the plots' : 'Hide every run from the plots'} hoverOpenDelay={400}>
              <Button size="small" variant="minimal" icon={hidden.size > 0 ? 'eye-off' : 'eye-open'} aria-label={hidden.size > 0 ? 'Show every run in the plots' : 'Hide every run from the plots'}
                onClick={() => onSetHidden(hidden.size > 0 ? [] : [...plotted])} style={{ minWidth: 22, minHeight: 22, padding: 2 }}/>
            </Tooltip>
          </th>
          <SortHeader column="status" sort={sort} onSort={onSort} title="Status"><span className={Classes.TEXT_MUTED} aria-label="Status"/></SortHeader>
          <SortHeader column="name" sort={sort} onSort={onSort} title="Named by what tells the runs apart: input, configurations, parameters">Run</SortHeader>
          {node !== null && <SortHeader column="experiment" sort={sort} onSort={onSort}>Experiment</SortHeader>}
          {metrics.map(metric => <SortHeader key={metric} column={`metrics.${metric}`} sort={sort} onSort={onSort} className="qa-number">
            <MetricHeader metric={metric} available_metrics={props.available_metrics}/>
          </SortHeader>)}
          {params.map(param => <SortHeader key={param} column={`params.${param}`} sort={sort} onSort={onSort} title="A parameter that differs between the runs">{param}</SortHeader>)}
          <SortHeader column="user" sort={sort} onSort={onSort}>Who</SortHeader>
          <SortHeader column="started" sort={sort} onSort={onSort}>Started</SortHeader>
          <SortHeader column="duration" sort={sort} onSort={onSort} className="qa-number">Duration</SortHeader>
          <SortHeader column="commit" sort={sort} onSort={onSort} title="The commit the run's code came from: * when it had changes">Commit</SortHeader>
        </tr>
      </thead>
      <tbody>
        {groups
          ? groups.map(group => <Fragment key={group.value}>
              <tr className="qa-group">
                <td colSpan={columns}>
                  <span style={{ ...swatch(group.style, true), marginRight: 8 }}/>
                  {group_by} = {group.value}
                  <span className={Classes.TEXT_MUTED} style={{ fontWeight: 'normal', marginLeft: 8 }}>{group.runs.length} run{group.runs.length === 1 ? '' : 's'}</span>
                  <Button size="small" variant="minimal" style={{ marginLeft: 8 }} text="Only these"
                    onClick={() => onSetSelected(group.runs.map(run => run.id))}/>
                </td>
              </tr>
              {group.runs.map(row)}
            </Fragment>)
          : runs.map(row)}
      </tbody>
    </Table>
  </div>;
};

export default RunsTable;
