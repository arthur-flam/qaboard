/**
 * /oauth/consent: users allow an app (an MCP client) to get read-only tokens for them, or deny it.
 * Run with: cd webapp && npm test -- ConsentPage
 */
import { act, screen, fireEvent, waitFor } from '@testing-library/react';
import { unstable_HistoryRouter as HistoryRouter } from 'react-router';

import { history } from '../../router';
import { ConsentPage } from '../ConsentPage';
import { goTo } from '../../login/api';
import { renderWithProviders } from '../../test-utils';

vi.mock('../../login/api', async importOriginal => ({ ...await importOriginal(), goTo: vi.fn() }));

const json = (data, status = 200) => Promise.resolve(new Response(JSON.stringify(data), { status, headers: { 'Content-Type': 'application/json' } }));

const alice = { is_logged: true, user_id: 1, user_name: 'alice', full_name: 'Alice Martin' };
// oauth.serialize_request()
const consent = {
  client: { id: 1, name: 'Open WebUI', client_id: 'qab_app_x' },
  redirect_uri: 'https://openwebui.example.com/oauth/clients/mcp:qaboard/callback',
  redirect_host: 'openwebui.example.com',
  scopes: [{ id: 'read', description: 'Read your projects and results' }],
  resource: 'https://qaboard.example.com/mcp',
};
const request = 'client_id=qab_app_x&redirect_uri=https%3A%2F%2Fopenwebui.example.com%2Fcb&state=xyz';

const mockApi = ({ details = consent, status = 200, answer = init => ({ redirect_to: `https://openwebui.example.com/cb?${JSON.parse(init.body).approve ? 'code=abc' : 'error=access_denied'}` }) } = {}) =>
  vi.spyOn(globalThis, 'fetch').mockImplementation((url, init = {}) => {
    if (url.startsWith('/api/v1/oauth/consent') && init.method === 'POST') return json(answer(init));
    if (url.startsWith('/api/v1/oauth/consent')) return json(details, status);
    return json({ is_authenticated: false });
  });

const renderAt = (pathname, user) => {
  act(() => history.replace(pathname));
  return renderWithProviders(<HistoryRouter history={history}><ConsentPage/></HistoryRouter>, { user, siteConfig: {} });
};

afterEach(() => {
  vi.restoreAllMocks();
  goTo.mockClear();
});


describe('ConsentPage', () => {
  it('shows the app, what it can do, where it goes back and as whom, then allows it', async () => {
    const fetch = mockApi();
    renderAt(`/oauth/consent?${request}`, alice);
    expect(await screen.findByText('Allow Open WebUI to use QA-Board as you?')).toBeInTheDocument();
    expect(fetch.mock.calls.map(([url]) => url)).toContain(`/api/v1/oauth/consent?${request}`);
    expect(screen.getByText('Read your projects and results')).toBeInTheDocument();
    expect(screen.getByText('openwebui.example.com')).toBeInTheDocument();
    expect(screen.getByText('Alice Martin', { selector: 'strong' })).toBeInTheDocument();
    expect(screen.getByText('https://qaboard.example.com/mcp')).toBeInTheDocument();
    fireEvent.click(screen.getByRole('button', { name: 'Allow' }));
    await waitFor(() => expect(goTo).toHaveBeenCalledWith('https://openwebui.example.com/cb?code=abc'));
    const [, init] = fetch.mock.calls.find(([, init]) => init?.method === 'POST');
    // the request as the app sent it: the server checks it again
    expect(JSON.parse(init.body)).toEqual({ request, approve: true });
  });

  it('denies', async () => {
    const fetch = mockApi();
    renderAt(`/oauth/consent?${request}`, alice);
    fireEvent.click(await screen.findByRole('button', { name: 'Deny' }));
    await waitFor(() => expect(goTo).toHaveBeenCalledWith('https://openwebui.example.com/cb?error=access_denied'));
    const [, init] = fetch.mock.calls.find(([, init]) => init?.method === 'POST');
    expect(JSON.parse(init.body)).toEqual({ request, approve: false });
  });

  it('says why an unknown app can\'t sign in, and never goes back to it', async () => {
    mockApi({ details: { error: "This app isn't registered in QA-Board.", reason: 'invalid_client' }, status: 400 });
    renderAt('/oauth/consent?client_id=nope', alice);
    expect(await screen.findByRole('alert')).toHaveTextContent("This app isn't registered in QA-Board.");
    expect(screen.queryByRole('button', { name: 'Back to the app' })).not.toBeInTheDocument();
  });

  it('offers to go back to the app with errors it should hear about', async () => {
    mockApi({ details: { error: 'PKCE is required.', reason: 'invalid_request', redirect_to: 'https://openwebui.example.com/cb?error=invalid_request' }, status: 400 });
    renderAt(`/oauth/consent?${request}`, alice);
    fireEvent.click(await screen.findByRole('button', { name: 'Back to the app' }));
    expect(goTo).toHaveBeenCalledWith('https://openwebui.example.com/cb?error=invalid_request');
  });

  it('shows errors of the answer, and lets users try again', async () => {
    mockApi({ answer: () => { throw new Error('Network down'); } });
    renderAt(`/oauth/consent?${request}`, alice);
    fireEvent.click(await screen.findByRole('button', { name: 'Allow' }));
    expect(await screen.findByRole('alert')).toBeInTheDocument();
    expect(screen.getByRole('button', { name: 'Allow' })).toBeEnabled();
    expect(goTo).not.toHaveBeenCalled();
  });
});
