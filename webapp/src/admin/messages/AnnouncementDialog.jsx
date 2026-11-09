// Admin → Messages → New announcement / Edit: the banner's text, when it shows, and the event it's about, with a preview.
import { useState } from "react";
import { useQueryClient } from "@tanstack/react-query";
import { Button, Callout, Classes, Dialog, DialogBody, DialogFooter, FormGroup, HTMLSelect, InputGroup, Intent, Switch, TextArea } from "@blueprintjs/core";
import { DateInput, TimePrecision } from "@blueprintjs/datetime";
import enUS from "date-fns/locale/en-US";
import { DateTime } from "luxon";

import { errorMessage } from "../../api/http";
import { AnnouncementBanner } from "../../announcements/Announcements";
import { saveAnnouncement } from "../api";

import "@blueprintjs/datetime/lib/css/blueprint-datetime.css";


// the server's limits
export const MAX_TITLE = 120;
export const MAX_MESSAGE = 600;

export const intent_options = [
  { value: 'primary', label: 'Information' },
  { value: 'success', label: 'Success' },
  { value: 'warning', label: 'Warning' },
  { value: 'danger', label: 'Outage/incident' },
];

const now_iso = (now = DateTime.now()) => now.startOf('minute').toISO({ suppressMilliseconds: true });

export const new_form = (now = DateTime.now()) => ({
  title: '', message: '', intent: 'primary', link_url: '', link_label: '',
  starts_at: now_iso(now), ends_at: null, dismissible: true, event_starts_at: null, event_ends_at: null,
});

// Tomorrow 18:00–20:00 in the admin's time zone, the banner from now until it ends: admins adjust the dates
export const maintenance_form = (now = DateTime.now()) => {
  const start = now.plus({ days: 1 }).set({ hour: 18, minute: 0, second: 0, millisecond: 0 });
  const end = start.plus({ hours: 2 });
  return {
    ...new_form(now),
    intent: 'warning',
    title: 'Scheduled maintenance',
    message: 'QA-Board will be unavailable while we upgrade it.',
    ends_at: end.toISO({ suppressMilliseconds: true }),
    event_starts_at: start.toISO({ suppressMilliseconds: true }),
    event_ends_at: end.toISO({ suppressMilliseconds: true }),
  };
};

export const formOf = announcement => ({
  title: announcement.title ?? '',
  message: announcement.message ?? '',
  intent: announcement.intent ?? 'primary',
  link_url: announcement.link_url ?? '',
  link_label: announcement.link_label ?? '',
  starts_at: announcement.starts_at ?? null,
  ends_at: announcement.ends_at ?? null,
  dismissible: announcement.dismissible !== false,
  event_starts_at: announcement.event_starts_at ?? null,
  event_ends_at: announcement.event_ends_at ?? null,
});

// The server takes UTC
const utc = value => {
  if (!value) return null;
  const date = DateTime.fromISO(value, { zone: 'utc' });
  return date.isValid ? date.toUTC().toISO({ suppressMilliseconds: true }) : null;
};

export const bodyOf = form => ({
  title: form.title.trim(),
  message: form.message.trim(),
  intent: form.intent,
  link_url: form.link_url.trim() || null,
  link_label: form.link_url.trim() ? form.link_label.trim() || null : null,
  starts_at: utc(form.starts_at),
  ends_at: utc(form.ends_at),
  dismissible: form.dismissible,
  event_starts_at: utc(form.event_starts_at),
  event_ends_at: utc(form.event_ends_at),
});

// Edits send what changed: an unchanged announcement keeps its revision, and stays dismissed
export const changesOf = (initial, form) => {
  const before = bodyOf(initial);
  return Object.fromEntries(Object.entries(bodyOf(form)).filter(([key, value]) => before[key] !== value));
};


const Counter = ({ length, max }) => <span className={Classes.TEXT_MUTED} style={{ fontSize: 12 }} aria-label={`${length} of ${max} characters`}>{length}/{max}</span>;

const DateField = ({ id, label, value, onChange, optional = false, helperText }) => <FormGroup label={label} labelFor={id} labelInfo={optional ? '(optional)' : undefined} helperText={helperText} style={{ flex: '1 1 220px', minWidth: 0 }}>
  <DateInput
    value={value}
    onChange={value => onChange(value)}
    timePrecision={TimePrecision.MINUTE}
    dateFnsFormat="yyyy-MM-dd HH:mm"
    locale={enUS}
    placeholder="YYYY-MM-DD HH:mm"
    closeOnSelection={false}
    canClearSelection={optional}
    showActionsBar
    inputProps={{ id }}
    fill
  />
</FormGroup>;

const row = { display: 'flex', flexWrap: 'wrap', gap: '0 12px' };


const Form = ({ announcement, initial, onClose }) => {
  const queryClient = useQueryClient();
  const [form, setForm] = useState(initial);
  const [error, setError] = useState(null);
  const [saving, setSaving] = useState(false);
  const set = changes => setForm(form => ({ ...form, ...changes }));
  const field = key => event => set({ [key]: event.target.value });

  const submit = event => {
    event.preventDefault();
    if (!form.title.trim()) return setError('The title is missing.');
    const body = announcement ? changesOf(formOf(announcement), form) : bodyOf(form);
    if (announcement && Object.keys(body).length === 0) return onClose();
    setSaving(true);
    setError(null);
    saveAnnouncement(queryClient, announcement?.id, body)
      .then(() => onClose())
      .catch(error => setError(errorMessage(error)))
      .finally(() => setSaving(false));
  };

  const preview = { id: 'preview', ...bodyOf(form), title: form.title.trim() || 'Title', calendar_url: form.event_starts_at ? '#' : null };
  return <form onSubmit={submit} aria-label={announcement ? 'Edit the announcement' : 'New announcement'}>
    <DialogBody>
      {error && <Callout intent={Intent.DANGER} role="alert" compact style={{ marginBottom: 16 }}>{error}</Callout>}
      <FormGroup label="Title" labelFor="announcement-title" labelInfo={<Counter length={form.title.length} max={MAX_TITLE}/>} helperText="A few words people get at a glance, e.g. “Scheduled maintenance”.">
        <InputGroup id="announcement-title" value={form.title} onChange={field('title')} maxLength={MAX_TITLE} autoFocus required/>
      </FormGroup>
      <FormGroup label="Message" labelFor="announcement-message" labelInfo={<Counter length={form.message.length} max={MAX_MESSAGE}/>} helperText="Plain text. Put the details behind the link.">
        <TextArea id="announcement-message" value={form.message} onChange={field('message')} maxLength={MAX_MESSAGE} rows={3} fill/>
      </FormGroup>
      <div style={row}>
        <FormGroup label="Kind" labelFor="announcement-intent" style={{ flex: '0 0 auto' }}>
          <HTMLSelect id="announcement-intent" options={intent_options} value={form.intent} onChange={field('intent')}/>
        </FormGroup>
        <FormGroup label="Link" labelFor="announcement-link" labelInfo="(optional)" style={{ flex: '2 1 220px' }}>
          <InputGroup id="announcement-link" value={form.link_url} onChange={field('link_url')} placeholder="https://… or /docs/…"/>
        </FormGroup>
        <FormGroup label="Link text" labelFor="announcement-link-label" style={{ flex: '1 1 140px' }}>
          <InputGroup id="announcement-link-label" value={form.link_label} onChange={field('link_label')} placeholder="Learn more" disabled={!form.link_url.trim()}/>
        </FormGroup>
      </div>
      <div style={row}>
        <DateField id="announcement-starts" label="Show from" value={form.starts_at} onChange={starts_at => set({ starts_at })} helperText="Empty: now."/>
        <DateField id="announcement-ends" label="Until" value={form.ends_at} onChange={ends_at => set({ ends_at })} optional helperText="Empty: until you delete it."/>
      </div>
      <div style={row}>
        <DateField id="announcement-event-starts" label="Event starts" value={form.event_starts_at} onChange={event_starts_at => set({ event_starts_at })} optional helperText="e.g. the maintenance: people get it in their time zone, and a calendar file."/>
        <DateField id="announcement-event-ends" label="Event ends" value={form.event_ends_at} onChange={event_ends_at => set({ event_ends_at })} optional/>
      </div>
      <Switch checked={form.dismissible} onChange={() => set({ dismissible: !form.dismissible })} label="People can dismiss it (it shows again if you edit it)"/>
      <FormGroup label="Preview" style={{ marginBottom: 0 }}>
        <AnnouncementBanner announcement={preview} preview onDismiss={() => {}} style={{ borderRadius: 4 }}/>
      </FormGroup>
    </DialogBody>
    <DialogFooter actions={<>
      <Button text="Cancel" onClick={onClose}/>
      <Button type="submit" intent={Intent.PRIMARY} icon={announcement ? 'floppy-disk' : 'add'} text={announcement ? 'Save' : 'Publish'} loading={saving}/>
    </>}/>
  </form>;
};


// `announcement`: to edit one. `initial`: a new one's form, e.g. maintenance_form(). The form starts again at each opening.
export const AnnouncementDialog = ({ isOpen, onClose, announcement = null, initial }) => <Dialog
  isOpen={isOpen}
  onClose={onClose}
  title={announcement ? 'Edit the announcement' : 'New announcement'}
  icon="notifications"
  style={{ width: 680, maxWidth: 'calc(100vw - 32px)' }}
  canOutsideClickClose={false}
>
  {isOpen && <Form announcement={announcement} initial={announcement ? formOf(announcement) : initial ?? new_form()} onClose={onClose}/>}
</Dialog>;

export default AnnouncementDialog;
