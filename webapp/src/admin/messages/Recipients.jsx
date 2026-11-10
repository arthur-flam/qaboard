// Admin → Messages → Recipients: the email addresses of the people who use QA-Board, to paste in Outlook
import { useEffect, useState } from "react";
import { useQuery } from "@tanstack/react-query";
import { AnchorButton, Button, Classes, FormGroup, HTMLSelect, InputGroup, Intent, Switch, Tooltip } from "@blueprintjs/core";

import { errorMessage } from "../../api/http";
import { toaster } from "../../toaster";
import { copyWithToast } from "../../utils/clipboard";
import { Loading } from "../../settings/states";
import { AdminError } from "../states";
import { recipientsQuery } from "../api";


export const active_days_options = [
  { value: 7, label: '7 days' },
  { value: 30, label: '30 days' },
  { value: 90, label: '90 days' },
  { value: 365, label: 'year' },
  { value: 0, label: 'Everyone, active or not' },
];

// "1 person", "2 people"
export const count = (n, one, many) => `${n} ${n === 1 ? one : many}`;

// Copies the "Name <email>; ..." line Outlook takes, says how many in a toast. Used by Admin → Users too.
export const copyRecipients = async (queryClient, params) => {
  try {
    const { recipients, to } = await queryClient.fetchQuery(recipientsQuery(params));
    if (!recipients.length)
      return toaster.show({ message: 'Nobody to copy: no one with a known email matches.', intent: Intent.WARNING, icon: 'info-sign', timeout: 4000 });
    await copyWithToast(to, { what: count(recipients.length, 'address', 'addresses'), message: `Copied ${count(recipients.length, 'address', 'addresses')}: paste them in Outlook's To or Bcc` });
  } catch (error) {
    toaster.show({ message: errorMessage(error), intent: Intent.DANGER, timeout: 8000 });
  }
};

const csv_cell = text => /[",;\n]/.test(text) ? `"${text.replace(/"/g, '""')}"` : text;
export const recipientsCsv = recipients => ['name,email', ...recipients.map(r => [r.full_name || r.user_name, r.email ?? ''].map(csv_cell).join(','))].join('\n') + '\n';

const names = (recipients, max = 10) => {
  const shown = recipients.slice(0, max).map(r => r.full_name || r.user_name).join(', ');
  return recipients.length > max ? `${shown}, and ${recipients.length - max} more` : shown;
};


export const Recipients = () => {
  const [active_days, setActiveDays] = useState(30);
  const [admins, setAdmins] = useState(false);
  // the query follows the project a moment after admins stop typing
  const [project_input, setProjectInput] = useState('');
  const [project, setProject] = useState('');
  useEffect(() => {
    const timer = setTimeout(() => setProject(project_input.trim().replace(/^\/+|\/+$/g, '')), 300);
    return () => clearTimeout(timer);
  }, [project_input]);

  const { data, isPending, isFetching, error, refetch } = useQuery(recipientsQuery({ active_days, project, admins }));
  const recipients = data?.recipients ?? [];
  const without_email = data?.without_email ?? [];

  return <section aria-label="Recipients" style={{ marginBottom: 32 }}>
    <h4 className={Classes.HEADING}>Recipients</h4>
    <p className={Classes.TEXT_MUTED} style={{ marginTop: 0, maxWidth: 820 }}>
      To email the people who use QA-Board, e.g. about a maintenance or a big change: pick who, copy their addresses,
      and paste them in Outlook's To field, or in Bcc for large lists.
    </p>
    <div style={{ display: 'flex', flexWrap: 'wrap', alignItems: 'flex-end', gap: '0 16px' }}>
      <FormGroup label="Active in the last" labelFor="recipients-active-days">
        <HTMLSelect id="recipients-active-days" options={active_days_options} value={active_days} onChange={e => setActiveDays(Number(e.target.value))}/>
      </FormGroup>
      <FormGroup label="Who used the project" labelFor="recipients-project" labelInfo="(optional)">
        <InputGroup id="recipients-project" placeholder="group/repo" value={project_input} onChange={e => setProjectInput(e.target.value)} style={{ width: 220 }}/>
      </FormGroup>
      <FormGroup>
        <Switch checked={admins} onChange={() => setAdmins(!admins)} label="Admins only" style={{ marginBottom: 0 }}/>
      </FormGroup>
    </div>
    {isPending && <Loading label="Loading the recipients"/>}
    {error && <AdminError error={error} refetch={refetch} what="the recipients"/>}
    {!isPending && !error && <div style={{ opacity: isFetching ? 0.7 : 1 }}>
      <p style={{ marginBottom: 8 }}>
        <strong>{count(recipients.length, 'person', 'people')}</strong>
        {recipients.length > 0 && <span className={Classes.TEXT_MUTED}>: {names(recipients)}</span>}
      </p>
      {without_email.length > 0 && <p className={Classes.TEXT_MUTED} style={{ marginBottom: 8 }}>
        <Tooltip content={<span style={{ overflowWrap: 'anywhere' }}>{without_email.slice(0, 50).join(', ')}{without_email.length > 50 ? '…' : ''}</span>} hoverOpenDelay={200}>
          <span style={{ textDecoration: 'underline dotted', cursor: 'help' }}>{count(without_email.length, 'more person', 'more people')}</span>
        </Tooltip>
        {' '}match but have no known email: set the email domain of users in Admin → Settings → Email, or their email in their account.
      </p>}
      <div style={{ display: 'flex', flexWrap: 'wrap', gap: 8 }}>
        <Button icon="duplicate" intent={Intent.PRIMARY} text="Copy for Outlook" disabled={!recipients.length} onClick={() => copyWithToast(data.to, { what: count(recipients.length, 'address', 'addresses') })}/>
        <AnchorButton
          icon="download"
          text="Download CSV"
          disabled={!recipients.length}
          href={recipients.length ? `data:text/csv;charset=utf-8,${encodeURIComponent(recipientsCsv(recipients))}` : undefined}
          download="qaboard-recipients.csv"
        />
      </div>
    </div>}
  </section>;
};

export default Recipients;
