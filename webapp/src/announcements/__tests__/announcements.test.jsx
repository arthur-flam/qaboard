/**
 * The banners admins show on every page: dismissals are per browser and per version, a failed fetch shows nothing.
 * Run with: cd webapp && npm test -- announcements
 */
import { screen, fireEvent, waitFor } from '@testing-library/react';
import { DateTime } from 'luxon';

import { Announcements, eventWindow } from '../Announcements';
import { DISMISSED_KEY } from '../api';
import { renderWithProviders } from '../../test-utils';

const json = (data, status = 200) => Promise.resolve(new Response(JSON.stringify(data), { status, headers: { 'Content-Type': 'application/json' } }));
const mockAnnouncements = (announcements, status = 200) => vi.spyOn(globalThis, 'fetch').mockImplementation(url => url.startsWith('/api/v1/announcements')
  ? json(status === 200 ? { announcements } : { error: 'Oops' }, status)
  : json({}));
const fetched = () => fetch.mock.calls.filter(([url]) => url.startsWith('/api/v1/announcements')).length;

const announcement = props => ({
  id: 1, title: 'Scheduled maintenance', message: 'QA-Board will be unavailable while we upgrade it.', intent: 'warning',
  link_url: null, link_label: null, starts_at: '2026-11-01T00:00:00Z', ends_at: null, dismissible: true,
  event_starts_at: null, event_ends_at: null, revision: 0, calendar_url: null, ...props,
});

const stored = () => JSON.parse(localStorage.getItem(DISMISSED_KEY) ?? '[]');

beforeEach(() => localStorage.clear());
afterEach(() => {
  vi.restoreAllMocks();
  vi.useRealTimers();
});


describe('Announcements', () => {
  it('shows the active ones, with their link', async () => {
    mockAnnouncements([
      announcement({ id: 1, intent: 'danger', title: 'Outage', message: 'Images are slow.', link_url: 'https://status.example.com', link_label: 'Status page' }),
      announcement({ id: 2, intent: 'primary', title: 'New: tips emails' }),
    ]);
    renderWithProviders(<Announcements/>);
    expect(await screen.findByRole('status', { name: 'Outage' })).toHaveTextContent('Outage Images are slow. Status page');
    expect(screen.getByRole('status', { name: 'New: tips emails' })).toBeInTheDocument();
    expect(screen.getByRole('link', { name: 'Status page' })).toHaveAttribute('href', 'https://status.example.com');
  });

  it('hides a dismissed one, also on the next pages', async () => {
    mockAnnouncements([announcement()]);
    const { unmount } = renderWithProviders(<Announcements/>);
    fireEvent.click(await screen.findByRole('button', { name: 'Dismiss “Scheduled maintenance”' }));
    expect(screen.queryByRole('region', { name: 'Announcements' })).not.toBeInTheDocument();
    expect(stored()).toEqual(['1:0']);
    unmount();

    renderWithProviders(<Announcements/>);
    await waitFor(() => expect(fetched()).toBe(2));
    expect(screen.queryByRole('status')).not.toBeInTheDocument();
  });

  it("can't be dismissed when admins say so", async () => {
    mockAnnouncements([announcement({ dismissible: false })]);
    renderWithProviders(<Announcements/>);
    expect(await screen.findByRole('status', { name: 'Scheduled maintenance' })).toBeInTheDocument();
    expect(screen.queryByRole('button', { name: /Dismiss/ })).not.toBeInTheDocument();
  });

  it('shows again once edited, and forgets what ended', async () => {
    localStorage.setItem(DISMISSED_KEY, JSON.stringify(['1:0', '9:3']));
    mockAnnouncements([announcement({ revision: 1 })]);
    renderWithProviders(<Announcements/>);
    expect(await screen.findByRole('status', { name: 'Scheduled maintenance' })).toBeInTheDocument();
    await waitFor(() => expect(stored()).toEqual([]));
  });

  it('keeps what is still dismissed', async () => {
    localStorage.setItem(DISMISSED_KEY, JSON.stringify(['1:2', '9:3']));
    mockAnnouncements([announcement({ revision: 2 })]);
    renderWithProviders(<Announcements/>);
    await waitFor(() => expect(stored()).toEqual(['1:2']));
    expect(screen.queryByRole('status')).not.toBeInTheDocument();
  });

  it("says when the event is, in the viewer's time zone, with a calendar file", async () => {
    vi.useFakeTimers({ toFake: ['Date'] });
    vi.setSystemTime(new Date('2026-11-20T12:00:00Z'));
    mockAnnouncements([announcement({ event_starts_at: '2026-11-22T10:00:00Z', event_ends_at: '2026-11-22T12:00:00Z', calendar_url: '/api/v1/announcements/1/calendar.ics' })]);
    renderWithProviders(<Announcements/>);
    const start = DateTime.fromISO('2026-11-22T10:00:00Z').toLocal();
    const end = DateTime.fromISO('2026-11-22T12:00:00Z').toLocal();
    const banner = await screen.findByRole('status', { name: 'Scheduled maintenance' });
    expect(banner).toHaveTextContent(`${start.toFormat('ccc d LLL')}, ${start.toFormat('HH:mm')}–${end.toFormat('HH:mm')} · in 2 days`);
    const calendar = screen.getByRole('link', { name: 'Add to calendar' });
    expect(calendar).toHaveAttribute('href', '/api/v1/announcements/1/calendar.ics');
    expect(calendar).toHaveAttribute('download');
  });

  it("shows nothing when the server doesn't answer", async () => {
    mockAnnouncements([], 502);
    renderWithProviders(<Announcements/>);
    await waitFor(() => expect(fetched()).toBe(1));
    expect(screen.queryByRole('region', { name: 'Announcements' })).not.toBeInTheDocument();
    expect(screen.queryByRole('alert')).not.toBeInTheDocument();
  });
});


describe('eventWindow', () => {
  const now = DateTime.fromISO('2026-11-20T12:00:00Z');
  it('says how soon', () => {
    expect(eventWindow('2026-11-20T15:00:00Z', '2026-11-20T16:00:00Z', now)).toMatch(/ · in 3 hours$/);
    expect(eventWindow('2026-11-20T11:00:00Z', '2026-11-20T13:00:00Z', now)).toMatch(/ · happening now$/);
    expect(eventWindow('2026-11-20T09:00:00Z', '2026-11-20T10:00:00Z', now)).toMatch(/ · ended$/);
  });

  it('gives both days of events over several days, and the year of other years', () => {
    const text = eventWindow('2026-11-21T12:00:00Z', '2026-11-24T12:00:00Z', now);
    expect(text).toContain(' – ');
    expect(eventWindow('2027-01-21T12:00:00Z', null, now)).toContain('2027');
  });
});
