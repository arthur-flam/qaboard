// Announcements: the banners admins show everyone, e.g. a maintenance (Admin → Messages, backend/api/messages.py).
// Public: signed-out visitors and the sign-in page get them too.
import { queryOptions } from "@tanstack/react-query";

import { http } from "../api/http";


// The ones to show now, most severe first
export const announcementsQuery = queryOptions({
  queryKey: ['announcements'],
  // `background`: nobody asked for it, a 401 must not ask users to sign in
  queryFn: async ({ signal }) => {
    const { data } = await http.get('/api/v1/announcements', { signal, background: true });
    // e.g. a proxy's HTML page during an outage
    return Array.isArray(data?.announcements) ? data.announcements : [];
  },
  // new ones show up within minutes on pages people keep open, and when they come back to the tab
  refetchInterval: 5 * 60 * 1000,
  refetchOnWindowFocus: true,
  staleTime: 60 * 1000,
  retry: false,
});


// Dismissals are per browser, per version: "<id>:<revision>". An edited announcement shows again.
export const DISMISSED_KEY = 'qaboard.announcements.dismissed';

const dismissal = announcement => `${announcement.id}:${announcement.revision ?? 0}`;

// localStorage can be unavailable (private mode, blocked storage...)
export const getDismissed = () => {
  try {
    const value = JSON.parse(window.localStorage.getItem(DISMISSED_KEY) ?? '[]');
    return Array.isArray(value) ? value.filter(item => typeof item === 'string') : [];
  } catch {
    return [];
  }
};

const setDismissed = dismissed => {
  try {
    if (dismissed.length) window.localStorage.setItem(DISMISSED_KEY, JSON.stringify(dismissed));
    else window.localStorage.removeItem(DISMISSED_KEY);
  } catch {}
};

export const isDismissed = (announcement, dismissed) => !!announcement.dismissible && dismissed.includes(dismissal(announcement));

// => the new list. Also forgets the announcements that ended: the list doesn't grow forever.
export const dismiss = (announcement, active = []) => {
  const others = getDismissed().filter(item => item !== dismissal(announcement));
  const dismissed = pruneDismissed([...others, dismissal(announcement)], [...active, announcement]);
  setDismissed(dismissed);
  return dismissed;
};

// Only the ids still shown: older revisions of an announcement go too
export const pruneDismissed = (dismissed, active) => {
  const current = new Set(active.map(dismissal));
  return dismissed.filter(item => current.has(item));
};

// When the server says what's active. Untouched when nothing changes.
export const forgetEnded = active => {
  const dismissed = getDismissed();
  const kept = pruneDismissed(dismissed, active);
  if (kept.length !== dismissed.length) setDismissed(kept);
  return kept;
};
