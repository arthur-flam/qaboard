// Admin → Messages → Tips emails: the welcome and weekly tips people get after they sign up, where people are in them,
// a preview of each, and enrolling the people who signed up before the tips were turned on
import { useState } from "react";
import { useQuery, useQueryClient } from "@tanstack/react-query";
import { Alert, Button, Callout, Classes, Dialog, DialogBody, HTMLSelect, HTMLTable, Intent, NonIdealState, Tag, Tooltip } from "@blueprintjs/core";

import { Link } from "../../router";
import { errorMessage } from "../../api/http";
import { toaster } from "../../toaster";
import { useSiteConfig } from "../../hooks";
import { Loading } from "../../settings/states";
import { docsUrl } from "../settings/SettingRow";
import { AdminError } from "../states";
import { enrollInTips, sendTestTip, tipPreviewUrl, tipsQuery } from "../api";
import { count } from "./Recipients";


const docs_page = '/docs/backend-admin/messaging-users#tips-emails';

const people_stats = [
  { key: 'enrolled', label: 'enrolled', description: 'People in the sequence: new users since the tips were on, and those you enrolled.' },
  { key: 'due', label: 'due now', description: 'Their next tip goes out within the hour.' },
  { key: 'waiting', label: 'waiting for their next tip', description: 'They got a tip in the last days.' },
  { key: 'paused', label: 'paused', description: "They weren't active lately: their tips resume when they come back." },
  { key: 'finished', label: 'finished', description: 'They got every tip.' },
  { key: 'opted_out', label: 'turned them off', description: 'In Settings → Preferences, or from an email.' },
  { key: 'no_email', label: 'no email', description: "QA-Board doesn't know their email address." },
];

const enroll_days_options = [7, 30, 90, 365].map(days => ({ value: days, label: `${days} days` }));


const PreviewDialog = ({ tip, onClose }) => <Dialog
  isOpen={!!tip}
  onClose={onClose}
  title={tip ? `Preview: ${tip.subject}` : ''}
  icon="envelope"
  style={{ width: 720, maxWidth: 'calc(100vw - 32px)' }}
>
  {tip && <DialogBody>
    <p style={{ marginTop: 0 }}><span className={Classes.TEXT_MUTED}>Subject:</span> <strong>{tip.subject}</strong></p>
    {tip.preheader && <p className={Classes.TEXT_MUTED}>{tip.preheader}</p>}
    <iframe
      title={`Preview of ${tip.subject}`}
      src={tipPreviewUrl(tip.key)}
      style={{ display: 'block', width: 640, maxWidth: '100%', height: '65vh', margin: '0 auto', border: '1px solid var(--qa-border)', borderRadius: 4, background: '#fff' }}
    />
  </DialogBody>}
</Dialog>;


const TestButton = ({ tip }) => {
  const [sending, setSending] = useState(false);
  const send = () => {
    setSending(true);
    sendTestTip(tip.key)
      .then(({ sent_to }) => toaster.show({ message: `Sent “${tip.subject}” to ${sent_to}`, intent: Intent.SUCCESS, icon: 'tick', timeout: 4000 }))
      .catch(error => toaster.show({ message: errorMessage(error), intent: Intent.DANGER, timeout: 8000 }))
      .finally(() => setSending(false));
  };
  return <Button size="small" variant="minimal" icon="send-message" text="Send me a test" loading={sending} onClick={send}/>;
};


const TipsTable = ({ tips, onPreview }) => <div style={{ overflowX: 'auto' }}>
  <HTMLTable compact style={{ width: '100%' }} aria-label="Tips">
    <thead>
      <tr>
        <th>#</th>
        <th>Subject</th>
        <th>Key</th>
        <th>Source</th>
        <th style={{ textAlign: 'right' }}>Sent</th>
        <th style={{ textAlign: 'right' }}>Failed</th>
        <th style={{ textAlign: 'right' }} title="Not sent, e.g. a welcome for people who were already using QA-Board">Skipped</th>
        <th aria-label="Actions"/>
      </tr>
    </thead>
    <tbody>
      {tips.map(tip => <tr key={tip.key}>
        <td>{tip.position}</td>
        <td style={{ minWidth: 240 }}>
          <div>{tip.subject} {tip.new_users_only && <Tooltip content="Only for new users, at their first sign-in" hoverOpenDelay={300}><Tag minimal round intent={Intent.SUCCESS}>welcome</Tag></Tooltip>}</div>
          {tip.preheader && <div className={Classes.TEXT_MUTED} style={{ fontSize: 12 }}>{tip.preheader}</div>}
        </td>
        <td><code>{tip.key}</code></td>
        <td title={tip.file ?? undefined}><Tag minimal intent={tip.source === 'site' ? Intent.PRIMARY : undefined}>{tip.source}</Tag></td>
        <td style={{ textAlign: 'right' }}>{tip.sent ?? 0}</td>
        <td style={{ textAlign: 'right' }}>{tip.failed ? <Tag minimal round intent={Intent.DANGER}>{tip.failed}</Tag> : 0}</td>
        <td style={{ textAlign: 'right' }}>{tip.skipped ?? 0}</td>
        <td style={{ whiteSpace: 'nowrap', textAlign: 'right' }}>
          <Button size="small" variant="minimal" icon="eye-open" text="Preview" onClick={() => onPreview(tip)}/>
          <TestButton tip={tip}/>
        </td>
      </tr>)}
    </tbody>
  </HTMLTable>
</div>;


const Enroll = ({ not_enrolled, inactive_days }) => {
  const queryClient = useQueryClient();
  const [active_days, setActiveDays] = useState(30);
  const [confirming, setConfirming] = useState(false);
  const [busy, setBusy] = useState(false);
  const enroll = () => {
    setBusy(true);
    enrollInTips(queryClient, active_days)
      .then(({ enrolled }) => toaster.show({
        message: enrolled ? `Enrolled ${count(enrolled, 'person', 'people')}: they get their first tip within the hour` : 'Nobody to enroll: everyone active already gets the tips',
        intent: Intent.SUCCESS, icon: 'tick', timeout: 5000,
      }))
      .catch(error => toaster.show({ message: errorMessage(error), intent: Intent.DANGER, timeout: 8000 }))
      .finally(() => { setBusy(false); setConfirming(false); });
  };
  return <div style={{ display: 'flex', flexWrap: 'wrap', alignItems: 'center', gap: 8, marginTop: 16 }}>
    <span>
      {not_enrolled
        ? <><strong>{count(not_enrolled, 'person', 'people')}</strong> active in the last {inactive_days} days {not_enrolled === 1 ? "doesn't" : "don't"} get the tips: they signed up before.</>
        : 'Everyone active gets the tips.'}
    </span>
    <Button icon="new-person" text="Enroll people already using QA-Board" onClick={() => setConfirming(true)}/>
    <span className={Classes.TEXT_MUTED}>active in the last</span>
    <HTMLSelect aria-label="Enroll people active in the last" options={enroll_days_options} value={active_days} onChange={e => setActiveDays(Number(e.target.value))}/>
    <Alert
      isOpen={confirming}
      intent={Intent.PRIMARY}
      icon="envelope"
      cancelButtonText="Cancel"
      confirmButtonText="Enroll them"
      loading={busy}
      onCancel={() => setConfirming(false)}
      onConfirm={enroll}
      canEscapeKeyCancel
    >
      <p>Enroll the people active in the last {active_days} days who don't get the tips yet?</p>
      <p>They get one tip a week until the last one, not the welcome. They can turn them off from any of them, or in Settings → Preferences.</p>
    </Alert>
  </div>;
};


export const Tips = () => {
  const { data, isPending, error, refetch } = useQuery(tipsQuery);
  const { docs_root } = useSiteConfig();
  const [previewing, setPreviewing] = useState(null);

  return <section aria-label="Tips emails" style={{ marginBottom: 32 }}>
    <h4 className={Classes.HEADING} style={{ display: 'flex', alignItems: 'center', gap: 8 }}>
      Tips emails
      {data && <Tag minimal round intent={data.enabled ? Intent.SUCCESS : undefined}>{data.enabled ? 'On' : 'Off'}</Tag>}
    </h4>
    <p className={Classes.TEXT_MUTED} style={{ marginTop: 0, maxWidth: 820 }}>
      New users get a welcome email at their first sign-in, then one tip about QA-Board
      {data ? ` every ${count(data.every_days, 'day', 'days')}` : ' a week'}, then they stop.
      Tips pause for people inactive {data ? `for ${data.inactive_days} days` : 'for a while'}.
      {' '}<Link to="/admin/settings?group=tips">{data?.enabled ? 'Change them in Admin → Settings → Tips emails' : 'Turn them on in Admin → Settings → Tips emails'}</Link>.
    </p>
    {isPending && <Loading label="Loading the tips"/>}
    {error && <AdminError error={error} refetch={refetch} what="the tips"/>}
    {data && <>
      {!data.email_enabled && <Callout intent={Intent.WARNING} compact style={{ marginBottom: 12 }}>
        QA-Board can't send emails yet: <Link to="/admin/settings?group=email">set an SMTP server in Admin → Settings → Email</Link>.
      </Callout>}
      {data.errors?.length > 0 && <Callout intent={Intent.WARNING} compact title="Some tips can't be read" style={{ marginBottom: 12 }}>
        <ul style={{ margin: '4px 0 0', paddingLeft: 20 }}>{data.errors.map((error, i) => <li key={i}>{error}</li>)}</ul>
      </Callout>}
      <div style={{ display: 'flex', flexWrap: 'wrap', gap: '4px 16px', marginBottom: 12 }}>
        {people_stats.map(stat => <Tooltip key={stat.key} content={stat.description} hoverOpenDelay={300}>
          <span style={{ whiteSpace: 'nowrap' }}>
            <strong>{data.people?.[stat.key] ?? 0}</strong> <span className={Classes.TEXT_MUTED}>{stat.label}</span>
          </span>
        </Tooltip>)}
      </div>
      {data.tips?.length
        ? <TipsTable tips={data.tips} onPreview={setPreviewing}/>
        : <NonIdealState icon="lightbulb" title="No tips" description="QA-Board found no tips to send."/>}
      <Enroll not_enrolled={data.people?.not_enrolled_active ?? 0} inactive_days={data.inactive_days}/>
      <p className={Classes.TEXT_MUTED} style={{ marginTop: 16, marginBottom: 0, maxWidth: 820 }}>
        Each tip is a Markdown file: the built-in ones are in the repository's <code>backend/notifications/tips/</code>.
        Add or replace tips with your own folder, in the <code>tips.folder</code> setting{data.folder ? <> (now <code>{data.folder}</code>)</> : null}.
        {' '}<a href={docsUrl(docs_root, docs_page)} target="_blank" rel="noopener noreferrer">How to write them</a>
      </p>
    </>}
    <PreviewDialog tip={previewing} onClose={() => setPreviewing(null)}/>
  </section>;
};

export default Tips;
