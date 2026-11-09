// The banners admins show everyone (Admin → Messages): at the top of every page, below the bar.
// Quiet on purpose: compact, one per announcement, and nothing at all when the server doesn't answer.
import { useEffect, useState } from "react";
import { useQuery } from "@tanstack/react-query";
import { Button, Callout, Classes, Intent } from "@blueprintjs/core";
import { DateTime } from "luxon";

import { useSiteConfig } from "../hooks";
import { parseDate } from "../settings/format";
import { resolveLink } from "../releaseNotes/logic";
import { announcementsQuery, dismiss, forgetEnded, getDismissed, isDismissed } from "./api";


const intents = {
  primary: Intent.PRIMARY,
  success: Intent.SUCCESS,
  warning: Intent.WARNING,
  danger: Intent.DANGER,
};

// a warning about an event is a maintenance
const iconOf = announcement => ({
  primary: 'info-sign',
  success: 'tick-circle',
  warning: announcement.event_starts_at ? 'wrench' : 'warning-sign',
  danger: 'error',
})[announcement.intent] ?? 'info-sign';

const day = (date, now) => date.toFormat(date.year === now.year ? 'ccc d LLL' : 'ccc d LLL yyyy');

// When the event happens, in the viewer's time zone: "Sun 22 Nov, 20:00–22:00 · in 2 days"
export const eventWindow = (starts_at, ends_at, now = DateTime.now()) => {
  const start = parseDate(starts_at);
  if (!start?.isValid) return null;
  const end = parseDate(ends_at);
  let when = `${day(start, now)}, ${start.toFormat('HH:mm')}`;
  if (end?.isValid)
    when += start.hasSame(end, 'day') ? `–${end.toFormat('HH:mm')}` : ` – ${day(end, now)}, ${end.toFormat('HH:mm')}`;
  // "in 3 hours" today, "tomorrow" or "in 2 days" further
  const relative = now < start
    ? (start.diff(now, 'hours').hours < 24 ? start.toRelative({ base: now }) : start.toRelativeCalendar({ base: now }))
    : (!end?.isValid || now < end) ? 'happening now' : 'ended';
  return `${when} · ${relative}`;
};

const external = url => /^(https?:)?\/\//.test(url);

// One banner. `preview`: in the admin's form, links don't navigate. `style`: e.g. rounded corners in the form.
export const AnnouncementBanner = ({ announcement, onDismiss, preview = false, style }) => {
  const { docs_root } = useSiteConfig();
  const stay = preview ? { onClick: event => event.preventDefault() } : {};
  const link = announcement.link_url ? resolveLink(announcement.link_url, docs_root) : null;
  const event = announcement.event_starts_at ? eventWindow(announcement.event_starts_at, announcement.event_ends_at) : null;
  const event_start = parseDate(announcement.event_starts_at);
  return <Callout
    compact
    intent={intents[announcement.intent] ?? Intent.PRIMARY}
    icon={iconOf(announcement)}
    role="status"
    aria-label={announcement.title}
    style={{ borderRadius: 0, ...style }}
  >
    <div style={{ display: 'flex', alignItems: 'flex-start', gap: 8 }}>
      <div style={{ flex: 1, minWidth: 0, overflowWrap: 'anywhere' }}>
        <strong>{announcement.title}</strong>
        {announcement.message && <span style={{ whiteSpace: 'pre-line' }}> {announcement.message}</span>}
        {link && <> <a href={link} {...(external(link) ? { target: '_blank', rel: 'noopener noreferrer' } : {})} {...stay}>{announcement.link_label || 'Learn more'}</a></>}
        {event && <div style={{ marginTop: 2 }}>
          <time dateTime={announcement.event_starts_at} title={event_start?.toLocaleString(DateTime.DATETIME_FULL)}>{event}</time>
          {announcement.calendar_url && <>
            <span className={Classes.TEXT_MUTED}> · </span>
            <a href={announcement.calendar_url} download {...stay}>Add to calendar</a>
          </>}
        </div>}
      </div>
      {announcement.dismissible && onDismiss && <Button
        variant="minimal"
        size="small"
        icon="cross"
        aria-label={`Dismiss “${announcement.title}”`}
        title="Dismiss"
        onClick={() => onDismiss(announcement)}
        style={{ margin: '-3px -4px -3px 0' }}
      />}
    </div>
  </Callout>;
};


// The banners to show now, without those dismissed in this browser. `style`: the wrapper's, when there's something to show.
export const Announcements = ({ style }) => {
  const { data } = useQuery(announcementsQuery);
  const [dismissed, setDismissed] = useState(getDismissed);
  // what ended can be forgotten
  useEffect(() => { if (data) forgetEnded(data); }, [data]);
  const shown = (data ?? []).filter(announcement => !isDismissed(announcement, dismissed));
  if (!shown.length) return null;
  return <div role="region" aria-label="Announcements" style={{ display: 'flex', flexDirection: 'column', gap: 1, ...style }}>
    {shown.map(announcement => <AnnouncementBanner
      key={announcement.id}
      announcement={announcement}
      onDismiss={announcement => setDismissed(dismiss(announcement, data))}
    />)}
  </div>;
};

export default Announcements;
