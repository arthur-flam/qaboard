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
import { toaster } from '../../toaster';

const a = 'a'.repeat(40), b = 'b'.repeat(40), c = 'c'.repeat(40), d = 'd'.repeat(40), e = 'e'.repeat(40), f = 'f'.repeat(40);
const branches = { [a]: 'feature/denoise', [b]: 'master', [c]: 'release/1.0', [d]: 'feature/denoise', [e]: 'other', [f]: 'other' };
// a, b, c... were committed one day after the other
const commit = id => ({
  id, branch: branches[id], message: `Commit ${id.slice(0, 3)}`, committer_name: 'Ada', authored_datetime: `2026-09-0${'abcdef'.indexOf(id[0]) + 1}T10:00:00Z`, data: {},
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
// the commits of the references not shown, in order, whatever how the link writes them
const compared_commits = () => {
  const params = [...query().entries()];
  const simple = params.filter(([key]) => key === 'compare').map(([, value]) => value);
  const indexed = params.filter(([key]) => /^compare\[\d+\]\[commit\]$/.test(key)).map(([, value]) => value);
  return simple.length ? simple : indexed;
};
const header = () => within(screen.getByRole('region', { name: 'What we compare' }));

beforeEach(() => {
  usePrefsStore.setState({ referencesTogether: true });
  vi.spyOn(toaster, 'show').mockImplementation(() => {});
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
afterEach(() => {
  vi.unstubAllGlobals();
  vi.restoreAllMocks();
});
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
  show(`/p/commit/${d}?reference=${c}&compare=${a}`);
  await waitFor(() => expect(screen.getByTestId('names')).toHaveTextContent('release 1.0, aaaaaaa'));
  // a milestone says its notes, else its commit's message
  expect(await header().findByText('Commit ccc')).toBeInTheDocument();
  expect(await header().findByText('Earlier on this branch')).toBeInTheDocument();
  // every row has its commit's short id
  expect(header().getByRole('button', { name: 'Copy the commit id aaaaaaa' })).toBeInTheDocument();
});

test('a newer commit of the same branch is later, not earlier', async () => {
  show(`/p/commit/${a}?reference=${d}`);
  await waitFor(() => expect(header().getByText('Later on this branch')).toBeInTheDocument());
});

test('names given by default ("ref 2") are not names: rows say why the reference is there', async () => {
  show(`/p/commit/${a}?reference=${b}&compare=${d}&label_ref=ref`);
  await waitFor(() => expect(screen.getByTestId('names')).toHaveTextContent('master, ddddddd'));
  expect(header().queryByText('ref 2')).not.toBeInTheDocument();
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

test('past 4 references, they scroll, the footer says how many are out of sight, and all can show', async () => {
  show(`/p/commit/${a}?reference=${b}&compare=${c}&compare=${d}&compare=${e}&compare=${f}`);
  await waitFor(() => expect(header().getByText('5 references')).toBeInTheDocument());
  // 3 rows and a half, and the footer
  expect(screen.getByTestId('height')).toHaveTextContent(String(8 + 36 + 3.5 * 36 + 26));
  expect(header().getByRole('radiogroup', { name: 'The references' })).toBeInTheDocument();
  fireEvent.click(await header().findByRole('radio', { name: /^Compare with fffffff/ }));
  await waitFor(() => expect(query().get('reference')).toBe(f));
  fireEvent.click(header().getByRole('button', { name: 'Show all' }));
  expect(await header().findByRole('button', { name: 'Show fewer' })).toBeInTheDocument();
  fireEvent.click(header().getByRole('button', { name: 'Show fewer' }));
});

test('users hide references from the image viewers, and remove them', async () => {
  show(`/p/commit/${a}?reference=${b}&compare=${e}`);
  await waitFor(() => expect(header().getByRole('button', { name: /^Hide eeeeeee/ })).toBeInTheDocument());
  fireEvent.click(header().getByRole('button', { name: /^Hide eeeeeee/ }));
  await waitFor(() => expect(header().getByRole('button', { name: /^Show eeeeeee/ })).toBeInTheDocument());
  fireEvent.click(header().getByRole('button', { name: 'Actions on eeeeeee' }));
  fireEvent.click(await screen.findByText('Remove'));
  await waitFor(() => expect(query().getAll('compare')).toEqual([]));
  // it can be undone
  await waitFor(() => expect(toaster.show).toHaveBeenCalledWith(expect.objectContaining({ message: 'Removed eeeeeee from the comparison' }), 'remove-reference'));
  act(() => toaster.show.mock.calls.find(([, key]) => key === 'remove-reference')[0].action.onClick());
  await waitFor(() => expect(compared_commits()).toEqual([e]));
});

test('on a row, Delete removes the reference, Alt+arrows move it', async () => {
  show(`/p/commit/${a}?reference=${b}&compare=${e}&compare=${f}`);
  await waitFor(() => expect(header().getByRole('button', { name: 'Actions on fffffff' })).toBeInTheDocument());
  const row = header().getByRole('button', { name: 'Actions on fffffff' }).closest('.qa-row');
  fireEvent.keyDown(row, { key: 'ArrowUp', altKey: true });
  await waitFor(() => expect(compared_commits()).toEqual([f, e]));
  fireEvent.keyDown(header().getByRole('button', { name: 'Actions on eeeeeee' }).closest('.qa-row'), { key: 'Delete' });
  await waitFor(() => expect(compared_commits()).toEqual([f]));
});

test('with a filter, the row says how many runs match', async () => {
  show(`/p/commit/${a}?reference=${b}&filter=nothing-matches`);
  await waitFor(() => expect(header().getByTitle('0 of the 0 runs match the filter')).toBeInTheDocument());
});
