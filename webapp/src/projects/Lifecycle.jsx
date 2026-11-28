// The Access page's "Project" section: who created it and when, its namespace and repositories; owners archive it
// (hidden from lists, refuses new results) and unarchive it.
import { useState } from "react";
import { useQuery, useQueryClient } from "@tanstack/react-query";
import { Alert, Button, Callout, Classes, Intent } from "@blueprintjs/core";

import { errorMessage } from "../api/http";
import { toaster } from "../toaster";
import { Time } from "../settings/format";
import { lifecycleQuery, setArchived } from "./api";


const via_text = { api: "with New project", ingest: "with its first results", webhook: "by a push to its repository" };

export const ProjectLifecycle = ({ project }) => {
  const queryClient = useQueryClient();
  const { data } = useQuery(lifecycleQuery(project));
  const [confirming, setConfirming] = useState(false);
  const [saving, setSaving] = useState(false);
  // older servers, projects users can't read: the rest of the page says it
  if (!data) return null;

  const change = archived => {
    setSaving(true);
    setArchived(queryClient, project, archived)
      .then(() => toaster.show({ message: archived ? `Archived ${project}` : `Unarchived ${project}`, intent: Intent.SUCCESS, icon: 'tick', timeout: 3000 }))
      .catch(error => toaster.show({ message: errorMessage(error), intent: Intent.DANGER, timeout: 8000 }))
      .finally(() => { setSaving(false); setConfirming(false); });
  };

  const created = data.created_at
    ? <>Created <Time iso={data.created_at}/>{data.created_by ? <> by <strong>{data.created_by}</strong></> : null} {via_text[data.created_via] ?? ''}.</>
    : <>Created before QA-Board recorded who creates projects.</>;
  const namespace = data.namespace
    ? <> In the {data.namespace.kind === 'user' ? 'personal namespace' : 'namespace'} <strong>{data.namespace.path}</strong>.</>
    : null;
  const repositories = data.repositories ?? [];
  return <section aria-label="Project">
    <h4 className={Classes.HEADING}>Project</h4>
    {data.archived && <Callout intent={Intent.WARNING} icon="archive" compact style={{ maxWidth: 720, marginBottom: 8 }}>
      Archived <Time iso={data.archived_at}/>{data.archived_by ? ` by ${data.archived_by}` : ''}: it's hidden from the lists of projects and refuses new results.
    </Callout>}
    <p style={{ margin: '0 0 4px' }}>{created}{namespace}</p>
    {repositories.length > 0 && <p className={Classes.TEXT_MUTED} style={{ margin: '0 0 8px' }}>
      Repository: {repositories.map(r => `${r.git_host ? `${r.git_host}:` : ''}${r.repo_path}${r.subdir ? ` (${r.subdir})` : ''}`).join(', ')}
    </p>}
    {data.can_archive && (data.archived
      ? <Button icon="unarchive" text="Unarchive" loading={saving} onClick={() => change(false)}/>
      : <Button icon="archive" text="Archive…" loading={saving} onClick={() => setConfirming(true)}/>)}
    <Alert isOpen={confirming} icon="archive" intent={Intent.WARNING} confirmButtonText="Archive" cancelButtonText="Cancel"
      onConfirm={() => change(true)} onCancel={() => setConfirming(false)} loading={saving}>
      <p>Archive <strong>{project}</strong>? It's hidden from the lists of projects, and new results are refused. Its results stay: owners can unarchive it.</p>
    </Alert>
  </section>;
};
