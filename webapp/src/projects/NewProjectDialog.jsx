// "New project": creates a project before its first results, with you as its owner (design/user-management.md,
// Appendix G). The server says, as people type, whether they may create the id, else whom to ask or where to go.
// The first `qa run` creates projects too: the dialog says so, with the commands.
import { useEffect, useState } from "react";
import { useQuery, useQueryClient } from "@tanstack/react-query";
import { Button, Callout, Classes, Dialog, DialogBody, DialogFooter, FormGroup, HTMLSelect, InputGroup, Intent } from "@blueprintjs/core";

import { history } from "../router";
import { path_segment } from "../selection";
import { useUser } from "../hooks";
import { errorMessage } from "../api/http";
import { toaster } from "../toaster";
import { newProjectCheckQuery, createProject, projectIdProblem } from "./api";


// what people type, checked once they pause
const useDebounced = (value, delay = 300) => {
  const [debounced, setDebounced] = useState(value);
  useEffect(() => {
    const timer = setTimeout(() => setDebounced(value), delay);
    return () => clearTimeout(timer);
  }, [value, delay]);
  return debounced;
};

const CheckResult = ({ check, onUse }) => {
  if (!check) return null;
  if (check.exists)
    return <Callout intent={Intent.WARNING} compact icon="info-sign">{check.project} already exists.</Callout>;
  if (check.allowed) {
    const claims = check.new_namespace && check.namespace_kind !== 'user'
      ? <> It also creates the namespace <strong>{check.namespace}</strong>, with you as its owner.</>
      : null;
    return <Callout intent={Intent.SUCCESS} compact icon="tick">You can create it, and you'll be its owner.{claims}</Callout>;
  }
  return <Callout intent={Intent.WARNING} compact icon="disable" role="alert">
    {check.error}
    {check.hint && <div className={Classes.TEXT_MUTED} style={{ marginTop: 4 }}>{check.hint}</div>}
    {check.suggestion && <Button variant="minimal" size="small" intent={Intent.PRIMARY} style={{ marginTop: 4 }}
      text={`Use ${check.suggestion}`} onClick={() => onUse(check.suggestion)}/>}
  </Callout>;
};

export const NewProjectDialog = ({ isOpen, onClose }) => {
  const user = useUser();
  const queryClient = useQueryClient();
  const [id, setId] = useState(() => user.user_name ? `${user.user_name}/` : '');
  const [displayName, setDisplayName] = useState('');
  const [visibility, setVisibility] = useState('');
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState(null);
  const typed = id.trim();
  const problem = projectIdProblem(typed);
  const checked = useDebounced(problem ? '' : typed);
  const { data: check, isFetching } = useQuery(newProjectCheckQuery(checked));
  const current = check && check.project === typed ? check : null;

  const submit = event => {
    event.preventDefault();
    setSaving(true);
    setError(null);
    createProject(queryClient, { project: typed, ...(displayName.trim() ? { display_name: displayName.trim() } : {}), ...(visibility ? { visibility } : {}) })
      .then(project => {
        toaster.show({ message: `Created ${project.id}: you're its owner`, intent: Intent.SUCCESS, icon: 'tick', timeout: 4000 });
        onClose();
        history.push(`/${path_segment(project.id)}`);
      })
      .catch(error => {
        const data = error.response?.data;
        setError(data?.hint ? `${data.error} ${data.hint}` : errorMessage(error));
      })
      .finally(() => setSaving(false));
  };

  return <Dialog isOpen={isOpen} onClose={onClose} title="New project" icon="add">
    <form onSubmit={submit} aria-label="New project">
      <DialogBody>
        {error && <Callout intent={Intent.DANGER} role="alert" compact style={{ marginBottom: 12 }}>{error}</Callout>}
        <FormGroup label="Project" labelFor="new-project-id" intent={problem ? Intent.DANGER : undefined}
          helperText={problem ?? <>namespace/name: your user name, or a team's namespace. It never changes: results are stored under it.</>}>
          <InputGroup id="new-project-id" value={id} onChange={e => setId(e.target.value)} autoFocus required spellCheck={false}
            className={Classes.MONOSPACE_TEXT} placeholder="camera-algo/isp" intent={problem ? Intent.DANGER : undefined}/>
        </FormGroup>
        <div aria-live="polite" style={{ minHeight: 24, marginBottom: 12 }}>
          {!problem && typed && (current ? <CheckResult check={current} onUse={setId}/> : isFetching && <span className={Classes.TEXT_MUTED}>Checking…</span>)}
        </div>
        <FormGroup label="Display name" labelFor="new-project-name" labelInfo="(optional)">
          <InputGroup id="new-project-name" value={displayName} onChange={e => setDisplayName(e.target.value)} maxLength={200} placeholder="Image pipeline"/>
        </FormGroup>
        <FormGroup label="Visibility" labelFor="new-project-visibility">
          <HTMLSelect id="new-project-visibility" value={visibility} onChange={e => setVisibility(e.target.value)} options={[
            { value: '', label: "The server's default" },
            { value: 'private', label: "Private: only its members" },
            { value: 'internal', label: "Internal: signed-in users" },
            { value: 'public', label: "Public: anyone who opens QA-Board" },
          ]}/>
        </FormGroup>
        <p className={Classes.TEXT_MUTED} style={{ margin: 0, fontSize: 12 }}>
          Or let the first results create it: <code>qa init</code>, set <code>project.name</code> in <em>qaboard.yaml</em>, then <code>qa run</code>.
          From the shell: <code>qa project create {typed || 'namespace/name'}</code>.
        </p>
      </DialogBody>
      <DialogFooter actions={<>
        <Button text="Cancel" onClick={onClose}/>
        <Button type="submit" intent={Intent.PRIMARY} text="Create" loading={saving} disabled={!!problem || !typed || (current && !current.allowed)}/>
      </>}/>
    </form>
  </Dialog>;
};

// For signed-in people only: the server checks the rest
export const NewProjectButton = props => {
  const user = useUser();
  const [open, setOpen] = useState(false);
  if (!user.is_logged || user.kind === 'service') return null;
  return <>
    <Button icon="add" text="New project" onClick={() => setOpen(true)} {...props}/>
    {open && <NewProjectDialog isOpen onClose={() => setOpen(false)}/>}
  </>;
};
