// The comparison header: a row for the new results, one for each reference, which say why the reference is there
import { act, fireEvent, render, screen, waitFor, within } from '@testing-library/react';
import { QueryClientProvider } from '@tanstack/react-query';
import { unstable_HistoryRouter as HistoryRouter, useLocation } from 'react-router';

import { ComparisonProvider, useComparison } from '../../hooks';
import { history, matchRoutes, RouteMatch } from '../../router';
import { routes } from '../../routes';
import { makeQueryClient } from '../../test-utils';
import { milestonesQuery, index_milestones } from '../../milestones/api';
import { ComparisonHeader } from '../ComparisonHeader';
import { commit_header_height } from '../layout';
import { usePrefsStore } from '../../stores/prefs';

const a = 'a'.repeat(40), b = 'b'.repeat(40), c = 'c'.repeat(40), d = 'd'.repeat(40), e = 'e'.repeat(40), f = 'f'.repeat(40);
const branches = { [a]: 'feature/denoise', [b]: 'master', [c]: 'release/1.0', [d]: 'feature/denoise', [e]: 'other', [f]: 'other' };
const commit = id => ({
  id, branch: branches[id], message: `Commit ${id.slice(0, 3)}`, committer_name: 'Ada', authored_datetime: '2026-09-01T10:00:00Z', data: {},
  batches: { default: { id: 1, label: 'default', valid_outputs: 3, failed_outputs: 0, outputs: {} }, nightly: { id: 2, label: 'nightly', valid_outputs: 9, outputs: {} } },
});

const Labels = () => {
  const { labels, references, selected, selected_views } = useComparison();
  return <>
    <span data-testid="labels">{labels.new} vs {labels.reference}</span>
    <span data-testid="names">{references.map(r => r.display_label).join(', ')}</span>
    <span data-testid="height">{commit_header_height({ selected_views, references })}</span>
    <span data-testid="ref_index">{selected.ref_index}</span>
  </>;
};
const Page = () => {
  const { pathname } = useLocation();
  const { match } = matchRoutes(routes, pathname);
  return <RouteMatch match={match}><ComparisonProvider><ComparisonHeader/><Labels/></ComparisonProvider></RouteMatch>;
};
const show = url => {
  act(() => history.replace(url));
  const queryClient = makeQueryClient();
  queryClient.setQueryData(milestonesQuery('p').queryKey, index_milestones([
    { id: 1, project: 'p', commit: c, batch: 'default', label: 'release 1.0', is_mine: true },
  ]));
  render(<QueryClientProvider client={queryClient}><HistoryRouter history={history}><Page/></HistoryRouter></QueryClientProvider>);
};
const query = () => new URLSearchParams(window.location.search);
const header = () => within(screen.getByRole('region', { name: 'What we compare' }));

beforeEach(() => {
  usePrefsStore.setState({ referencesTogether: true });
  vi.stubGlobal('fetch', vi.fn(async url => {
    const { pathname } = new URL(url, 'http://localhost');
    if (pathname.startsWith('/api/v1/project')) return new Response(JSON.stringify({ git: {}, qatools_config: { project: { reference_branch: 'master' } } }));
    const id = pathname.match(/^\/api\/v1\/commit\/(\w+)$/)?.[1];
    if (id) return new Response(JSON.stringify(commit(id)));
    // without an id: the latest commit of the reference branch
    if (pathname === '/api/v1/commit/') return new Response(JSON.stringify(commit(b)));
    return new Response('{}');
  }));
});
afterEach(() => vi.unstubAllGlobals());
// Blueprint's popovers don't clear their delays when they unmount
afterAll(() => new Promise(resolve => setTimeout(resolve, 1000)));

test('says why the reference is there, and calls it so: the latest commit of the reference branch', async () => {
  show(`/p/commit/${a}`);
  await waitFor(() => expect(screen.getByTestId('labels')).toHaveTextContent('new vs master'));
  expect(header().getByText('Latest on master')).toBeInTheDocument();
  // two rows
  expect(screen.getByTestId('height')).toHaveTextContent(String(8 + 2 * 36));
});

test('milestones, and commits from the same branch', async () => {
  show(`/p/commit/${a}?reference=${c}&compare=${d}`);
  await waitFor(() => expect(screen.getByTestId('names')).toHaveTextContent('release 1.0, ddddddd'));
  expect(header().getAllByText('Milestone').length).toBeGreaterThan(0);
  expect(header().getByText('Earlier on this branch')).toBeInTheDocument();
});

test('the same commit with another batch is called by its batch', async () => {
  show(`/p/commit/${a}?reference=${a}&batch_ref=nightly`);
  await waitFor(() => expect(screen.getByTestId('labels')).toHaveTextContent('new vs nightly'));
  expect(header().getByText(/Same commit as new/)).toBeInTheDocument();
});

test('users compare with another reference with its radio button, or "r", and it stays in its row', async () => {
  show(`/p/commit/${a}?reference=${c}&compare=${d}`);
  await waitFor(() => expect(screen.getByTestId('names')).toHaveTextContent('release 1.0, ddddddd'));
  fireEvent.click(header().getByRole('radio', { name: 'Compare with ddddddd' }));
  await waitFor(() => expect(query().get('reference')).toBe(d));
  expect(screen.getByTestId('ref_index')).toHaveTextContent('1');
  await waitFor(() => expect(screen.getByTestId('names')).toHaveTextContent('release 1.0, ddddddd'));
  expect(header().getByRole('radio', { name: 'Compare with release 1.0' })).toBeInTheDocument();

  fireEvent.keyDown(document.body, { key: 'r' });
  await waitFor(() => expect(query().get('reference')).toBe(c));
  expect(screen.getByTestId('ref_index')).toHaveTextContent('0');
});

test('the swap button swaps the new results and the reference compared', async () => {
  show(`/p/commit/${a}?reference=${e}`);
  await waitFor(() => expect(header().getByRole('button', { name: /^Swap new and / })).toBeInTheDocument());
  fireEvent.click(header().getByRole('button', { name: /^Swap new and / }));
  await waitFor(() => expect(window.location.pathname).toBe(`/p/commit/${e}`));
  expect(query().get('reference')).toBe(a);
});

test('past 3 references, the others are listed in a last row, and the one compared always has its row', async () => {
  show(`/p/commit/${a}?reference=${b}&compare=${c}&compare=${d}&compare=${e}&compare=${f}`);
  await waitFor(() => expect(header().getByText('2 more references: click one to compare with it')).toBeInTheDocument());
  expect(screen.getByTestId('height')).toHaveTextContent(String(8 + 4 * 36 + 30));
  fireEvent.click(await header().findByRole('button', { name: /^Compare with fffffff/ }));
  await waitFor(() => expect(query().get('reference')).toBe(f));
  await waitFor(() => expect(header().getByRole('radio', { name: /^Comparing with fffffff/ })).toBeInTheDocument());
  expect(header().getByText('2 more references: click one to compare with it')).toBeInTheDocument();
});

test('users hide references from the image viewers, and remove them', async () => {
  show(`/p/commit/${a}?reference=${b}&compare=${e}`);
  await waitFor(() => expect(header().getByRole('button', { name: /^Hide eeeeeee/ })).toBeInTheDocument());
  fireEvent.click(header().getByRole('button', { name: /^Hide eeeeeee/ }));
  await waitFor(() => expect(header().getByRole('button', { name: /^Show eeeeeee/ })).toBeInTheDocument());
  fireEvent.click(header().getByRole('button', { name: 'Actions on eeeeeee' }));
  fireEvent.click(await screen.findByText('Remove'));
  await waitFor(() => expect(query().getAll('compare')).toEqual([]));
});
