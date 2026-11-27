/**
 * Admin → Connected apps: registering the apps users can allow (MCP clients like Open WebUI), and revoking them.
 * Run with: cd webapp && npm test -- connectedApps
 */
import { screen, fireEvent, waitFor, within } from '@testing-library/react';

import { ConnectedApps, parseRedirectUris } from '../ConnectedApps';
import { renderWithProviders } from '../../test-utils';

const json = (data, status = 200) => Promise.resolve(new Response(JSON.stringify(data), { status, headers: { 'Content-Type': 'application/json' } }));
const admin = { is_logged: true, user_id: 1, user_name: 'root', full_name: 'Root', is_admin: true };

const REDIRECT_URI = 'https://openwebui.example.com/oauth/clients/mcp:qaboard/callback';
// oauth.serialize_client()
const open_webui = {
  id: 1, client_id: 'qab_app_3kTq9WcZ', name: 'Open WebUI', redirect_uris: [REDIRECT_URI], secret_prefix: 'qab_sec_Xk3f',
  created_at: '2026-11-20T10:00:00Z', created_by: { id: 1, user_name: 'root' }, revoked_at: null, status: 'active', users: 3,
};
const revoked = { ...open_webui, id: 2, client_id: 'qab_app_old', name: 'Old app', status: 'revoked', users: 0 };

const mockApi = routes => vi.spyOn(globalThis, 'fetch').mockImplementation((url, init = {}) => {
  const { pathname } = new URL(url, 'http://localhost');
  const answer = routes[`${init.method ?? 'GET'} ${pathname}`];
  if (!answer) return json({});
  const [data, status] = Array.isArray(answer) ? answer : [answer, 200];
  return json(data, status);
});
const calls = (fetch, method, path) => fetch.mock.calls.filter(([url, init]) => (init?.method ?? 'GET') === method && new URL(url, 'http://localhost').pathname === path);

afterEach(() => vi.restoreAllMocks());


test('redirect URIs are read one per line', () => {
  expect(parseRedirectUris(` ${REDIRECT_URI}\n\nhttp://localhost:8080/cb ${REDIRECT_URI}`)).toEqual([REDIRECT_URI, 'http://localhost:8080/cb']);
});

describe('ConnectedApps', () => {
  it('lists the active apps, the revoked ones on demand', async () => {
    mockApi({ 'GET /api/v1/admin/oauth/clients': { clients: [open_webui, revoked] } });
    renderWithProviders(<ConnectedApps/>, { user: admin });
    const table = await screen.findByRole('table', { name: 'Registered apps' });
    const rows = within(table).getAllByRole('row');
    expect(rows).toHaveLength(2);
    expect(within(rows[1]).getByText('Open WebUI')).toBeInTheDocument();
    expect(within(rows[1]).getByText('qab_app_3kTq9WcZ')).toBeInTheDocument();
    expect(within(rows[1]).getByText(REDIRECT_URI)).toBeInTheDocument();
    expect(within(rows[1]).getByText('3')).toBeInTheDocument();
    fireEvent.click(screen.getByLabelText('Show revoked apps (1)'));
    expect(within(table).getByText('Old app')).toBeInTheDocument();
  });

  it('registers an app, and shows its secret once', async () => {
    const fetch = mockApi({
      'GET /api/v1/admin/oauth/clients': { clients: [] },
      'POST /api/v1/admin/oauth/clients': [{ ...open_webui, users: 0, client_secret: 'qab_sec_secret' }, 201],
    });
    renderWithProviders(<ConnectedApps/>, { user: admin });
    expect(await screen.findByText('No apps registered yet.')).toBeInTheDocument();
    fireEvent.click(screen.getByRole('button', { name: 'Register an app' }));
    const register = await screen.findByRole('button', { name: 'Register' });
    expect(register).toBeDisabled();
    fireEvent.change(screen.getByLabelText('Name'), { target: { value: ' Open WebUI ' } });
    fireEvent.change(screen.getByLabelText('Redirect URIs'), { target: { value: `${REDIRECT_URI}\n` } });
    fireEvent.click(register);
    expect(await screen.findByText('Copy the client secret now')).toBeInTheDocument();
    expect(screen.getByLabelText('Client ID')).toHaveValue('qab_app_3kTq9WcZ');
    expect(screen.getByLabelText('Client secret')).toHaveValue('qab_sec_secret');
    const [[, init]] = calls(fetch, 'POST', '/api/v1/admin/oauth/clients');
    expect(JSON.parse(init.body)).toEqual({ name: 'Open WebUI', redirect_uris: [REDIRECT_URI] });
  });

  it('shows why the server refuses an app', async () => {
    mockApi({
      'GET /api/v1/admin/oauth/clients': { clients: [] },
      'POST /api/v1/admin/oauth/clients': [{ error: "javascript:x isn't an http(s) URL." }, 400],
    });
    renderWithProviders(<ConnectedApps/>, { user: admin });
    fireEvent.click(await screen.findByRole('button', { name: 'Register an app' }));
    fireEvent.change(await screen.findByLabelText('Name'), { target: { value: 'x' } });
    fireEvent.change(screen.getByLabelText('Redirect URIs'), { target: { value: 'javascript:x' } });
    fireEvent.click(screen.getByRole('button', { name: 'Register' }));
    expect(await screen.findByRole('alert')).toHaveTextContent("javascript:x isn't an http(s) URL.");
  });

  it('changes the redirect URIs', async () => {
    const fetch = mockApi({ 'GET /api/v1/admin/oauth/clients': { clients: [open_webui] }, 'PATCH /api/v1/admin/oauth/clients/1': open_webui });
    renderWithProviders(<ConnectedApps/>, { user: admin });
    fireEvent.click(await screen.findByRole('button', { name: 'Change Open WebUI' }));
    expect(await screen.findByLabelText('Redirect URIs')).toHaveValue(REDIRECT_URI);
    fireEvent.change(screen.getByLabelText('Redirect URIs'), { target: { value: `${REDIRECT_URI}\nhttp://localhost:8080/cb` } });
    fireEvent.click(screen.getByRole('button', { name: 'Save' }));
    await waitFor(() => expect(calls(fetch, 'PATCH', '/api/v1/admin/oauth/clients/1')).toHaveLength(1));
    const [[, init]] = calls(fetch, 'PATCH', '/api/v1/admin/oauth/clients/1');
    expect(JSON.parse(init.body)).toEqual({ name: 'Open WebUI', redirect_uris: [REDIRECT_URI, 'http://localhost:8080/cb'] });
  });

  it('revokes an app, after saying what it stops', async () => {
    const fetch = mockApi({ 'GET /api/v1/admin/oauth/clients': { clients: [open_webui] }, 'DELETE /api/v1/admin/oauth/clients/1': { ...open_webui, status: 'revoked' } });
    renderWithProviders(<ConnectedApps/>, { user: admin });
    fireEvent.click(await screen.findByRole('button', { name: 'Revoke Open WebUI' }));
    expect(await screen.findByText(/the tokens of the 3 users who/)).toBeInTheDocument();
    fireEvent.click(screen.getByRole('button', { name: 'Revoke' }));
    await waitFor(() => expect(calls(fetch, 'DELETE', '/api/v1/admin/oauth/clients/1')).toHaveLength(1));
  });
});
