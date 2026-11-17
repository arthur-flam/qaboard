// What the views of many runs' series share: the Summary's comparison of a batch (../SeriesComparison.jsx) and the
// Runs page (src/runs/RunsPlots.jsx). One plot per series, a line per run.
import { useRef } from "react";

import Plot, { restylePlot } from "../../components/Plot";
import { fileQuery } from "../../api/queries";
import { REFRESH_INTERVAL, parse_file, plot_config, plot_layout } from "./SeriesOptions";
import { highlight_update } from "./comparison";

export const SERIES_FILE = 'series.json';
export const PLOT_WIDTH = 460;
export const PLOT_HEIGHT = 300;

// runs whose file isn't series are left out
const parse = text => parse_file(text).model;

// A run's series, the file made for plots (qaboard/series.py), refreshed while it runs
export const series_query = output => ({
  ...fileQuery(output?.output_dir_url ? `${output.output_dir_url}/${SERIES_FILE}` : undefined, { is_running: output?.is_running, responseType: 'text' }),
  select: parse,
  // runs without series: no file
  retry: false,
  refetchInterval: output?.is_running ? REFRESH_INTERVAL : false,
});

// with many runs, a label with every run's value hides the plot: only the closest
// register: tells the comparison the plot's element once Plotly drew it, null when it is removed
export const ComparisonPlot = ({ name, traces, options, many, register, width = PLOT_WIDTH, height = PLOT_HEIGHT }) => <Plot
  data={traces}
  layout={plot_layout(name, options, { width, height, hovermode: many ? 'closest' : 'x unified' })}
  config={plot_config}
  onInitialized={(_, element) => register(element)}
  onPurge={() => register(null)}
/>;

// The run under the pointer, e.g. in a list: its lines come forward in every plot. Not React state: Plotly restyles
// the plots that are drawn, instead of React drawing them all again each time the pointer moves.
// [highlight(id or null), register(key) => element => ...]
export const useHighlight = () => {
  const plots = useRef(new Map());
  const highlight = id => plots.current.forEach(element => restylePlot(element, highlight_update(element.data, id)));
  const register = key => element => element ? plots.current.set(key, element) : plots.current.delete(key);
  return [highlight, register];
};
