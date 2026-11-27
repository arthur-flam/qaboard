// Admin → Connected apps: the apps users can allow to get read-only tokens for them, e.g. Open WebUI connected to
// QA-Board's MCP server (QA-Board's OAuth authorization server, backend/oauth.py; how to connect them:
// website/docs/backend-admin/mcp-clients.mdx). Users allow them on the consent page, and see them in Settings.
import { useState } from "react";
import { useQuery, useQueryClient } from "@tanstack/react-query";
import {
  Alert, Button, Callout, Classes, Dialog, DialogBody, DialogFooter, FormGroup, HTMLTable, InputGroup, Intent, Switch, Tag, TextArea,
} from "@blueprintjs/core";

import { errorMessage } from "../api/http";
import { toaster } from "../toaster";
import { useSiteConfig } from "../hooks";
import { copyWithToast } from "../utils/clipboard";
import { Loading } from "../settings/states";
import { Time } from "../settings/format";
import { AdminError } from "./states";
import { docsUrl } from "./settings/SettingRow";
import { oauthClientsQuery, registerOAuthClient, updateOAuthClient, revokeOAuthClient } from "./api";


const DOCS = '/docs/backend-admin/mcp-clients';
const mono = { className: Classes.MONOSPACE_TEXT, style: { overflowWrap: 'anywhere' } };

// one per line, as admins paste them
export const parseRedirectUris = text => [...new Set(text.split(/\s+/).map(uri => uri.trim()).filter(Boolean))];


const Copyable = ({ label, value, what, id }) => <FormGroup label={label} labelFor={id}>
  <InputGroup
    id={id}
    readOnly
    value={value}
    className={Classes.MONOSPACE_TEXT}
    onFocus={event => event.target.select()}
    rightElement={<Button icon="duplicate" text="Copy" onClick={() => copyWithToast(value, { what })}/>}
  />
</FormGroup>;

// What admins paste in the app, once: the secret is never shown again
const Credentials = ({ client }) => <>
  <Callout intent={Intent.WARNING} icon="eye-open" style={{ marginBottom: 16 }} title="Copy the client secret now">
    You won't be able to see it again. If you lose it, register the app again.
  </Callout>
  <Copyable id="oauth-client-id" label="Client ID" value={client.client_id} what="the client ID"/>
  <Copyable id="oauth-client-secret" label="Client secret" value={client.client_secret} what="the client secret"/>
  <p className={Classes.TEXT_MUTED}>In Open WebUI, paste them in the MCP server's connection, with the authentication “OAuth 2.1 (Static)”.</p>
</>;


// Registers an app, or changes one (`client`): its name and redirect URIs
const ClientDialog = ({ isOpen, client, onClose }) => {
  const queryClient = useQueryClient();
  const [name, setName] = useState(client?.name ?? '');
  const [uris, setUris] = useState((client?.redirect_uris ?? []).join('\n'));
  const [created, setCreated] = useState(null);
  const [error, setError] = useState(null);
  const [busy, setBusy] = useState(false);
  const redirect_uris = parseRedirectUris(uris);
  const valid = name.trim() && redirect_uris.length > 0;

  const submit = event => {
    event.preventDefault();
    if (!valid) return;
    setBusy(true);
    setError(null);
    const body = { name: name.trim(), redirect_uris };
    (client ? updateOAuthClient(queryClient, client.id, body) : registerOAuthClient(queryClient, body))
      .then(saved => {
        if (client) {
          toaster.show({ message: `Saved ${saved.name}`, intent: Intent.SUCCESS, icon: 'tick', timeout: 3000 });
          onClose();
        } else setCreated(saved);
      })
      .catch(error => setError(errorMessage(error)))
      .finally(() => setBusy(false));
  };

  const title = created ? `${created.name} is registered` : client ? `Change ${client.name}` : 'Register an app';
  return <Dialog isOpen={isOpen} onClose={onClose} title={title} icon="applications" style={{ width: 600 }}>
    {created
      ? <>
        <DialogBody><Credentials client={created}/></DialogBody>
        <DialogFooter actions={<Button intent={Intent.PRIMARY} text="Done" onClick={onClose}/>}/>
      </>
      : <form onSubmit={submit}>
        <DialogBody>
          {error && <Callout intent={Intent.DANGER} style={{ marginBottom: 16 }} role="alert">{error}</Callout>}
          <FormGroup label="Name" labelFor="oauth-client-name" helperText="Users see it when they allow the app, e.g. “Open WebUI”.">
            <InputGroup id="oauth-client-name" value={name} onChange={e => setName(e.target.value)} autoFocus maxLength={100}/>
          </FormGroup>
          <FormGroup
            label="Redirect URIs"
            labelFor="oauth-client-redirect-uris"
            helperText={<>Where users go back to the app, one per line, exactly. Open WebUI's:{' '}
              <code>https://&lt;open-webui&gt;/oauth/clients/mcp:&lt;connection ID&gt;/callback</code>.</>}
          >
            <TextArea
              id="oauth-client-redirect-uris"
              value={uris}
              onChange={e => setUris(e.target.value)}
              fill
              rows={3}
              className={Classes.MONOSPACE_TEXT}
              placeholder="https://openwebui.example.com/oauth/clients/mcp:qaboard/callback"
            />
          </FormGroup>
        </DialogBody>
        <DialogFooter actions={<>
          <Button text="Cancel" onClick={onClose}/>
          <Button type="submit" intent={Intent.PRIMARY} text={client ? 'Save' : 'Register'} loading={busy} disabled={!valid}/>
        </>}/>
      </form>}
  </Dialog>;
};


const ClientsTable = ({ clients, onEdit, onRevoke }) => <div style={{ overflowX: 'auto' }}>
  <HTMLTable compact style={{ width: '100%' }} aria-label="Registered apps">
    <thead>
      <tr><th>App</th><th>Redirect URIs</th><th>Users</th><th>Registered</th><th>Status</th><th><span className={Classes.TEXT_MUTED} style={{ fontWeight: 'normal' }}>Actions</span></th></tr>
    </thead>
    <tbody>
      {clients.map(client => <tr key={client.id} style={client.status === 'revoked' ? { opacity: 0.7 } : undefined}>
        <td>
          <strong>{client.name}</strong>
          <div className={Classes.TEXT_MUTED} style={{ fontSize: 12 }}><code>{client.client_id}</code></div>
        </td>
        <td style={{ maxWidth: 360 }}>{client.redirect_uris.map(uri => <div key={uri} {...mono}>{uri}</div>)}</td>
        <td>{client.users}</td>
        <td>
          <Time iso={client.created_at}/>
          {client.created_by && <div className={Classes.TEXT_MUTED} style={{ fontSize: 12 }}>by {client.created_by.user_name}</div>}
        </td>
        <td>{client.status === 'revoked' ? <Tag minimal>Revoked</Tag> : <Tag minimal intent={Intent.SUCCESS}>Active</Tag>}</td>
        <td style={{ whiteSpace: 'nowrap' }}>
          {client.status === 'active' && <>
            <Button size="small" variant="outlined" icon="edit" text="Change" aria-label={`Change ${client.name}`} onClick={() => onEdit(client)}/>
            {' '}
            <Button size="small" variant="outlined" intent={Intent.DANGER} text="Revoke" aria-label={`Revoke ${client.name}`} onClick={() => onRevoke(client)}/>
          </>}
        </td>
      </tr>)}
    </tbody>
  </HTMLTable>
</div>;


export const ConnectedApps = () => {
  const queryClient = useQueryClient();
  const { data: clients, isPending, error, refetch } = useQuery(oauthClientsQuery);
  const { docs_root = '/' } = useSiteConfig();
  // {client}: undefined when closed, null to register one
  const [editing, setEditing] = useState(undefined);
  const [revoking, setRevoking] = useState(null);
  const [busy, setBusy] = useState(false);
  const [show_revoked, setShowRevoked] = useState(false);
  const shown = (clients ?? []).filter(client => show_revoked || client.status === 'active');
  const nb_revoked = (clients ?? []).length - (clients ?? []).filter(client => client.status === 'active').length;

  const revoke = client => {
    setBusy(true);
    revokeOAuthClient(queryClient, client.id)
      .then(() => toaster.show({ message: `Revoked ${client.name}`, intent: Intent.SUCCESS, icon: 'tick', timeout: 3000 }))
      .catch(error => toaster.show({ message: `Could not revoke the app: ${errorMessage(error)}`, intent: Intent.DANGER }))
      .finally(() => {
        setBusy(false);
        setRevoking(null);
      });
  };

  return <section aria-labelledby="connected-apps-title">
    <div style={{ display: 'flex', alignItems: 'center', gap: 12, flexWrap: 'wrap', marginBottom: 8 }}>
      <h4 id="connected-apps-title" className={Classes.HEADING} style={{ margin: 0 }}>Connected apps</h4>
      <a href={docsUrl(docs_root, DOCS)} target="_blank" rel="noopener noreferrer" style={{ fontSize: 13 }}>Docs</a>
      <Button style={{ marginLeft: 'auto' }} intent={Intent.PRIMARY} icon="plus" text="Register an app" onClick={() => setEditing(null)}/>
    </div>
    <p className={Classes.TEXT_MUTED} style={{ margin: '0 0 12px', maxWidth: 760 }}>
      Apps users can allow to read QA-Board as them, e.g. Open WebUI connected to QA-Board's MCP server
      at <code>{window.location.origin}/mcp</code>. They get tokens that can only read, users see and revoke them
      in Settings → Connected apps.
    </p>
    {isPending && <Loading label="Loading the apps"/>}
    {error && <AdminError error={error} refetch={refetch} what="the apps"/>}
    {!isPending && !error && <>
      {shown.length > 0
        ? <ClientsTable clients={shown} onEdit={setEditing} onRevoke={setRevoking}/>
        : <p className={Classes.TEXT_MUTED}>No apps registered yet.</p>}
      {nb_revoked > 0 && <Switch style={{ marginTop: 12 }} checked={show_revoked} onChange={() => setShowRevoked(!show_revoked)} label={`Show revoked apps (${nb_revoked})`}/>}
    </>}
    {editing !== undefined && <ClientDialog isOpen client={editing} onClose={() => setEditing(undefined)}/>}
    <Alert
      isOpen={!!revoking}
      icon="trash"
      intent={Intent.DANGER}
      cancelButtonText="Cancel"
      confirmButtonText="Revoke"
      loading={busy}
      onCancel={() => setRevoking(null)}
      onConfirm={() => revoke(revoking)}
      canEscapeKeyCancel
    >
      {revoking && <p>
        Revoke <strong>{revoking.name}</strong>? It can't get tokens anymore, and the tokens of the {revoking.users} user{revoking.users === 1 ? '' : 's'} who
        allowed it stop working right away. This can't be undone.
      </p>}
    </Alert>
  </section>;
};

export default ConnectedApps;
