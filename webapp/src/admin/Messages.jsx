// Admin → Messages: telling people things. Banners on every page (e.g. a maintenance), who to email, the tips emails.
import { useState } from "react";
import { useQuery, useQueryClient } from "@tanstack/react-query";
import { Alert, AnchorButton, Button, Classes, Collapse, HTMLTable, Intent, NonIdealState, Tag, Tooltip } from "@blueprintjs/core";
import { DateTime } from "luxon";

import { errorMessage } from "../api/http";
import { toaster } from "../toaster";
import { parseDate } from "../settings/format";
import { Loading } from "../settings/states";
import { eventWindow } from "../announcements/Announcements";
import { AdminError } from "./states";
import { adminAnnouncementsQuery, deleteAnnouncement } from "./api";
import { AnnouncementDialog, intent_options, maintenance_form } from "./messages/AnnouncementDialog";
import { Recipients } from "./messages/Recipients";
import { Tips } from "./messages/Tips";


const intent_labels = Object.fromEntries(intent_options.map(option => [option.value, option.label]));

// "Mon 9 Nov, 14:30", in the admin's time zone
const when = iso => {
  const date = parseDate(iso);
  if (!date?.isValid) return null;
  return <time dateTime={iso} title={date.toLocaleString(DateTime.DATETIME_FULL)}>
    {date.toFormat(date.year === DateTime.now().year ? 'ccc d LLL, HH:mm' : 'ccc d LLL yyyy, HH:mm')}
  </time>;
};

const Shown = ({ announcement, past }) => {
  const upcoming = !past && parseDate(announcement.starts_at) > DateTime.now();
  return <div style={{ whiteSpace: 'nowrap' }}>
    {!past && <Tag minimal round intent={upcoming ? undefined : Intent.SUCCESS} style={{ marginRight: 6 }}>{upcoming ? 'Upcoming' : 'Showing'}</Tag>}
    {when(announcement.starts_at)} – {announcement.ends_at ? when(announcement.ends_at) : <span className={Classes.TEXT_MUTED}>until deleted</span>}
  </div>;
};

const AnnouncementsTable = ({ announcements, past = false, onEdit, onDelete }) => <div style={{ overflowX: 'auto' }}>
  <HTMLTable compact style={{ width: '100%' }} aria-label={past ? 'Past announcements' : 'Announcements'}>
    <thead>
      <tr>
        <th>Title</th>
        <th>Kind</th>
        <th>Shown</th>
        <th>Event</th>
        <th>Dismissible</th>
        <th aria-label="Actions"/>
      </tr>
    </thead>
    <tbody>
      {announcements.map(announcement => <tr key={announcement.id}>
        <td style={{ minWidth: 200, maxWidth: 360 }}>
          <strong style={{ overflowWrap: 'anywhere' }}>{announcement.title}</strong>
          {announcement.message && <div className={Classes.TEXT_MUTED} style={{ fontSize: 12, overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }} title={announcement.message}>{announcement.message}</div>}
        </td>
        <td><Tag minimal intent={announcement.intent}>{intent_labels[announcement.intent] ?? announcement.intent}</Tag></td>
        <td><Shown announcement={announcement} past={past}/></td>
        <td style={{ whiteSpace: 'nowrap' }}>{announcement.event_starts_at ? eventWindow(announcement.event_starts_at, announcement.event_ends_at) : <span className={Classes.TEXT_MUTED}>—</span>}</td>
        <td>{announcement.dismissible ? 'Yes' : 'No'}</td>
        <td style={{ whiteSpace: 'nowrap', textAlign: 'right' }}>
          {announcement.calendar_url && <Tooltip content="The event as an .ics file: open it in Outlook to invite people" hoverOpenDelay={300}>
            <AnchorButton size="small" variant="minimal" icon="calendar" text="Calendar file" href={announcement.calendar_url} download/>
          </Tooltip>}
          <Button size="small" variant="minimal" icon="edit" text="Edit" onClick={() => onEdit(announcement)}/>
          <Button size="small" variant="minimal" icon="trash" intent={Intent.DANGER} aria-label={`Delete “${announcement.title}”`} title="Delete" onClick={() => onDelete(announcement)}/>
        </td>
      </tr>)}
    </tbody>
  </HTMLTable>
</div>;


// What to tell people about the maintenance, beyond the banner: an invite in their calendar
const InviteHowTo = () => <div style={{ marginTop: 16, maxWidth: 820 }}>
  <h5 className={Classes.HEADING}>Inviting people to the maintenance</h5>
  <ol style={{ margin: 0, paddingLeft: 20 }}>
    <li>Download the announcement's <strong>Calendar file</strong>, and open it in Outlook.</li>
    <li>Click <strong>Invite Attendees</strong>.</li>
    <li>Copy the people from <strong>Recipients</strong> below, and paste them in To, or in Optional for many.</li>
    <li>Click <strong>Send</strong>.</li>
  </ol>
  <p className={Classes.TEXT_MUTED} style={{ marginTop: 4 }}>The event shows as free: it doesn't block people's calendars.</p>
</div>;


const AnnouncementsSection = () => {
  const queryClient = useQueryClient();
  const { data, isPending, error, refetch } = useQuery(adminAnnouncementsQuery);
  // {announcement} to edit one, {initial} for a new one
  const [editing, setEditing] = useState(null);
  const [deleting, setDeleting] = useState(null);
  const [busy, setBusy] = useState(false);
  const [show_past, setShowPast] = useState(false);
  const announcements = data?.announcements ?? [];
  const past = data?.past ?? [];

  const remove = announcement => {
    setBusy(true);
    deleteAnnouncement(queryClient, announcement.id)
      .then(() => toaster.show({ message: `Deleted “${announcement.title}”`, intent: Intent.SUCCESS, icon: 'tick', timeout: 3000 }))
      .catch(error => toaster.show({ message: errorMessage(error), intent: Intent.DANGER, timeout: 8000 }))
      .finally(() => { setBusy(false); setDeleting(null); });
  };
  const actions = { onEdit: announcement => setEditing({ announcement }), onDelete: setDeleting };

  return <section aria-label="Announcements" style={{ marginBottom: 32 }}>
    <div style={{ display: 'flex', flexWrap: 'wrap', alignItems: 'center', gap: 8, marginBottom: 8 }}>
      <h4 className={Classes.HEADING} style={{ margin: 0, marginRight: 'auto' }}>Announcements</h4>
      <Button icon="wrench" text="Scheduled maintenance" onClick={() => setEditing({ initial: maintenance_form() })}/>
      <Button icon="add" intent={Intent.PRIMARY} text="New announcement" onClick={() => setEditing({})}/>
    </div>
    <p className={Classes.TEXT_MUTED} style={{ marginTop: 0, maxWidth: 820 }}>
      A banner at the top of every page, sign-in page included, for everyone, while it's on: a maintenance, an outage,
      a big change. Pages that stay open get it within 5 minutes. People can dismiss it unless you say otherwise,
      and it shows again when you edit it. With an event, people see it in their time zone and can add it to their calendar.
    </p>
    {isPending && <Loading label="Loading the announcements"/>}
    {error && <AdminError error={error} refetch={refetch} what="the announcements"/>}
    {data && (announcements.length === 0
      ? <NonIdealState icon="notifications" title="No announcements" description="Nothing shows now, and nothing is planned."/>
      : <AnnouncementsTable announcements={announcements} {...actions}/>)}
    {past.length > 0 && <div style={{ marginTop: 8 }}>
      <Button variant="minimal" size="small" icon={show_past ? 'chevron-up' : 'chevron-down'} aria-expanded={show_past} onClick={() => setShowPast(!show_past)}
        text={show_past ? 'Hide the past announcements' : `Show the past announcements (${past.length})`}/>
      <Collapse isOpen={show_past}>
        <AnnouncementsTable announcements={past} past {...actions}/>
      </Collapse>
    </div>}
    <InviteHowTo/>

    <AnnouncementDialog isOpen={!!editing} onClose={() => setEditing(null)} announcement={editing?.announcement} initial={editing?.initial}/>
    <Alert
      isOpen={!!deleting}
      intent={Intent.DANGER}
      icon="trash"
      cancelButtonText="Cancel"
      confirmButtonText="Delete"
      loading={busy}
      onCancel={() => setDeleting(null)}
      onConfirm={() => remove(deleting)}
      canEscapeKeyCancel
    >
      <p>Delete <strong>{deleting?.title}</strong>? Its banner disappears from every page within 5 minutes, and its calendar file stops working.</p>
    </Alert>
  </section>;
};


export const Messages = () => <>
  <AnnouncementsSection/>
  <Recipients/>
  <Tips/>
</>;

export default Messages;
