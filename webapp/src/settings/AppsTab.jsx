// Settings → Connected apps: the apps the user allowed to get tokens for them, e.g. an MCP client like Open WebUI
// (QA-Board's OAuth authorization server, backend/oauth.py). They ask on the consent page (oauth/ConsentPage.jsx).
import { useState } from "react";
import { useQuery, useQueryClient } from "@tanstack/react-query";
import { Alert, Button, Classes, HTMLTable, Intent, NonIdealState } from "@blueprintjs/core";

import { errorMessage } from "../api/http";
import { toaster } from "../toaster";
import { QueryError, Loading } from "./states";
import { Time } from "./format";
import { myAppsQuery, revokeApp } from "./api";


export const AppsTable = ({ apps, onRevoke }) => {
  const [revoking, setRevoking] = useState(null);
  const [busy, setBusy] = useState(false);
  const confirm = () => {
    setBusy(true);
    Promise.resolve(onRevoke(revoking))
      .then(() => toaster.show({ message: `${revoking.client.name} can't use QA-Board as you anymore`, intent: Intent.SUCCESS, icon: 'tick', timeout: 3000 }))
      .catch(error => toaster.show({ message: `Could not revoke the app: ${errorMessage(error)}`, intent: Intent.DANGER }))
      .finally(() => {
        setBusy(false);
        setRevoking(null);
      });
  };
  return <>
    <div style={{ overflowX: 'auto' }}>
      <HTMLTable compact style={{ width: '100%' }} aria-label="Connected apps">
        <thead>
          <tr>
            <th>App</th>
            <th>It can</th>
            <th>Allowed</th>
            <th>Last used</th>
            <th><span className={Classes.TEXT_MUTED} style={{ fontWeight: 'normal' }}>Actions</span></th>
          </tr>
        </thead>
        <tbody>
          {apps.map(app => <tr key={app.id}>
            <td>
              <strong>{app.client.name}</strong>
              {app.resource && <div className={Classes.TEXT_MUTED} style={{ fontSize: 12 }}>for <code>{app.resource}</code></div>}
            </td>
            <td>{app.scopes.map(scope => scope.description).join(', ')}</td>
            <td><Time iso={app.created_at}/></td>
            <td><Time iso={app.last_used_at} never="Never used"/></td>
            <td>
              <Button size="small" variant="outlined" intent={Intent.DANGER} text="Revoke" aria-label={`Revoke ${app.client.name}`} onClick={() => setRevoking(app)}/>
            </td>
          </tr>)}
        </tbody>
      </HTMLTable>
    </div>
    <Alert
      isOpen={!!revoking}
      icon="trash"
      intent={Intent.DANGER}
      cancelButtonText="Cancel"
      confirmButtonText="Revoke"
      loading={busy}
      onCancel={() => setRevoking(null)}
      onConfirm={confirm}
      canEscapeKeyCancel
    >
      {revoking && <p>
        Revoke <strong>{revoking.client.name}</strong>? Its tokens stop working right away. To use it again, you'll allow it again.
      </p>}
    </Alert>
  </>;
};


export const AppsTab = () => {
  const queryClient = useQueryClient();
  const { data: apps, isPending, error, refetch } = useQuery(myAppsQuery);
  return <section aria-label="Connected apps">
    <p className={Classes.TEXT_MUTED} style={{ marginTop: 0, maxWidth: 640 }}>
      Apps you allowed to use QA-Board as you, e.g. an AI assistant that reads your results with QA-Board's MCP server.
      They can't change anything. Revoke the ones you don't use anymore.
    </p>
    {isPending && <Loading label="Loading your apps"/>}
    {error && <QueryError error={error} refetch={refetch} what="your apps"/>}
    {!isPending && !error && (apps.length === 0
      ? <NonIdealState icon="applications" title="No connected apps" description="Apps you allow to use QA-Board as you show up here."/>
      : <AppsTable apps={apps} onRevoke={app => revokeApp(queryClient, app.id)}/>)}
  </section>;
};

export default AppsTab;
