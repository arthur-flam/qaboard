// The Runs page's table: its columns, sorting, the best values, durations and links. Pure, tested in
// __tests__/runs.test.js. Runs are outputs from GET /api/v1/runs, with their `batch` and `commit`.
import { path_segment, stringify_query } from "../selection";
import { run_labels, run_params } from "../viewers/series/comparison";
import { sorted_keys } from "../viewers/series/series";
import { value_text } from "./format";

// Run statuses and bookkeeping, not metrics to show
const NOT_METRICS = new Set(['is_failed', 'compute_time', 'params']);
// More columns don't fit, people pick theirs
const DEFAULT_METRIC_COLUMNS = 4;
export const MAX_PARAM_COLUMNS = 8;

const is_number = value => typeof value === 'number' && Number.isFinite(value);

// What people look at first: the project's main metrics, then validation, then training, then the rest (learning
// rates, system metrics last). Plots and columns use the same order.
export const by_interest = (keys, main_metrics = []) => {
  const rank = key => {
    const main = main_metrics.indexOf(key);
    if (main >= 0) return main;
    if (/^(val|valid|validation|eval|test)[/_]/i.test(key)) return 100;
    if (/^(train|training)[/_]/i.test(key)) return 200;
    if (/^(system|sys|gpu|cpu)[/_]|(^|[/_])(lr|learning_rate|step|epoch)$/i.test(key)) return 400;
    return 300;
  };
  const sorted = sorted_keys(keys);
  return sorted.sort((a, b) => rank(a) - rank(b) || sorted.indexOf(a) - sorted.indexOf(b));
};

// Every metric the runs have, by interest, the most common first
export const metric_keys = (runs, main_metrics = []) => {
  const counts = new Map();
  for (const run of runs)
    for (const [key, value] of Object.entries(run.metrics ?? {}))
      if (!NOT_METRICS.has(key) && !key.startsWith('.') && (is_number(value) || typeof value === 'string'))
        counts.set(key, (counts.get(key) ?? 0) + 1);
  const keys = by_interest([...counts.keys()], main_metrics);
  const most = Math.max(0, ...counts.values());
  // metrics only a few runs have come after
  return keys.sort((a, b) => (counts.get(b) * 2 >= most) - (counts.get(a) * 2 >= most) || keys.indexOf(a) - keys.indexOf(b));
};

// The metrics shown as columns: those people chose, else the project's main metrics the runs have, else the first few
// A metric named like a parameter (the learning rate a training logs) is left out: the parameter has a column.
export const metric_columns = (runs, { chosen, main_metrics = [], params = [] } = {}) => {
  if (chosen) return chosen;
  const keys = metric_keys(runs, main_metrics).filter(key => !params.includes(key));
  const main = main_metrics.filter(key => keys.includes(key));
  return main.length > 0 ? main : keys.slice(0, DEFAULT_METRIC_COLUMNS);
};

// Lower is better? From the metric's definition, else guessed from its name, so that the best run stands out even
// for metrics nobody declared. { smaller: true | false | null, guessed }
const SMALLER = /(^|[/_.\- ])(loss|err|error|errors|mse|mae|rmse|nll|perplexity|ppl|latency|time|duration|wer|cer|fid|epe|fpr|fnr|miss)([/_.\- ]|$)/i;
const LARGER = /(^|[/_.\- ])(acc|accuracy|map|ap|ap50|ap75|auc|f1|precision|recall|iou|miou|dice|psnr|ssim|bleu|rouge|score|top1|top5|tpr|throughput|fps)([/_.\- @]|$)/i;
export const metric_direction = (key, info) => {
  if (info && info.smaller_is_better !== null && info.smaller_is_better !== undefined && info.declared !== false)
    return { smaller: !!info.smaller_is_better, guessed: false };
  if (SMALLER.test(key)) return { smaller: true, guessed: true };
  if (LARGER.test(key)) return { smaller: false, guessed: true };
  return { smaller: null, guessed: true };
};

// The best value of each metric among the runs: { [key]: value }. Only for metrics with a known direction, and
// when more than one run has a value: one run alone is not "the best".
export const best_values = (runs, keys, available_metrics) => {
  const best = {};
  for (const key of keys) {
    const { smaller } = metric_direction(key, available_metrics?.[key]);
    if (smaller === null) continue;
    const values = runs.map(run => run.metrics?.[key]).filter(is_number);
    if (values.length < 2) continue;
    best[key] = smaller ? Math.min(...values) : Math.max(...values);
  }
  return best;
};

// The runs' names: what tells them apart (run_labels). A run that nothing tells apart, e.g. alone after a filter, is
// named by its first parameters rather than its input: the name stays meaningful as the table changes.
const SHOWN_PARAMS = 3;
export const display_labels = runs => {
  const labels = run_labels(runs);
  for (const run of runs) {
    const label = labels[run.id];
    if (label !== run.test_input_path && !label.startsWith(`${run.test_input_path} · #`)) continue;
    const params = Object.entries(run_params(run)).slice(0, SHOWN_PARAMS).map(([key, value]) => `${key}=${value_text(value)}`);
    if (params.length > 0) labels[run.id] = label.includes(' · #') ? `${params.join(' · ')} · #${run.id}` : params.join(' · ');
  }
  return labels;
};

// The parameters that differ between the runs, for columns: those of the most runs first
export const param_columns = (varying, runs) => {
  const counts = Object.fromEntries(varying.map(key => [key, runs.filter(run => key in run_params(run)).length]));
  return [...varying].sort((a, b) => counts[b] - counts[a] || a.localeCompare(b));
};

// Seconds a run ran: until it ended, or until now while it runs. null when we don't know.
export const duration_seconds = (run, now = Date.now()) => {
  const start = Date.parse(run.started_at ?? '');
  if (!Number.isFinite(start)) return null;
  const end = run.finished_at ? Date.parse(run.finished_at) : (run.is_running ? now : NaN);
  if (!Number.isFinite(end)) return null;
  return Math.max(0, (end - start) / 1000);
};

// 42s, 12m 3s, 3h 05m, 2d 4h
export const format_duration = seconds => {
  if (seconds === null || seconds === undefined) return '';
  const s = Math.round(seconds);
  if (s < 60) return `${s}s`;
  if (s < 3600) return `${Math.floor(s / 60)}m ${s % 60}s`;
  if (s < 86400) return `${Math.floor(s / 3600)}h ${String(Math.floor((s % 3600) / 60)).padStart(2, '0')}m`;
  return `${Math.floor(s / 86400)}d ${Math.floor((s % 86400) / 3600)}h`;
};

// When the run started, else when the server first heard of it
export const started = run => run.started_at ?? (run.created_date ? `${run.created_date}${/Z|[+-]\d\d:?\d\d$/.test(run.created_date) ? '' : 'Z'}` : null);

// What a column sorts by. Missing values last, whatever the direction.
const sort_value = (run, key, labels) => {
  if (key === 'name') return labels[run.id] ?? '';
  if (key === 'experiment') return run.batch?.experiment ?? '';
  if (key === 'user') return run.batch?.user_name ?? '';
  if (key === 'started') return started(run) ?? '';
  if (key === 'duration') return duration_seconds(run);
  if (key === 'commit') return run.commit?.authored_datetime ?? '';
  if (key === 'status') return run.is_running ? 0 : run.is_pending ? 1 : run.is_failed ? 3 : 2;
  if (key.startsWith('metrics.')) return run.metrics?.[key.slice('metrics.'.length)];
  if (key.startsWith('params.')) return run_params(run)[key.slice('params.'.length)];
  return undefined;
};
const missing = value => value === undefined || value === null || value === '' || (typeof value === 'number' && !Number.isFinite(value));
export const sort_runs = (runs, sort, labels = {}) => {
  if (!sort) return runs;
  const direction = sort.desc ? -1 : 1;
  return [...runs].sort((a, b) => {
    const va = sort_value(a, sort.key, labels), vb = sort_value(b, sort.key, labels);
    if (missing(va) || missing(vb)) return missing(va) - missing(vb);
    if (typeof va === 'number' && typeof vb === 'number') return direction * (va - vb);
    return direction * String(va).localeCompare(String(vb), undefined, { numeric: true });
  });
};

// Sorting a column the first time: numbers and dates from the largest (the latest), names from A, metrics from their
// best value
export const first_sort = (key, available_metrics) => {
  if (key.startsWith('metrics.')) {
    const { smaller } = metric_direction(key.slice('metrics.'.length), available_metrics?.[key.slice('metrics.'.length)]);
    return { key, desc: smaller === false };
  }
  return { key, desc: ['started', 'duration', 'commit'].includes(key) };
};

// The runs in groups by a parameter's value, in the order of their first run: [{ value, runs }]
export const group_runs = (runs, param) => {
  const groups = new Map();
  for (const run of runs) {
    const params = run_params(run);
    const value = param in params ? (typeof params[param] === 'string' ? params[param] : JSON.stringify(params[param])) : '—';
    if (!groups.has(value)) groups.set(value, { value, runs: [] });
    groups.get(value).runs.push(run);
  }
  return [...groups.values()];
};

// The run on its commit's page, alone (filtered by its id), compared with another run, or with the reference
// the commit page picks by default
export const commit_page_url = (project, run, compare) => {
  const query = {
    batch: run.batch?.label && run.batch.label !== 'default' ? run.batch.label : undefined,
    filter: `id:${run.id}$`,
    ...(compare ? {
      reference: compare.commit?.id,
      batch_ref: compare.batch?.label && compare.batch.label !== 'default' ? compare.batch.label : undefined,
      filter_ref: `id:${compare.id}$`,
      ref_project: undefined,
    } : {}),
    selected_views: 'output-list',
  };
  Object.keys(query).forEach(key => query[key] === undefined && delete query[key]);
  return `/${path_segment(project)}/commit/${path_segment(run.commit?.id ?? '')}?${stringify_query(query)}`;
};

// The code that reads the runs shown, for notebooks and agents (qaboard.runs() takes the page's address too)
const py = value => JSON.stringify(value);
export const python_snippet = ({ project, experiment, state }) => {
  const args = [`project=${py(project)}`];
  if (experiment) args.push(`experiment=${py(experiment)}`);
  args.push(`since=${state.days}`);
  if (state.scope === 'mine') args.push('scope="mine"');
  if (state.user) args.push(`user=${py(state.user)}`);
  if (state.status.length === 1) args.push(`status=${py(state.status[0])}`);
  else if (state.status.length > 1) args.push(`status=${py(state.status)}`);
  if (state.q) args.push(`filter=${py(state.q)}`);
  if (state.ci) args.push('include_ci=True');
  return [
    'import qaboard as qb',
    '',
    `runs = qb.runs(${args.join(', ')})`,
    'for run in runs:',
    '    print(run.experiment, run.params, run.metrics, run.output_dir)',
    '    # run.series("val/loss"): what it logged at each step',
  ].join('\n');
};

// How to start a run in this experiment, for empty states
export const start_snippet = experiment => [
  'import qaboard as qb',
  '',
  `with qb.start_run(experiment=${py(experiment || 'my-area/my-question')}, configs=[{"lr": 1e-3}]) as run:`,
  '    for step in range(1000):',
  '        run.log({"train/loss": loss}, step=step)',
].join('\n');
