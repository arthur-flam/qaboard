/**
 * The commit page opened with ?prefill=: the form fills once, says what the link filled, and nothing starts.
 * Run with: cd webapp && npm test -- LinkPrefill
 */
import { screen, fireEvent, act } from '@testing-library/react';
import { unstable_HistoryRouter as HistoryRouter } from 'react-router';

import { TuningForm } from '../forms';
import { usePrefillLink } from '../LinkPrefill';
import { prefill_link } from '../prefill_link';
import { history } from '../../../router';
import { renderWithProviders } from '../../../test-utils';
import { usePrefsStore } from '../../../stores/prefs';

vi.mock('../../MonacoEditor', () => ({
  default: ({ value, onChange, name }) => <textarea data-testid={name} value={value} onChange={e => onChange(e.target.value)}/>,
}));

const json = data => Promise.resolve(new Response(JSON.stringify(data), { status: 200, headers: { 'Content-Type': 'application/json' } }));
const config = { runners: { lsf: { user: 'runner' } }, inputs: {} };
const metrics = { default_metric: 'loss', main_metrics: ['loss'], available_metrics: { loss: { target: 1 } } };
const commit = { id: 'abcdef12', branch: 'master' };

const Page = () => {
  usePrefillLink('proj');
  return <TuningForm project="proj" config={config} metrics={metrics} commit={commit} available_tests_files={{}}/>;
};
const show = () => renderWithProviders(<HistoryRouter history={history}><Page/></HistoryRouter>, { user: { is_logged: true, user_name: 'alice' }, siteConfig: {} });

describe('links that fill Run Tests / Tuning', () => {
  let fetch;
  beforeEach(() => {
    usePrefsStore.setState({ tuning: { proj: { experiment_name: 'mine', selected_group: 'nightly', other: 'kept' } } });
    fetch = vi.spyOn(globalThis, 'fetch').mockImplementation(url => url.startsWith('/api/v1/tests/group?')
      ? json({ tests: [{ input_path: 'a.jpg', configurations: ['base'] }, { input_path: 'b.jpg', configurations: ['base'] }] })
      : json({}));
  });
  afterEach(() => vi.restoreAllMocks());

  it('fills the form once, says what it filled, and undoes it', async () => {
    const link = prefill_link('proj', 'abcdef12', { label: 'sweep', batches: ['ci'], parameter_search: { a: [1, 2, 3] }, platform: 'windows' });
    act(() => history.replace(`${link}&batch=default&filter=x`));
    show();

    const notice = await screen.findByTestId('tuning-link-prefill');
    expect(notice).toHaveTextContent('Filled in from a link');
    expect(notice).toHaveTextContent('Batch label: sweep');
    expect(notice).toHaveTextContent('Tests: ci');
    expect(notice).toHaveTextContent('Values to try: a: 3 combinations');
    expect(notice).toHaveTextContent('nothing runs until you click Start');
    expect(usePrefsStore.getState().tuning.proj).toMatchObject({ experiment_name: 'sweep', selected_group: 'ci', search_type: 'grid', other: 'kept' });
    expect(usePrefsStore.getState().tuning.proj.platform).toBeUndefined();
    expect(await screen.findByText('Start 6 runs')).toBeInTheDocument();
    // ?prefill= is gone, the rest stays, and the form shows
    expect(history.location.search).toBe('?selected_views=tuning&batch=default&filter=x');
    // nothing started
    expect(fetch.mock.calls.some(([url]) => url.startsWith('/api/v1/commit/abcdef12/batch?'))).toBe(false);

    fireEvent.click(screen.getByTestId('tuning-link-prefill-undo'));
    expect(usePrefsStore.getState().tuning.proj).toMatchObject({ experiment_name: 'mine', selected_group: 'nightly', other: 'kept', parameter_search: undefined });
    expect(screen.queryByTestId('tuning-link-prefill')).toBeNull();
  });

  it('says what it could not read, and leaves the form as it was', async () => {
    act(() => history.replace('/proj/commit/abcdef12?prefill=%7Bnot%20json'));
    show();
    const notice = await screen.findByTestId('tuning-link-prefill');
    expect(notice).toHaveTextContent('This link could not fill the form');
    expect(notice).toHaveTextContent("The link's values could not be read: they are not JSON.");
    expect(usePrefsStore.getState().tuning.proj).toEqual({ experiment_name: 'mine', selected_group: 'nightly', other: 'kept' });
    expect(history.location.search).toBe('?selected_views=tuning');
    fireEvent.click(screen.getByText('Dismiss'));
    expect(screen.queryByTestId('tuning-link-prefill')).toBeNull();
  });
});
