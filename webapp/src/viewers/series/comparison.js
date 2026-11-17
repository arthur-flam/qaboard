// The series of many runs on the same plots (SeriesComparison.jsx): which runs, how they are named and colored, and
// the traces to draw. Pure functions, tested in __tests__/seriesComparison.test.js.
import { smooth } from './series';

// One color per run, or per value of a parameter. Readable on the light and dark themes; runs past the last color
// start again from the first with dotted lines.
export const RUN_COLORS = [
  '#2d72d2', '#d1980b', '#238551', '#cd4246', '#9752fe', '#00a396',
  '#d33d17', '#db2c6f', '#147eb3', '#946638', '#7961db', '#738091',
];
const DASHES = ['solid', 'dot', 'dashdot', 'longdash'];
export const run_style = index => ({ color: RUN_COLORS[index % RUN_COLORS.length], dash: DASHES[Math.floor(index / RUN_COLORS.length) % DASHES.length] });

const is_object = value => value !== null && typeof value === 'object' && !Array.isArray(value);
// what the web app doesn't show as configuration: view hints, regions of interest
const HIDDEN_PARAMS = new Set(['view', 'roi']);

// A run's parameters: the mappings of its configurations, its tuning parameters, then what it reported itself
export const run_params = output => {
  const params = {};
  for (const source of [...(output?.configurations ?? []), output?.extra_parameters, output?.data?.params])
    if (is_object(source))
      for (const [key, value] of Object.entries(source))
        if (!HIDDEN_PARAMS.has(key)) params[key] = value;
  return params;
};

const text = value => typeof value === 'string' ? value : JSON.stringify(value);

// The parameters that tell the runs apart: those that don't have the same value for every run, sorted by name
export const varying_params = outputs => {
  const values = new Map();
  for (const output of outputs)
    for (const [key, value] of Object.entries(run_params(output)))
      values.set(key, (values.get(key) ?? new Set()).add(text(value)));
  const all = outputs.map(run_params);
  return [...values.keys()]
    .filter(key => values.get(key).size > 1 || all.some(params => !(key in params)))
    .sort((a, b) => a.localeCompare(b));
};

// A short name for each run, with only what tells it apart from the others: its input when runs have several, the
// names of its configurations that not all share, and its varying parameters. { [id]: label }
export const run_labels = outputs => {
  const inputs = new Set(outputs.map(output => output.test_input_path));
  const names = outputs.map(output => (output.configurations ?? []).filter(c => typeof c === 'string'));
  const shared = new Set((names[0] ?? []).filter(name => names.every(list => list.includes(name))));
  const varying = varying_params(outputs);
  const labels = {};
  const seen = new Map();
  outputs.forEach((output, index) => {
    const params = run_params(output);
    const parts = [
      ...(inputs.size > 1 ? [output.test_input_path] : []),
      ...names[index].filter(name => !shared.has(name)),
      ...varying.filter(key => key in params).map(key => `${key}=${text(params[key])}`),
      ...(output.platform && outputs.some(o => o.platform !== output.platform) ? [`@${output.platform}`] : []),
    ];
    let label = parts.join(' · ') || output.test_input_path || `run ${output.id}`;
    // runs that only differ by something we don't show
    if (seen.has(label)) label = `${label} · #${output.id}`;
    seen.set(label, true);
    labels[output.id] = label;
  });
  return labels;
};

// The color of each run: its own, or with `color_by` (a parameter) the color of its value, so that the runs that
// share it look alike. { styles: { [id]: {color, dash} }, groups: [{ value, style, ids }] } (groups only with color_by)
export const run_styles = (outputs, color_by) => {
  const styles = {};
  if (!color_by) {
    outputs.forEach((output, index) => { styles[output.id] = run_style(index); });
    return { styles, groups: [] };
  }
  const groups = new Map();
  for (const output of outputs) {
    const params = run_params(output);
    const value = color_by in params ? text(params[color_by]) : '—';
    if (!groups.has(value)) groups.set(value, { value, style: { ...run_style(groups.size), dash: 'solid' }, ids: [] });
    groups.get(value).ids.push(output.id);
    styles[output.id] = groups.get(value).style;
  }
  return { styles, groups: [...groups.values()] };
};

// The names of all the series the runs logged, with how many runs logged each
export const series_counts = models => {
  const counts = {};
  for (const model of models)
    for (const key of Object.keys(model?.series ?? {}))
      counts[key] = (counts[key] ?? 0) + 1;
  return counts;
};

// The traces of one series: a line per run that logged it, in the run's color; its reference run's dashed and
// thinner, in the same color. Hidden runs are left out.
// runs: [{ id, label, style, model, ref_model? }]
export const comparison_traces = (key, { runs, hidden, options, reference_name = 'reference' }) => {
  const traces = [];
  for (const run of runs) {
    if (hidden?.has(run.id)) continue;
    for (const [side, model] of [['ref', run.ref_model], ['new', run.model]]) {
      const series = model?.series?.[key];
      if (!series) continue;
      const is_ref = side === 'ref';
      const name = is_ref ? `${run.label} (${reference_name})` : run.label;
      traces.push({
        x: options.x === 'time' ? series.time : series.step,
        y: smooth(series.value, options.smoothing),
        type: 'scatter',
        mode: series.step.length > 1 ? 'lines' : 'markers',
        name,
        // ours, for highlight_update
        run: run.id,
        is_ref,
        opacity: is_ref ? REF_OPACITY : 1,
        line: { color: run.style.color, width: is_ref ? REF_WIDTH : WIDTH, dash: is_ref ? 'dash' : run.style.dash },
        marker: { color: run.style.color, size: 6 },
        hovertemplate: `%{y:.5g}<extra>${name}</extra>`,
      });
    }
  }
  return traces;
};

const WIDTH = 2;
const REF_WIDTH = 1.5;
const REF_OPACITY = 0.7;

// What changes in a plot's traces when a run is highlighted: its lines are thicker, the others fade. null: as drawn.
// For Plotly.restyle: a plot isn't drawn again when the pointer moves over the runs' list.
export const highlight_update = (traces, highlighted) => {
  const none = highlighted === null || highlighted === undefined;
  return {
    opacity: traces.map(trace => !none && trace.run !== highlighted ? 0.12 : (trace.is_ref ? REF_OPACITY : 1)),
    // bands have no line
    'line.width': traces.map(trace => trace.is_band ? 0 : trace.is_ref ? REF_WIDTH : (!none && trace.run === highlighted ? 3 : WIDTH)),
  };
};

// Steps where we compute a group's mean: all the steps its runs logged, at most this many (evenly picked)
const MAX_BAND_POINTS = 600;

// The value of a run's series at x, linearly between its points; undefined outside of them
const value_at = (xs, ys, x, from) => {
  // from: where to start looking, xs are sorted: the caller walks forward
  let i = from;
  while (i < xs.length - 1 && xs[i + 1] < x) i += 1;
  if (x < xs[0] || x > xs[xs.length - 1]) return { value: undefined, index: i };
  if (xs[i] === x) return { value: ys[i], index: i };
  const j = Math.min(i + 1, xs.length - 1);
  if (xs[j] === x) return { value: ys[j], index: i };
  const t = (x - xs[i]) / (xs[j] - xs[i]);
  return { value: ys[i] + t * (ys[j] - ys[i]), index: i };
};

// The mean, min and max of several runs' series at each step: runs are compared where they have values (a run that
// stopped early leaves the others). NaN values (diverged) are skipped. { x, mean, min, max, count }
export const group_band = (series_list, { x = 'step', smoothing = 0 } = {}) => {
  const curves = series_list
    .filter(Boolean)
    .map(series => {
      const xs = x === 'time' ? series.time : series.step;
      const ys = smooth(series.value, smoothing);
      // sorted by x, without missing values
      const points = xs.map((v, i) => [v, ys[i]]).filter(([a, b]) => Number.isFinite(a) && Number.isFinite(b)).sort((a, b) => a[0] - b[0]);
      return { xs: points.map(p => p[0]), ys: points.map(p => p[1]) };
    })
    .filter(curve => curve.xs.length > 0);
  if (curves.length === 0) return { x: [], mean: [], min: [], max: [], count: [] };
  let grid = [...new Set(curves.flatMap(curve => curve.xs))].sort((a, b) => a - b);
  if (grid.length > MAX_BAND_POINTS) {
    const step = (grid.length - 1) / (MAX_BAND_POINTS - 1);
    grid = Array.from({ length: MAX_BAND_POINTS }, (_, i) => grid[Math.round(i * step)]);
  }
  const cursors = curves.map(() => 0);
  const band = { x: [], mean: [], min: [], max: [], count: [] };
  for (const at of grid) {
    const values = [];
    curves.forEach((curve, c) => {
      const { value, index } = value_at(curve.xs, curve.ys, at, cursors[c]);
      cursors[c] = index;
      if (value !== undefined && Number.isFinite(value)) values.push(value);
    });
    if (values.length === 0) continue;
    band.x.push(at);
    band.mean.push(values.reduce((a, b) => a + b, 0) / values.length);
    band.min.push(Math.min(...values));
    band.max.push(Math.max(...values));
    band.count.push(values.length);
  }
  return band;
};

// A translucent version of a color, for bands
const with_alpha = (hex, alpha) => {
  const value = parseInt(hex.slice(1), 16);
  return `rgba(${(value >> 16) & 255}, ${(value >> 8) & 255}, ${value & 255}, ${alpha})`;
};

// The traces of one series, a group per value of a parameter: the mean of its runs as a line, their range as a band.
// groups: [{ value, label, style, ids }], runs: [{ id, model }]. Groups whose runs are all hidden are left out.
// Groups of one run are a plain line. A trace's `run` is its group's key, for highlight_update.
export const group_traces = (key, { groups, runs, hidden, options }) => {
  const models = new Map(runs.map(run => [run.id, run.model]));
  const traces = [];
  for (const group of groups) {
    const series = group.ids.filter(id => !hidden?.has(id)).map(id => models.get(id)?.series?.[key]).filter(Boolean);
    if (series.length === 0) continue;
    const band = group_band(series, options);
    if (band.x.length === 0) continue;
    const name = `${group.label} (${series.length} run${series.length === 1 ? '' : 's'})`;
    const id = `group:${group.value}`;
    if (series.length > 1) {
      // the range: the lower edge, then the upper one filled down to it
      traces.push(
        { x: band.x, y: band.min, type: 'scatter', mode: 'lines', line: { width: 0, color: group.style.color }, hoverinfo: 'skip', showlegend: false, run: id, is_band: true, opacity: 1 },
        { x: band.x, y: band.max, type: 'scatter', mode: 'lines', fill: 'tonexty', fillcolor: with_alpha(group.style.color, 0.18), line: { width: 0, color: group.style.color }, hoverinfo: 'skip', showlegend: false, run: id, is_band: true, opacity: 1 },
      );
    }
    traces.push({
      x: band.x, y: band.mean, type: 'scatter', mode: band.x.length > 1 ? 'lines' : 'markers', name, run: id, is_ref: false, opacity: 1,
      line: { color: group.style.color, width: WIDTH, dash: 'solid' },
      marker: { color: group.style.color, size: 6 },
      customdata: band.min.map((min, i) => [min, band.max[i], band.count[i]]),
      hovertemplate: `%{y:.5g} (%{customdata[0]:.4g} to %{customdata[1]:.4g}, %{customdata[2]} runs)<extra>${name}</extra>`,
    });
  }
  return traces;
};
