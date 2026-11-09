/**
 * Admin → Messages: announcements, recipients, tips emails. Admin → Users copies emails too.
 * Run with: cd webapp && npm test -- messages
 */
import { act, screen, fireEvent, waitFor, within } from '@testing-library/react';
import { unstable_HistoryRouter as HistoryRouter } from 'react-router';
import { DateTime } from 'luxon';

import { history } from '../../router';
import { Messages } from '../Messages';
import { Users } from '../Users';
import { recipientsCsv } from '../messages/Recipients';
import { renderWithProviders } from '../../test-utils';
import { toaster } from '../../toaster';
import { copyWithToast } from '../../utils/clipboard';

vi.mock('../../utils/clipboard', () => ({ copyWithToast: vi.fn(() => Promise.resolve(true)) }));

const json = (data, status = 200) => Promise.resolve(new Response(JSON.stringify(data), { status, headers: { 'Content-Type': 'application/json' } }));

// Answers by "METHOD /path"
const mockApi = routes => vi.spyOn(globalThis, 'fetch').mockImplementation((url, init = {}) => {
  const { pathname } = new URL(url, 'http://localhost');
  const answer = routes[`${init.method ?? 'GET'} ${pathname}`];
  if (!answer) return json({});
  const [data, status] = typeof answer === 'function' ? answer(url, init) : [answer, 200];
  return json(data, status);
});
const calls = (fetch, method, path) => fetch.mock.calls.filter(([url, init]) => (init?.method ?? 'GET') === method && url.startsWith(path));
const body = call => JSON.parse(call[1].body);

const renderAt = (pathname, ui) => {
  act(() => history.replace(pathname));
  return renderWithProviders(<HistoryRouter history={history}>{ui}</HistoryRouter>);
};

const announcement = props => ({
  id: 7, title: 'Scheduled maintenance', message: 'QA-Board will be unavailable while we upgrade it.', intent: 'warning',
  link_url: null, link_label: null, starts_at: '2026-11-09T08:00:00Z', ends_at: '2026-11-10T20:00:00Z', dismissible: true,
  event_starts_at: '2026-11-10T18:00:00Z', event_ends_at: '2026-11-10T20:00:00Z', revision: 0,
  calendar_url: '/api/v1/announcements/7/calendar.ics', created_at: '2026-11-09T08:00:00Z', updated_at: null, created_by_id: 1, ...props,
});

const recipients = {
  recipients: [
    { user_name: 'alice', full_name: 'Alice Martin', email: 'alice@example.com', address: 'Alice Martin <alice@example.com>' },
    { user_name: 'bob', full_name: null, email: 'bob@example.com', address: 'bob@example.com' },
  ],
  without_email: ['svc-legacy'],
  to: 'Alice Martin <alice@example.com>; bob@example.com',
  active_days: 30,
  project: null,
};

const tips = {
  enabled: true, every_days: 7, inactive_days: 30, folder: null, email_enabled: false, errors: [],
  tips: [
    { key: 'welcome', order: 0, position: 1, subject: 'Welcome to QA-Board', preheader: 'Where to start', cta_label: 'Open', cta_url: '/', new_users_only: true, source: 'built-in', file: 'welcome.md', sent: 12, failed: 1, skipped: 3 },
    { key: 'compare', order: 10, position: 2, subject: 'Compare with a milestone', preheader: '', cta_label: null, cta_url: null, new_users_only: false, source: 'site', file: '/etc/qaboard/tips/compare.md', sent: 8, failed: 0, skipped: 0 },
  ],
  people: { enrolled: 20, due: 2, waiting: 10, paused: 3, finished: 4, opted_out: 1, no_email: 0, not_enrolled_active: 15 },
};

const routes = (extra = {}) => ({
  'GET /api/v1/admin/announcements': { announcements: [announcement()], past: [announcement({ id: 3, title: 'Old outage', intent: 'danger', ends_at: '2026-10-01T00:00:00Z', calendar_url: null, event_starts_at: null, event_ends_at: null })] },
  'GET /api/v1/admin/recipients': recipients,
  'GET /api/v1/admin/tips': tips,
  ...extra,
});

beforeEach(() => vi.clearAllMocks());
afterEach(() => {
  vi.restoreAllMocks();
  vi.useRealTimers();
});


describe('Messages', () => {
  it('shows the announcements, the recipients and the tips', async () => {
    mockApi(routes());
    renderAt('/admin/messages', <Messages/>);

    const table = await screen.findByRole('table', { name: 'Announcements' });
    expect(within(table).getByText('Scheduled maintenance')).toBeInTheDocument();
    expect(within(table).getByText('Warning')).toBeInTheDocument();
    expect(within(table).getByRole('button', { name: 'Calendar file' })).toHaveAttribute('href', '/api/v1/announcements/7/calendar.ics');
    // the past ones are collapsed
    expect(screen.getByRole('button', { name: 'Show the past announcements (1)' })).toHaveAttribute('aria-expanded', 'false');

    expect(await screen.findByText('2 people')).toBeInTheDocument();
    expect(screen.getByText(/Alice Martin, bob/)).toBeInTheDocument();
    expect(screen.getByText('1 more person')).toBeInTheDocument();

    const tips_table = await screen.findByRole('table', { name: 'Tips' });
    expect(within(tips_table).getByText('Welcome to QA-Board')).toBeInTheDocument();
    expect(within(tips_table).getByText('welcome', { selector: '.bp6-tag *, .bp6-tag' })).toBeInTheDocument();
    expect(within(tips_table).getByText('site')).toBeInTheDocument();
    expect(screen.getByText('On')).toBeInTheDocument();
    // no SMTP server
    expect(screen.getByRole('link', { name: /set an SMTP server/ })).toHaveAttribute('href', '/admin/settings?group=email');
    expect(screen.getByText(/active in the last 30 days don't get the tips/)).toHaveTextContent('15 people active in the last 30 days');
  });

  it('creates an announcement', async () => {
    vi.useFakeTimers({ toFake: ['Date'] });
    vi.setSystemTime(new Date('2026-11-09T10:15:30Z'));
    const fetch = mockApi(routes({ 'POST /api/v1/admin/announcements': () => [announcement({ id: 8 }), 201] }));
    renderAt('/admin/messages', <Messages/>);
    fireEvent.click(await screen.findByRole('button', { name: 'New announcement' }));
    const dialog = await screen.findByRole('dialog');
    fireEvent.change(within(dialog).getByRole('textbox', { name: /^Title/ }), { target: { value: 'New: tips emails' } });
    fireEvent.change(within(dialog).getByRole('textbox', { name: /^Message/ }), { target: { value: 'One tip a week.' } });
    fireEvent.change(within(dialog).getByLabelText('Kind'), { target: { value: 'success' } });
    fireEvent.change(within(dialog).getByRole('textbox', { name: /^Link \(optional\)/ }), { target: { value: '/docs/user-guide/overview' } });
    // the preview follows the form
    expect(within(dialog).getByRole('status', { name: 'New: tips emails' })).toHaveTextContent('New: tips emails One tip a week. Learn more');
    fireEvent.click(within(dialog).getByRole('button', { name: 'Publish' }));
    await waitFor(() => expect(calls(fetch, 'POST', '/api/v1/admin/announcements')).toHaveLength(1));
    expect(body(calls(fetch, 'POST', '/api/v1/admin/announcements')[0])).toEqual({
      title: 'New: tips emails', message: 'One tip a week.', intent: 'success', link_url: '/docs/user-guide/overview', link_label: null,
      starts_at: '2026-11-09T10:15:00Z', ends_at: null, dismissible: true, event_starts_at: null, event_ends_at: null,
    });
    await waitFor(() => expect(screen.queryByRole('dialog')).not.toBeInTheDocument());
  });

  it("pre-fills a scheduled maintenance: tomorrow 18:00–20:00 in the admin's time zone", async () => {
    vi.useFakeTimers({ toFake: ['Date'] });
    vi.setSystemTime(new Date('2026-11-09T10:15:30Z'));
    const fetch = mockApi(routes({ 'POST /api/v1/admin/announcements': () => [announcement(), 201] }));
    renderAt('/admin/messages', <Messages/>);
    fireEvent.click(await screen.findByRole('button', { name: 'Scheduled maintenance' }));
    const dialog = await screen.findByRole('dialog');
    expect(within(dialog).getByRole('textbox', { name: /^Title/ })).toHaveValue('Scheduled maintenance');
    fireEvent.click(within(dialog).getByRole('button', { name: 'Publish' }));
    await waitFor(() => expect(calls(fetch, 'POST', '/api/v1/admin/announcements')).toHaveLength(1));
    const start = DateTime.now().plus({ days: 1 }).set({ hour: 18, minute: 0, second: 0, millisecond: 0 });
    const utc = date => date.toUTC().toISO({ suppressMilliseconds: true });
    expect(body(calls(fetch, 'POST', '/api/v1/admin/announcements')[0])).toMatchObject({
      title: 'Scheduled maintenance', intent: 'warning', message: 'QA-Board will be unavailable while we upgrade it.',
      starts_at: '2026-11-09T10:15:00Z', ends_at: utc(start.plus({ hours: 2 })),
      event_starts_at: utc(start), event_ends_at: utc(start.plus({ hours: 2 })),
    });
  });

  it("says why the server refused", async () => {
    mockApi(routes({ 'POST /api/v1/admin/announcements': () => [{ error: 'The banner must end after it starts' }, 400] }));
    renderAt('/admin/messages', <Messages/>);
    fireEvent.click(await screen.findByRole('button', { name: 'New announcement' }));
    const dialog = await screen.findByRole('dialog');
    fireEvent.change(within(dialog).getByRole('textbox', { name: /^Title/ }), { target: { value: 'Hello' } });
    fireEvent.click(within(dialog).getByRole('button', { name: 'Publish' }));
    expect(await within(dialog).findByRole('alert')).toHaveTextContent('The banner must end after it starts');
  });

  it('sends only what changed: the others keep it dismissed if nothing did', async () => {
    const fetch = mockApi(routes({ 'PUT /api/v1/admin/announcements/7': announcement({ revision: 1 }) }));
    renderAt('/admin/messages', <Messages/>);
    const table = await screen.findByRole('table', { name: 'Announcements' });
    fireEvent.click(within(table).getByRole('button', { name: 'Edit' }));
    let dialog = await screen.findByRole('dialog');
    fireEvent.click(within(dialog).getByRole('button', { name: 'Save' }));
    await waitFor(() => expect(screen.queryByRole('dialog')).not.toBeInTheDocument());
    expect(calls(fetch, 'PUT', '/api/v1/admin/announcements/7')).toHaveLength(0);

    fireEvent.click(within(table).getByRole('button', { name: 'Edit' }));
    dialog = await screen.findByRole('dialog');
    fireEvent.change(within(dialog).getByRole('textbox', { name: /^Title/ }), { target: { value: 'Maintenance, moved' } });
    fireEvent.click(within(dialog).getByRole('button', { name: 'Save' }));
    await waitFor(() => expect(calls(fetch, 'PUT', '/api/v1/admin/announcements/7')).toHaveLength(1));
    expect(body(calls(fetch, 'PUT', '/api/v1/admin/announcements/7')[0])).toEqual({ title: 'Maintenance, moved' });
  });

  it('deletes after a confirmation', async () => {
    const fetch = mockApi(routes({ 'DELETE /api/v1/admin/announcements/7': { deleted: true } }));
    renderAt('/admin/messages', <Messages/>);
    const table = await screen.findByRole('table', { name: 'Announcements' });
    fireEvent.click(within(table).getByRole('button', { name: 'Delete “Scheduled maintenance”' }));
    fireEvent.click(await screen.findByRole('button', { name: 'Delete' }));
    await waitFor(() => expect(calls(fetch, 'DELETE', '/api/v1/admin/announcements/7')).toHaveLength(1));
  });

  it('copies the recipients for Outlook, and filters them', async () => {
    const fetch = mockApi(routes());
    renderAt('/admin/messages', <Messages/>);
    fireEvent.click(await screen.findByRole('button', { name: 'Copy for Outlook' }));
    expect(copyWithToast).toHaveBeenCalledWith('Alice Martin <alice@example.com>; bob@example.com', { what: '2 addresses' });
    expect(screen.getByRole('button', { name: 'Download CSV' }).getAttribute('href')).toContain(encodeURIComponent('Alice Martin,alice@example.com'));

    fireEvent.change(screen.getByLabelText('Active in the last'), { target: { value: '0' } });
    fireEvent.click(screen.getByLabelText('Admins only'));
    await waitFor(() => expect(calls(fetch, 'GET', '/api/v1/admin/recipients?active_days=0&admins=true')).toHaveLength(1));
  });

  it('previews a tip, sends a test, and enrolls people', async () => {
    const fetch = mockApi(routes({
      'POST /api/v1/admin/tips/compare/test': { sent_to: 'root@example.com' },
      'POST /api/v1/admin/tips/enroll': { enrolled: 15 },
    }));
    renderAt('/admin/messages', <Messages/>);
    const table = await screen.findByRole('table', { name: 'Tips' });
    const row = within(table).getByText('Compare with a milestone').closest('tr');

    fireEvent.click(within(row).getByRole('button', { name: 'Preview' }));
    const preview = await screen.findByTitle('Preview of Compare with a milestone');
    expect(preview).toHaveAttribute('src', '/api/v1/admin/tips/compare/preview?format=html');
    fireEvent.click(within(screen.getByRole('dialog')).getByRole('button', { name: 'Close' }));
    await waitFor(() => expect(screen.queryByRole('dialog')).not.toBeInTheDocument());

    fireEvent.click(within(row).getByRole('button', { name: 'Send me a test' }));
    await waitFor(() => expect(toaster.show).toHaveBeenCalledWith(expect.objectContaining({ message: 'Sent “Compare with a milestone” to root@example.com' })));

    fireEvent.click(screen.getByRole('button', { name: 'Enroll people already using QA-Board' }));
    fireEvent.click(await screen.findByRole('button', { name: 'Enroll them' }));
    await waitFor(() => expect(calls(fetch, 'POST', '/api/v1/admin/tips/enroll')).toHaveLength(1));
    expect(body(calls(fetch, 'POST', '/api/v1/admin/tips/enroll')[0])).toEqual({ active_days: 30 });
    await waitFor(() => expect(toaster.show).toHaveBeenCalledWith(expect.objectContaining({ message: expect.stringMatching(/^Enrolled 15 people/) })));
  });

  it('writes CSV that spreadsheets read', () => {
    expect(recipientsCsv([{ user_name: 'a', full_name: 'Martin, Alice', email: 'a@x.com' }, { user_name: 'bob', full_name: null, email: 'b@x.com' }]))
      .toBe('name,email\n"Martin, Alice",a@x.com\nbob,b@x.com\n');
  });
});


describe('Users: copy emails', () => {
  it('copies the addresses of the people active lately', async () => {
    const fetch = mockApi({ 'GET /api/v1/admin/users': { users: [], total: 0 }, 'GET /api/v1/admin/recipients': recipients });
    renderAt('/admin/users', <Users/>);
    fireEvent.click(await screen.findByRole('button', { name: /Copy emails/ }));
    fireEvent.click(await screen.findByRole('menuitem', { name: 'Active in the last 30 days' }));
    await waitFor(() => expect(copyWithToast).toHaveBeenCalledWith(recipients.to, expect.objectContaining({ message: expect.stringMatching(/^Copied 2 addresses/) })));
    expect(calls(fetch, 'GET', '/api/v1/admin/recipients?active_days=30')).toHaveLength(1);
  });
});
