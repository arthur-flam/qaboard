/**
 * The series of many runs on the same plots, in the Summary view: naming and coloring the runs, the traces, and the
 * component. Run with: cd webapp && npm test -- seriesComparison
 */
import { fireEvent, screen, within } from '@testing-library/react';

import SeriesComparison from '../SeriesComparison';
import { RUN_COLORS, comparison_traces, run_labels, run_params, run_style, run_styles, series_counts, varying_params } from '../series/comparison';
import { renderWithProviders } from '../../test-utils';

// Plotly doesn't run in jsdom: we only check what would be plotted
vi.mock('../../components/Plot', () => ({
  default: ({ data, layout }) => <div
    data-testid="plot"
    data-title={layout.title.text}
    data-hovermode={layout.hovermode}
    data-traces={data.map(t => `${t.name}|${t.line.color}|${t.line.dash}|${t.opacity}|${t.y.join(' ')}`).join(',')}
  />,
}));

const run = (id, lr, extra = {}) => ({
  id, platform: 'linux', configurations: ['resnet50', { lr }], extra_parameters: {}, data: { sdk: {} },
  test_input_path: 'train/coco-mini', output_dir_url: `/s/run-${id}`, ...extra,
});
const summary = series => JSON.stringify({ format: 'qaboard-series', version: 1, started_at: 0, series });
const curve = (...value) => ({ points: value.length, step: value.map((_, i) => i), time: value.map((_, i) => 2 * i), value });


describe('naming and coloring runs', () => {
  const runs = [run(1, 0.1), run(2, 0.01), run(3, 0.01, { configurations: ['resnet50', 'augment', { lr: 0.01 }] })];

  it("reads a run's parameters from its configurations, tuning and what it reported", () => {
    expect(run_params({ configurations: ['base', { lr: 0.1, view: { show: [] } }], extra_parameters: { depth: 50 }, data: { params: { device: 'cuda' } } }))
      .toEqual({ lr: 0.1, depth: 50, device: 'cuda' });
  });

  it('finds the parameters that tell runs apart', () => {
    expect(varying_params(runs)).toEqual(['lr']);
    expect(varying_params([run(1, 0.1), run(2, 0.1)])).toEqual([]);
    // a parameter only some runs have
    expect(varying_params([run(1, 0.1), run(2, 0.1, { extra_parameters: { seed: 1 } })])).toEqual(['seed']);
  });

  it('names runs by what differs', () => {
    expect(run_labels(runs)).toEqual({ 1: 'lr=0.1', 2: 'lr=0.01', 3: 'augment · lr=0.01' });
    expect(run_labels([run(1, 0.1), run(2, 0.1, { test_input_path: 'train/full' })])).toEqual({ 1: 'train/coco-mini', 2: 'train/full' });
    // the same for all we show: their id tells them apart
    expect(run_labels([run(1, 0.1), run(2, 0.1)])).toEqual({ 1: 'train/coco-mini', 2: 'train/coco-mini · #2' });
  });

  it('gives each run a color, then dotted lines', () => {
    expect(run_styles(runs).styles[2]).toEqual({ color: RUN_COLORS[1], dash: 'solid' });
    expect(run_style(RUN_COLORS.length)).toEqual({ color: RUN_COLORS[0], dash: 'dot' });
  });

  it('or a color per value of a parameter', () => {
    const { styles, groups } = run_styles(runs, 'lr');
    expect(styles[2]).toBe(styles[3]);
    expect(styles[1].color).not.toBe(styles[2].color);
    expect(groups.map(group => [group.value, group.ids])).toEqual([['0.1', [1]], ['0.01', [2, 3]]]);
  });
});

describe('comparison_traces', () => {
  const options = { smoothing: 0, log: false, x: 'step' };
  const model = (...value) => ({ series: { loss: curve(...value) } });
  const runs = [
    { id: 1, label: 'lr=0.1', style: run_style(0), model: model(3, 2), ref_model: model(4, 3) },
    { id: 2, label: 'lr=0.01', style: run_style(1), model: model(5, 4) },
    { id: 3, label: 'no loss', style: run_style(2), model: { series: { lr: curve(1) } } },
  ];

  it('draws a line per run that logged the series, its reference dashed in the same color', () => {
    const traces = comparison_traces('loss', { runs, hidden: new Set(), highlighted: null, options, reference_name: 'release' });
    expect(traces.map(t => [t.name, t.line.color, t.line.dash])).toEqual([
      ['lr=0.1 (release)', RUN_COLORS[0], 'dash'], ['lr=0.1', RUN_COLORS[0], 'solid'], ['lr=0.01', RUN_COLORS[1], 'solid'],
    ]);
  });

  it('leaves hidden runs out, and fades the others behind the highlighted run', () => {
    expect(comparison_traces('loss', { runs, hidden: new Set([1]), highlighted: null, options }).map(t => t.name)).toEqual(['lr=0.01']);
    const traces = comparison_traces('loss', { runs: [runs[0], runs[1]].map(r => ({ ...r, ref_model: null })), hidden: new Set(), highlighted: 1, options });
    expect(traces.map(t => [t.name, t.opacity, t.line.width])).toEqual([['lr=0.01', 0.12, 2], ['lr=0.1', 1, 3]]);
  });

  it('counts the runs that logged each series', () => {
    expect(series_counts(runs.map(r => r.model))).toEqual({ loss: 2, lr: 1 });
  });
});

describe('SeriesComparison', () => {
  const files = {
    '/s/run-1/series.json': summary({ 'train/loss': curve(3, 2, 1), lr: curve(0.1) }),
    '/s/run-2/series.json': summary({ 'train/loss': curve(5, 4, 3) }),
    '/s/ref-1/series.json': summary({ 'train/loss': curve(6, 5, 4) }),
    // run 3 logged nothing: no file
  };
  beforeEach(() => {
    vi.stubGlobal('fetch', vi.fn(async url => {
      const path = new URL(String(url?.url ?? url), 'http://localhost').pathname;
      return path in files ? new Response(files[path]) : new Response('not found', { status: 404 });
    }));
  });
  afterEach(() => vi.unstubAllGlobals());

  const outputs = { 1: run(1, 0.1, { reference_id: 11 }), 2: run(2, 0.01), 3: run(3, 0.001), 4: { ...run(4, 1), data: {} } };
  const new_batch = { outputs, filtered: { outputs: [1, 2, 3, 4] } };
  const ref_batch = { outputs: { 11: { id: 11, output_dir_url: '/s/ref-1' } } };
  const render = props => renderWithProviders(<SeriesComparison new_batch={new_batch} ref_batch={ref_batch} labels={{ reference: 'release 1.4' }} {...props}/>);
  const plot = async title => (await screen.findAllByTestId('plot')).find(p => p.dataset.title.startsWith(title));
  const names = element => element.dataset.traces.split(',').filter(Boolean).map(trace => trace.split('|')[0]);

  it('plots the runs that logged series together, one color each', async () => {
    render();
    expect(names(await plot('train/loss'))).toEqual(['lr=0.1', 'lr=0.01']);
    expect((await plot('train/loss')).dataset.traces).toContain(`lr=0.01|${RUN_COLORS[1]}|solid|1|5 4 3`);
    // a series only some runs logged says so
    expect((await plot('lr')).dataset.title).toBe('lr (1 of 2 runs)');
    expect(screen.getByText('2 runs, 2 series')).toBeInTheDocument();
    // only runs started from code are asked for their series
    expect(fetch.mock.calls.map(([url]) => String(url?.url ?? url)).some(url => url.includes('run-4'))).toBe(false);
  });

  it('hides and shows runs from the legend', async () => {
    render();
    await plot('train/loss');
    fireEvent.click(screen.getByRole('button', { name: 'Hide lr=0.1' }));
    expect(names(await plot('train/loss'))).toEqual(['lr=0.01']);
    expect(screen.getByText('1 of 2 runs, 2 series')).toBeInTheDocument();
    fireEvent.click(screen.getByRole('button', { name: 'Show only lr=0.1' }));
    expect(names(await plot('train/loss'))).toEqual(['lr=0.1']);
    fireEvent.click(screen.getByRole('button', { name: 'Hide all' }));
    expect(names(await plot('train/loss'))).toEqual([]);
    fireEvent.click(screen.getByRole('button', { name: 'Show all' }));
    expect(names(await plot('train/loss'))).toEqual(['lr=0.1', 'lr=0.01']);
  });

  it('highlights the run under the pointer', async () => {
    render();
    await plot('train/loss');
    fireEvent.mouseEnter(screen.getByRole('button', { name: 'Hide lr=0.01' }).parentElement);
    expect((await plot('train/loss')).dataset.traces).toContain(`lr=0.1|${RUN_COLORS[0]}|solid|0.12|`);
  });

  it("adds the reference's runs on demand, dashed", async () => {
    render();
    await plot('train/loss');
    fireEvent.click(screen.getByLabelText("With release 1.4's runs, dashed"));
    await screen.findByTestId('series-comparison');
    await vi.waitFor(async () => expect(names(await plot('train/loss'))).toEqual(['lr=0.1 (release 1.4)', 'lr=0.1', 'lr=0.01']));
  });

  it('colors runs by a parameter, and shows a group alone', async () => {
    render();
    await plot('train/loss');
    fireEvent.change(screen.getByLabelText('Color the runs by'), { target: { value: 'lr' } });
    const legend = screen.getByRole('group', { name: 'Runs' });
    expect(within(legend).getByText('lr = 0.01')).toBeInTheDocument();
    fireEvent.click(within(legend).getByRole('button', { name: 'Show only lr = 0.01' }));
    expect(names(await plot('train/loss'))).toEqual(['lr=0.01']);
  });

  it('shares its options with the runs\' cards', async () => {
    const onControlsChange = vi.fn();
    render({ controls: { series: { x: 'time' } }, onControlsChange });
    await plot('train/loss');
    fireEvent.click(screen.getByLabelText('Log scale'));
    expect(onControlsChange).toHaveBeenLastCalledWith({ series: { x: 'time', log: true } });
  });

  it('shows nothing for batches without runs that logged series', async () => {
    const { container } = render({ new_batch: { outputs: { 3: outputs[3], 4: outputs[4] }, filtered: { outputs: [3, 4] } } });
    await vi.waitFor(() => expect(container.querySelector('[aria-label="Loading the runs\' series"]')).toBeNull());
    expect(screen.queryByTestId('series-comparison')).toBeNull();
  });
});
