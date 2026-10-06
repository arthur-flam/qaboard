/**
 * Tests for the tables and summaries of metrics.
 * Run with: cd webapp && npm test -- tables
 */
import { screen, fireEvent } from '@testing-library/react';

import { TableCompare, TableKpi } from '../tables';
import { MetricsSummary } from '../metrics';
import { renderWithProviders } from '../../test-utils';

vi.mock('../Plot', () => ({ default: () => <div data-testid="plot"/> }));

const metric = (key, extra) => ({ key, label: key, short_label: key, scale: 1, suffix: '', smaller_is_better: true, ...extra });
const available_metrics = { loss: metric('loss'), status: metric('status'), time: metric('time', { suffix: 's' }) };

const output = (id, metrics, extra) => ({ id, test_input_path: `${id}.jpg`, configurations: [], extra_parameters: {}, metrics, ...extra });
const ref_batch = Object.freeze({ outputs: { r1: output('r1', { loss: 2, status: 'ok' }) }, filtered: { outputs: ['r1'] } });
const new_batch = Object.freeze({
  outputs: {
    a: output('a', { loss: 1, status: 'ok', time: 3 }, { reference_id: 'r1' }),
    b: output('b', { loss: 2, status: 'failed', time: 4 }, { reference_id: 'r1' }),
    c: output('c', { loss: 5 }, { is_pending: true }),
  },
  filtered: { outputs: ['a', 'b', 'c'] },
  used_metrics: new Set(['loss', 'status', 'time']),
  metrics_with_refs: new Set(['loss', 'status']),
  sorted_extra_parameters: [],
  data: {},
});


describe('TableCompare', () => {
  it('compares the metrics of outputs with their reference', () => {
    renderWithProviders(<TableCompare new_batch={new_batch} ref_batch={ref_batch} metrics={[]} available_metrics={available_metrics}/>);
    expect(screen.getByText('2 runs')).toBeInTheDocument();
    // only metrics with references
    expect(screen.queryByText('time')).not.toBeInTheDocument();
    const [row_a, row_b] = screen.getAllByRole('row').slice(2);
    expect(row_a).toHaveTextContent('-1 (-50%)');
    // equal values
    expect(row_a).toHaveTextContent('=');
    expect(row_b).toHaveTextContent('failed');
  });
});


describe('TableKpi', () => {
  it('shows the selected metrics', () => {
    renderWithProviders(<TableKpi new_batch={new_batch} ref_batch={ref_batch} metrics={['time', 'loss']} available_metrics={available_metrics}/>);
    const headers = screen.getAllByRole('columnheader').map(th => th.textContent);
    expect(headers.slice(1, 3)).toEqual(['time[s]', 'loss']);
    expect(screen.getAllByRole('row')).toHaveLength(4);
  });
});


describe('MetricsSummary', () => {
  it('shows the summary metrics, until users choose others', () => {
    const metrics = { available_metrics, summary_metrics: ['loss'] };
    const { rerender } = renderWithProviders(<MetricsSummary metrics={metrics} new_batch={new_batch} ref_batch={ref_batch}/>);
    expect(screen.getByRole('heading', { name: 'loss' })).toBeInTheDocument();
    expect(screen.queryByRole('heading', { name: 'time' })).not.toBeInTheDocument();

    fireEvent.click(screen.getByLabelText('Remove tag'));
    expect(screen.queryByRole('heading', { name: 'loss' })).not.toBeInTheDocument();
    // re-rendering with the same metrics keeps the user's choice
    rerender(<MetricsSummary metrics={{ ...metrics }} new_batch={new_batch} ref_batch={ref_batch}/>);
    expect(screen.queryByRole('heading', { name: 'loss' })).not.toBeInTheDocument();
    // new defaults replace it
    rerender(<MetricsSummary metrics={{ available_metrics, summary_metrics: ['time'] }} new_batch={new_batch} ref_batch={ref_batch}/>);
    expect(screen.getByRole('heading', { name: 'time' })).toBeInTheDocument();
  });
});
