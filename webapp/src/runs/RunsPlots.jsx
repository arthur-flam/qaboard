// Above the runs' table: what they logged as they ran, on the same plots, one line per run in its row's color.
// The table drives the plots: rows hidden with their eye leave them, selected rows are plotted alone, the row under
// the pointer comes forward (the page's useHighlight). Grouped by a parameter, each group can be its mean and range.
import { useCallback, useState } from "react";
import { useQueries } from "@tanstack/react-query";
import { Button, Callout, Card, Classes, HTMLSelect, InputGroup, SegmentedControl, Spinner } from "@blueprintjs/core";

import { SeriesOptions } from "../viewers/series/SeriesOptions";
import { comparison_traces, group_traces, series_counts } from "../viewers/series/comparison";
import { ComparisonPlot, series_query } from "../viewers/series/plots";
import { by_interest } from "./table";

const SHOWN_AT_FIRST = 6;
// plots fill the card's width: as many as fit, at least this wide
const MIN_PLOT_WIDTH = 320;
const PLOT_HEIGHT = 250;
const GAP = 4;

// The width of the plots, from the card's: [ref, width]
const usePlotWidth = () => {
  const [width, setWidth] = useState(null);
  const ref = useCallback(element => {
    if (!element) return;
    const observer = new ResizeObserver(([entry]) => {
      const available = Math.floor(entry.contentRect.width);
      const columns = Math.max(1, Math.floor((available + GAP) / (MIN_PLOT_WIDTH + GAP)));
      const next = Math.floor((available - GAP * (columns - 1)) / columns);
      setWidth(width => width === next ? width : next);
    });
    observer.observe(element);
    return () => observer.disconnect();
  }, []);
  return [ref, width ?? MIN_PLOT_WIDTH];
};
const toolbar_style = { display: 'flex', alignItems: 'center', gap: 14, flexWrap: 'wrap', marginBottom: 8 };

// - runs: those to plot: [{ id, label, style, output }], at most MAX_PLOTTED
// - plotted: how many runs are plotted, hidden or not, and total: how many could be, to say when some are left out
// - groups: when grouped by a parameter, [{ value, label, style, ids }]
export const RunsPlots = ({ runs, plotted, total, groups, group_by, params, lines, onState, options, setOptions, preview, register, main_metrics, selected_count, onClearSelection, onShowAll }) => {
  const queries = useQueries({ queries: runs.map(run => series_query(run.output)) });
  const [filter, setFilter] = useState('');
  const [show_all, setShowAll] = useState(false);
  const [plots_ref, plot_width] = usePlotWidth();

  const loading = queries.some(query => query.isPending);
  const logged = runs.map((run, index) => ({ ...run, model: queries[index]?.data }))
    .filter(run => run.model && Object.keys(run.model.series).length > 0);
  const counts = series_counts(logged.map(run => run.model));
  const keys = by_interest(Object.keys(counts), main_metrics);
  const needle = filter.trim().toLowerCase();
  const matching = needle ? keys.filter(key => key.toLowerCase().includes(needle)) : keys;
  const shown = show_all ? matching : matching.slice(0, SHOWN_AT_FIRST);
  const grouped = !!group_by && groups.length > 0;
  const as_bands = grouped && lines === 'mean';
  const no_hidden = new Set();

  // every run is hidden: the card stays, with how to get them back
  if (runs.length === 0)
    return <Card compact style={{ marginBottom: 12, display: 'flex', alignItems: 'center', gap: 10 }} data-testid="runs-plots">
      <h4 className={Classes.HEADING} style={{ margin: 0 }}>Logged series</h4>
      <span className={Classes.TEXT_MUTED}>Every run is hidden from the plots.</span>
      <Button size="small" icon="eye-open" text="Show them" onClick={onShowAll}/>
    </Card>;
  if (logged.length === 0)
    return loading
      ? <Card compact style={{ marginBottom: 12, minHeight: 80, display: 'flex', alignItems: 'center', gap: 8 }}><Spinner size={16}/><span className={Classes.TEXT_MUTED}>Loading what the runs logged…</span></Card>
      : null;

  return <Card compact style={{ marginBottom: 12 }} data-testid="runs-plots">
    <div style={{ display: 'flex', alignItems: 'baseline', gap: 10, flexWrap: 'wrap', marginBottom: 6 }}>
      <h4 className={Classes.HEADING} style={{ margin: 0 }}>Logged series</h4>
      <span className={Classes.TEXT_MUTED} style={{ fontSize: 12 }}>
        {logged.length} run{logged.length === 1 ? '' : 's'}{selected_count > 0 ? ' selected' : ''}, {keys.length} series
        {loading && <Spinner size={12} style={{ display: 'inline-block', marginLeft: 6, verticalAlign: 'middle' }} aria-label="Loading more runs"/>}
      </span>
      {selected_count > 0 && <Button size="small" variant="minimal" icon="cross" text="Plot every run shown" onClick={onClearSelection}/>}
      <span className={Classes.TEXT_MUTED} style={{ fontSize: 12, marginLeft: 'auto' }}>
        {selected_count > 0 ? 'Only the selected rows.' : 'Tick rows to plot only them, hover one to bring it forward.'}
      </span>
    </div>
    {total > plotted && <Callout compact icon="info-sign" style={{ marginBottom: 8 }}>
      Plotting the first {plotted} of {total} runs. Select rows, or open an experiment, to plot others.
    </Callout>}
    <div style={toolbar_style}>
      <SeriesOptions options={options} setOptions={setOptions} preview={preview}/>
      {params.length > 0 && <HTMLSelect
        minimal
        aria-label="Group the runs by"
        value={group_by}
        onChange={e => onState({ group_by: e.target.value })}
        options={[{ value: '', label: 'One color per run' }, ...params.map(param => ({ value: param, label: `Group by ${param}` }))]}
      />}
      {grouped && <SegmentedControl
        size="small"
        aria-label="Lines of a group"
        value={lines}
        onValueChange={value => onState({ lines: value })}
        options={[{ value: 'runs', label: 'Each run' }, { value: 'mean', label: 'Mean and range' }]}
      />}
      {keys.length > 4 && <InputGroup size="small" leftIcon="search" placeholder="Filter series" value={filter} onChange={e => setFilter(e.target.value)} style={{ width: 160 }} aria-label="Filter series"/>}
    </div>
    <div ref={plots_ref} style={{ display: 'flex', flexWrap: 'wrap', gap: GAP }}>
      {shown.map(key => <ComparisonPlot
        key={`${key}|${as_bands}`}
        name={counts[key] < logged.length ? `${key} (${counts[key]} of ${logged.length} runs)` : key}
        options={options}
        many={logged.length > 6 || as_bands}
        register={register(key)}
        width={plot_width}
        height={PLOT_HEIGHT}
        traces={as_bands
          ? group_traces(key, { groups, runs: logged, hidden: no_hidden, options })
          : comparison_traces(key, { runs: logged, hidden: no_hidden, options })}
      />)}
    </div>
    {matching.length > shown.length && <Button size="small" variant="minimal" icon="chevron-down" onClick={() => setShowAll(true)}>
      Show the {matching.length - shown.length} other series
    </Button>}
    {needle && matching.length === 0 && <span className={Classes.TEXT_MUTED}>No series matches “{filter}”.</span>}
  </Card>;
};

export default RunsPlots;
