// /oauth/consent: users allow an app that admins registered (an MCP client like Open WebUI) to get tokens for them.
// QA-Board's authorization endpoint (backend/api/oauth.py) sends them here with the app's request in the URL; once they
// answer, the browser goes back to the app, with a code or with access_denied.
import { useState } from "react";
import { useQuery } from "@tanstack/react-query";
import { Button, Callout, Card, Classes, Icon, Intent } from "@blueprintjs/core";
import { useLocation } from "react-router";

import { http, errorMessage } from "../api/http";
import { Link } from "../router";
import { goTo } from "../login/api";
import { PageShell, RequireLogin } from "../components/PageShell";
import { Loading } from "../settings/states";


// `request`: the authorization request's query string, as the app sent it
export const consentQuery = request => ({
  queryKey: ['oauth-consent', request],
  queryFn: async ({ signal }) => (await http.get(`/api/v1/oauth/consent?${request}`, { signal })).data,
  staleTime: Infinity,
  retry: false,
});

// => {redirect_to}: where the browser goes back to the app
export const answerConsent = async (request, approve) => (await http.post('/api/v1/oauth/consent', { request, approve })).data;

const card_style = { maxWidth: 560 };


// The request can't be answered: an unknown app, or an error the app should hear about (with `redirect_to`)
const RequestError = ({ error }) => {
  const { redirect_to } = error.response?.data ?? {};
  return <Card style={card_style}>
    <Callout intent={Intent.DANGER} title="QA-Board can't sign you in to this app" role="alert">
      {errorMessage(error)}
    </Callout>
    {redirect_to && <Button style={{ marginTop: 12 }} icon="arrow-left" text="Back to the app" onClick={() => goTo(redirect_to)}/>}
  </Card>;
};


const Consent = ({ request, user }) => {
  const { data: consent, isPending, error } = useQuery(consentQuery(request));
  const [busy, setBusy] = useState(null);
  const [answer_error, setAnswerError] = useState(null);

  if (isPending) return <Loading label="Looking for the app"/>;
  if (error) return <RequestError error={error}/>;

  const answer = approve => {
    setBusy(approve ? 'allow' : 'deny');
    setAnswerError(null);
    answerConsent(request, approve)
      // busy until the browser leaves
      .then(({ redirect_to }) => goTo(redirect_to))
      .catch(error => {
        setAnswerError(errorMessage(error));
        setBusy(null);
      });
  };

  const { client, redirect_host, scopes, resource } = consent;
  return <Card style={card_style}>
    <h4 className={Classes.HEADING}>Allow {client.name} to use QA-Board as you?</h4>
    <p>
      {client.name} will get tokens to use QA-Board as <strong>{user.full_name || user.user_name}</strong>
      {user.full_name && user.full_name !== user.user_name && <> ({user.user_name})</>}. It will be able to:
    </p>
    <ul className={Classes.LIST_UNSTYLED} style={{ marginBottom: 16 }}>
      {scopes.map(scope => <li key={scope.id} style={{ display: 'flex', alignItems: 'center', gap: 8, marginBottom: 4 }}>
        <Icon icon="tick" intent={Intent.SUCCESS}/>{scope.description}
      </li>)}
    </ul>
    <table className={Classes.TEXT_MUTED} style={{ marginBottom: 12, borderSpacing: '12px 4px', marginLeft: -12 }}>
      <tbody>
        {/* where the code goes: an app pretending to be another would send it elsewhere */}
        <tr><td>Sends you back to</td><td><strong>{redirect_host}</strong></td></tr>
        {resource && <tr><td>For</td><td><code>{resource}</code></td></tr>}
      </tbody>
    </table>
    <p className={Classes.TEXT_MUTED}>
      It can't change anything. Your QA-Board admins registered this app. You can revoke its access anytime
      in <Link to="/settings/apps">Settings → Connected apps</Link>.
    </p>
    {answer_error && <Callout intent={Intent.DANGER} style={{ marginBottom: 12 }} role="alert">{answer_error}</Callout>}
    <div style={{ display: 'flex', gap: 8 }}>
      <Button intent={Intent.PRIMARY} icon="tick" text="Allow" loading={busy === 'allow'} disabled={!!busy} onClick={() => answer(true)}/>
      <Button icon="cross" text="Deny" loading={busy === 'deny'} disabled={!!busy} onClick={() => answer(false)}/>
    </div>
  </Card>;
};


export const ConsentPage = () => {
  const location = useLocation();
  const request = location.search.replace(/^\?/, '');
  return <PageShell title="Allow an app" description="An app asks to use QA-Board as you.">
    <RequireLogin what="allow this app">
      {user => <Consent key={request} request={request} user={user}/>}
    </RequireLogin>
  </PageShell>;
};

export default ConsentPage;
