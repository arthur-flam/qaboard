/**
 * The Runs page's logic: its address, the experiments' tree, the table's columns, sorting and best values, and the
 * groups' mean and range. Run with: cd webapp && npm test -- runs
 */
import { DEFAULT_DAYS, has_runs_page, level_search, project_home, runs_path, runs_state, runs_url } from '../state';
import { ancestors, build_tree, experiment_of, find_node, relative_experiment } from '../tree';
import {
  best_values, by_interest, commit_page_url, display_labels, duration_seconds, first_sort, format_duration, group_runs, metric_columns,
  metric_direction, metric_keys, python_snippet, sort_runs,
} from '../table';
import { group_band, group_traces, highlight_update } from '../../viewers/series/comparison';

describe('the address', () => {
  it('has defaults that stay out of links', () => {
    const state = runs_state('');
    expect(state).toMatchObject({ scope: 'all', days: DEFAULT_DAYS, status: [], ci: false, q: '', selected: [], run: null, lines: 'runs', sort: null, metrics: undefined });
    expect(state.hidden).toEqual(new Set());
    expect(runs_url({ pathname: '/a/runs', search: '' }, { scope: 'all', days: DEFAULT_DAYS, ci: false, hidden: [], lines: 'runs' }).search).toBe('');
  });

  it('reads and writes everything people choose', () => {
    const search = '?scope=mine&days=7&status=running,pending&ci=1&q=lr%3D0.01&hidden=3,4&selected=5&group_by=lr&lines=mean&metrics=val/loss,val/mAP&sort=-metrics.val/mAP&run=5&vs=6&smoothing=0.6&log=1&x=time';
    const state = runs_state(search);
    expect(state).toMatchObject({
      scope: 'mine', days: 7, status: ['running', 'pending'], ci: true, q: 'lr=0.01', selected: [5], group_by: 'lr', lines: 'mean',
      metrics: ['val/loss', 'val/mAP'], sort: { key: 'metrics.val/mAP', desc: true }, run: 5, compare: 6,
      series: { smoothing: 0.6, log: true, x: 'time' },
    });
    expect([...state.hidden]).toEqual([3, 4]);
    // written back the same
    const { search: written } = runs_url({ pathname: '/a/runs', search: '' }, { ...state, hidden: state.hidden });
    expect(runs_state(written)).toEqual(state);
  });

  it("doesn't use the commit page's keys for what differs", () => {
    // ?compare= is the commit page's other references
    expect(runs_state('?compare=6').compare).toBe(null);
    expect(runs_url({ pathname: '/p/runs', search: '' }, { compare: 6 }).search).toBe('?vs=6');
  });

  it('keeps the filters when going to another level, not what was opened or selected', () => {
    expect(level_search({ pathname: '/p/runs/a', search: '?q=lr&run=4&vs=5&selected=1,2&hidden=3&days=7' })).toBe('?q=lr&days=7');
  });

  it('says which projects have the page', () => {
    expect(has_runs_page({ data: { has_runs_from_code: true } })).toBe(true);
    expect(has_runs_page({ data: { qatools_config: { project: { home: 'runs' } } } })).toBe(true);
    expect(has_runs_page({ data: {} })).toBe(false);
    expect(project_home({ data: { qatools_config: { project: { home: 'runs' } } } })).toBe('runs');
  });

  it('ignores what makes no sense', () => {
    expect(runs_state('?days=-3&status=done&run=abc&smoothing=2&scope=team')).toMatchObject({ days: DEFAULT_DAYS, status: [], run: null, scope: 'all', series: { smoothing: 0 } });
  });

  it('has a path per level of the tree', () => {
    expect(runs_path('kitefin/detector')).toBe('/kitefin/detector/runs');
    expect(runs_path('kitefin/detector', 'backbone/lr sweep#2')).toBe('/kitefin/detector/runs/backbone/lr%20sweep%232');
    expect(runs_url({ pathname: '/p/runs/a', search: '?q=x&run=4' }, { experiment: 'b/c', run: null }, { project: 'p' })).toEqual({ pathname: '/p/runs/b/c', search: '?q=x' });
  });
});

describe('the tree of experiments', () => {
  const rows = [
    { experiment: 'backbone/lr-sweep', runs: 6, running: 1, failed: 1, batches: 2, latest: '2026-11-17T10:00:00Z', users: ['alice', 'bob'] },
    { experiment: 'backbone/lr-sweep/warmup', runs: 2, running: 0, failed: 0, batches: 1, latest: '2026-11-17T11:00:00Z', users: ['bob'] },
    { experiment: 'backbone/resnet', runs: 3, batches: 1, latest: '2026-11-10T10:00:00Z', users: ['alice'] },
    { experiment: 'quantization/int8/calibration', runs: 2, batches: 1, latest: '2026-11-12T10:00:00Z', users: ['carol'] },
    { experiment: 'nightly | v2', runs: 1, batches: 1, latest: '2026-11-01T10:00:00Z', users: [] },
  ];
  const tree = build_tree(rows);

  it('folds names by their slashes, with the counts of everything under each level', () => {
    expect(tree.totals.runs).toBe(14);
    const backbone = find_node(tree, 'backbone');
    expect(backbone.totals).toMatchObject({ runs: 11, running: 1, failed: 1, latest: '2026-11-17T11:00:00Z' });
    expect([...backbone.totals.users]).toEqual(['alice', 'bob']);
    // the latest first
    expect(backbone.children.map(node => node.name)).toEqual(['lr-sweep', 'resnet']);
    const sweep = find_node(tree, 'backbone/lr-sweep');
    expect(sweep.own.runs).toBe(6);
    expect(sweep.totals.runs).toBe(8);
    expect(sweep.children.map(node => node.path)).toEqual(['backbone/lr-sweep/warmup']);
  });

  it('merges levels with a single child, like folders in code editors', () => {
    expect(tree.children.map(node => node.name)).toEqual(['backbone', 'quantization/int8/calibration', 'nightly | v2']);
    // their address still finds them
    expect(find_node(tree, 'quantization').path).toBe('quantization/int8/calibration');
    expect(build_tree(rows, { compact: false }).children.map(node => node.name)).toContain('quantization');
  });

  it('says which levels to open, and names runs under a level', () => {
    expect(ancestors(tree, 'backbone/lr-sweep/warmup')).toEqual(['backbone', 'backbone/lr-sweep']);
    expect(relative_experiment('backbone/lr-sweep/warmup', 'backbone/lr-sweep')).toBe('warmup');
    expect(relative_experiment('backbone/lr-sweep', 'backbone/lr-sweep')).toBe('');
    expect(relative_experiment('backbone/lr-sweep', '')).toBe('backbone/lr-sweep');
  });

  it('names experiments like the server', () => {
    expect(experiment_of('@alice| backbone/lr-sweep')).toBe('backbone/lr-sweep');
    expect(experiment_of('nightly | v2')).toBe('nightly | v2');
  });

  it('only folds slashes', () => {
    expect(find_node(tree, 'nightly | v2').own.runs).toBe(1);
    expect(build_tree([{ experiment: 'a//b/', runs: 1 }]).children[0].path).toBe('a/b');
  });
});

const run = (id, params, metrics, extra = {}) => ({
  id, configurations: ['base.yaml', params], extra_parameters: {}, metrics, data: { sdk: {} },
  batch: { label: `@alice| sweep`, experiment: 'sweep', user_name: 'alice' }, commit: { id: `${id}`.repeat(8), authored_datetime: `2026-11-1${id}` },
  started_at: `2026-11-17T0${id}:00:00Z`, finished_at: `2026-11-17T0${id}:30:00Z`, ...extra,
});

describe('the table', () => {
  const runs = [
    run(1, { lr: 0.1 }, { 'val/loss': 0.5, 'val/mAP': 0.3, lr: 0, 'train/loss': 0.4, 'system/gpu': 10 }),
    run(2, { lr: 0.01 }, { 'val/loss': 0.4, 'val/mAP': 0.35, lr: 0, 'train/loss': 0.45 }),
    run(3, { lr: 0.001 }, { 'val/loss': 0.6, 'val/mAP': 0.2, lr: 0 }, { is_running: true, finished_at: null }),
  ];

  it('shows the metrics people look at first', () => {
    expect(by_interest(['lr', 'system/gpu', 'train/loss', 'val/mAP', 'val/loss', 'other'])).toEqual(['val/loss', 'val/mAP', 'train/loss', 'other', 'lr', 'system/gpu']);
    expect(metric_keys(runs)).toEqual(['val/loss', 'val/mAP', 'train/loss', 'lr', 'system/gpu']);
    // a metric named like a parameter has the parameter's column
    expect(metric_columns(runs, { params: ['lr'] })).toEqual(['val/loss', 'val/mAP', 'train/loss', 'system/gpu']);
    expect(metric_columns(runs, { main_metrics: ['val/mAP', 'missing'] })).toEqual(['val/mAP']);
    expect(metric_columns(runs, { chosen: ['lr'] })).toEqual(['lr']);
  });

  it('knows or guesses which way is better', () => {
    expect(metric_direction('val/loss')).toEqual({ smaller: true, guessed: true });
    expect(metric_direction('val/mAP')).toEqual({ smaller: false, guessed: true });
    expect(metric_direction('mAP@0.5')).toEqual({ smaller: false, guessed: true });
    expect(metric_direction('val/mAP', { smaller_is_better: true, declared: true })).toEqual({ smaller: true, guessed: false });
    expect(metric_direction('weird')).toEqual({ smaller: null, guessed: true });
    expect(best_values(runs, ['val/loss', 'val/mAP', 'weird', 'system/gpu'])).toEqual({ 'val/loss': 0.4, 'val/mAP': 0.35 });
  });

  it('sorts, the missing values last', () => {
    expect(sort_runs(runs, { key: 'metrics.val/loss', desc: false }).map(r => r.id)).toEqual([2, 1, 3]);
    expect(sort_runs(runs, { key: 'metrics.train/loss', desc: true }).map(r => r.id)).toEqual([2, 1, 3]);
    expect(sort_runs(runs, { key: 'params.lr', desc: false }).map(r => r.id)).toEqual([3, 2, 1]);
    expect(sort_runs(runs, { key: 'status', desc: false }).map(r => r.id)).toEqual([3, 1, 2]);
    expect(first_sort('metrics.val/mAP')).toEqual({ key: 'metrics.val/mAP', desc: true });
    expect(first_sort('name')).toEqual({ key: 'name', desc: false });
  });

  it('says how long runs ran, and still run', () => {
    expect(duration_seconds(runs[0])).toBe(1800);
    expect(duration_seconds(runs[2], Date.parse('2026-11-17T04:00:00Z'))).toBe(3600);
    expect(duration_seconds({})).toBe(null);
    expect([42, 723, 3 * 3600 + 300, 2 * 86400 + 4 * 3600].map(format_duration)).toEqual(['42s', '12m 3s', '3h 05m', '2d 4h']);
  });

  it('names runs by what tells them apart, else by their parameters', () => {
    expect(display_labels(runs)).toEqual({ 1: 'lr=0.1', 2: 'lr=0.01', 3: 'lr=0.001' });
    // alone, nothing differs: not "train/coco"
    expect(display_labels([{ ...runs[0], test_input_path: 'train/coco' }])).toEqual({ 1: 'lr=0.1' });
    expect(display_labels([{ ...runs[0], test_input_path: 'train/coco' }, { ...runs[0], id: 9, test_input_path: 'train/coco' }])).toEqual({ 1: 'lr=0.1', 9: 'lr=0.1 · #9' });
  });

  it('groups runs by a parameter', () => {
    expect(group_runs([...runs, run(4, { lr: 0.1 }, {})], 'lr').map(g => [g.value, g.runs.map(r => r.id)])).toEqual([['0.1', [1, 4]], ['0.01', [2]], ['0.001', [3]]]);
  });

  it('links to the run on its commit page, compared with another', () => {
    expect(commit_page_url('kitefin/detector', runs[0])).toBe('/kitefin/detector/commit/11111111?batch=%40alice%7C%20sweep&filter=id%3A1%24&selected_views=output-list');
    expect(commit_page_url('p', runs[0], runs[1])).toContain('reference=22222222&batch_ref=%40alice%7C%20sweep&filter_ref=id%3A2%24');
  });

  it('writes the Python that reads the runs shown', () => {
    const code = python_snippet({ project: 'kitefin/detector', experiment: 'backbone/lr-sweep', state: runs_state('?scope=mine&days=7&status=failed&q=lr') });
    expect(code).toContain('qb.runs(project="kitefin/detector", experiment="backbone/lr-sweep", since=7, scope="mine", status="failed", filter="lr")');
    // the CI's runs, only when the page shows them, like qaboard.runs()
    expect(python_snippet({ project: 'p', experiment: '', state: runs_state('?ci=1') })).toContain('include_ci=True');
  });
});

describe('the mean and range of a group', () => {
  const series = (step, value) => ({ step, time: step, value, points: step.length });

  it('averages the runs where they have values, between their points', () => {
    const band = group_band([series([0, 10, 20], [1, 3, 5]), series([0, 20], [3, 7]), series([0, 10], [2, NaN])]);
    expect(band.x).toEqual([0, 10, 20]);
    // at 10, the second run is between its points: 5
    expect(band.mean).toEqual([2, (3 + 5) / 2, 6]);
    expect(band.min).toEqual([1, 3, 5]);
    expect(band.max).toEqual([3, 5, 7]);
    expect(band.count).toEqual([3, 2, 2]);
  });

  it('draws a line and a band per group, and highlights a group', () => {
    const runs = [{ id: 1, model: { series: { loss: series([0, 1], [1, 2]) } } }, { id: 2, model: { series: { loss: series([0, 1], [3, 4]) } } }, { id: 3, model: { series: { loss: series([0], [9]) } } }];
    const groups = [{ value: '0.1', label: 'lr=0.1', style: { color: '#2d72d2' }, ids: [1, 2] }, { value: '1', label: 'lr=1', style: { color: '#d1980b' }, ids: [3] }];
    const traces = group_traces('loss', { groups, runs, hidden: new Set(), options: { x: 'step', smoothing: 0 } });
    expect(traces.map(t => [t.run, !!t.is_band, t.name])).toEqual([['group:0.1', true, undefined], ['group:0.1', true, undefined], ['group:0.1', false, 'lr=0.1 (2 runs)'], ['group:1', false, 'lr=1 (1 run)']]);
    expect(traces[2].y).toEqual([2, 3]);
    expect(traces[1].fill).toBe('tonexty');
    expect(highlight_update(traces, 'group:1')['line.width']).toEqual([0, 0, 2, 3]);
    // hidden runs leave their group
    expect(group_traces('loss', { groups, runs, hidden: new Set([1, 3]), options: { x: 'step' } }).map(t => t.name)).toEqual(['lr=0.1 (1 run)']);
  });
});
