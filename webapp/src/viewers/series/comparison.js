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
// thinner, in the same color. Hidden runs are left out; with a highlighted run, the others fade.
// runs: [{ id, label, style, model, ref_model? }]
export const comparison_traces = (key, { runs, hidden, highlighted, options, reference_name = 'reference' }) => {
  const traces = [];
  for (const run of runs) {
    if (hidden?.has(run.id)) continue;
    const faded = highlighted !== undefined && highlighted !== null && highlighted !== run.id;
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
        run: run.id,
        opacity: faded ? 0.12 : (is_ref ? 0.7 : 1),
        line: { color: run.style.color, width: is_ref ? 1.5 : (highlighted === run.id ? 3 : 2), dash: is_ref ? 'dash' : run.style.dash },
        marker: { color: run.style.color, size: 6 },
        hovertemplate: `%{y:.5g}<extra>${name}</extra>`,
        hoverinfo: faded ? 'skip' : undefined,
      });
    }
  }
  // the highlighted run on top
  return traces.sort((a, b) => (a.run === highlighted) - (b.run === highlighted));
};
