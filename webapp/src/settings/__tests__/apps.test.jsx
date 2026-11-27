/**
 * Settings → Connected apps: the apps users allowed (MCP clients), and revoking them.
 * Run with: cd webapp && npm test -- apps.test
 */
import { screen, fireEvent, waitFor, within } from '@testing-library/react';

import { AppsTab } from '../AppsTab';
import { TokensTab } from '../TokensTab';
import { renderWithProviders } from '../../test-utils';

const json = (data, status = 200) => Promise.resolve(new Response(JSON.stringify(data), { status, headers: { 'Content-Type': 'application/json' } }));
const alice = { is_logged: true, user_id: 1, user_name: 'alice', full_name: 'Alice' };

// oauth.serialize_grant()
const open_webui = {
  id: 7, client: { id: 1, name: 'Open WebUI', client_id: 'qab_app_x' }, scopes: [{ id: 'read', description: 'Read your projects and results' }],
  resource: 'https://qaboard.example.com/mcp', created_at: '2026-11-20T10:00:00Z', last_used_at: '2026-11-27T09:00:00Z', status: 'active',
};

const mockApi = routes => vi.spyOn(globalThis, 'fetch').mockImplementation((url, init = {}) => {
  const { pathname } = new URL(url, 'http://localhost');
  const answer = routes[`${init.method ?? 'GET'} ${pathname}`];
  return answer ? json(answer) : json({});
});

afterEach(() => vi.restoreAllMocks());


describe('AppsTab', () => {
  it('lists the apps users allowed, and revokes them', async () => {
    const fetch = mockApi({ 'GET /api/v1/user/apps': { apps: [open_webui] }, 'DELETE /api/v1/user/apps/7': { ...open_webui, status: 'revoked' } });
    renderWithProviders(<AppsTab/>, { user: alice });
    const table = await screen.findByRole('table', { name: 'Connected apps' });
    const row = within(table).getAllByRole('row')[1];
    expect(within(row).getByText('Open WebUI')).toBeInTheDocument();
    expect(within(row).getByText('Read your projects and results')).toBeInTheDocument();
    expect(within(row).getByText('https://qaboard.example.com/mcp')).toBeInTheDocument();
    fireEvent.click(within(row).getByRole('button', { name: 'Revoke Open WebUI' }));
    fireEvent.click(await screen.findByRole('button', { name: 'Revoke' }));
    await waitFor(() => expect(fetch.mock.calls.some(([url, init]) => init?.method === 'DELETE' && url.endsWith('/api/v1/user/apps/7'))).toBe(true));
  });

  it('says when there are none', async () => {
    mockApi({ 'GET /api/v1/user/apps': { apps: [] } });
    renderWithProviders(<AppsTab/>, { user: alice });
    expect(await screen.findByText('No connected apps')).toBeInTheDocument();
  });
});

test('the apps\' tokens are not among the access tokens', async () => {
  const token = { id: 1, kind: 'personal', name: 'ci-bot', scopes: ['read'], status: 'active', created_at: '2026-11-01T00:00:00Z' };
  mockApi({ 'GET /api/v1/user/tokens': { tokens: [token, { ...token, id: 2, kind: 'oauth', name: 'Open WebUI' }] } });
  renderWithProviders(<TokensTab user={alice}/>, { user: alice });
  expect(await screen.findByText('ci-bot')).toBeInTheDocument();
  expect(screen.queryByText('Open WebUI')).not.toBeInTheDocument();
});
