// Above the runs of local batches: which code they ran, said once for all the runs when they share it.
import { useState } from "react";
import { Button, Callout, Intent } from "@blueprintjs/core";

import { updateSelected } from "../../hooks";
import { LocalChangesDialogLoader } from "./LocalChangesTag";
import { changes_sentence, local_changes_view, saved_file, short_sha, unpushed_text } from "./localChanges";

const showLocalChangesView = () => updateSelected({ selected_views: local_changes_view });

// groups: group_local_changes(the runs shown); find_reference: output => its reference run, for the dialog's comparison
export const LocalChangesCallout = ({ groups, find_reference }) => {
  const [open, setOpen] = useState(false);
  if (!groups?.has_data) return null;
  if (!groups.single) {
    const n = groups.groups.length;
    const title = n > 1 ? `These local runs used ${n} different sets of local changes` : 'Only some of these runs recorded their local changes';
    const inexact = groups.groups.some(group => !group.exact);
    return <Callout icon="git-new-branch" intent={Intent.PRIMARY} title={title}>
      {groups.missing > 0 && <p>{groups.missing} of them didn't record their code changes.</p>}
      {inexact && <p>Runs that saved only a summary of their changes, or whose changes are unknown, can't be told apart: each counts as its own set.</p>}
      <Button size="small" icon="list-detail-view" text="View" onClick={showLocalChangesView}/>
    </Callout>;
  }
  const { changes, output, kind } = groups.groups[0];
  const sha = <code>{short_sha(changes.head) || '?'}</code>;
  const unpushed = unpushed_text(changes);
  const unpushed_suffix = unpushed ? ` · ${unpushed}` : '';
  const viewable = kind !== 'clean' || !!saved_file(changes, 'unpushed');
  const title = kind === 'changes'
    ? <>Local runs on {sha} with uncommitted changes: {changes_sentence(changes)}{unpushed_suffix}</>
    : kind === 'clean'
      ? <>Local runs on {sha}, without local changes{unpushed_suffix}</>
      : <>Local runs on {sha}: git couldn't tell whether they had local changes{unpushed_suffix}</>;
  return <Callout
    icon={kind === 'changes' ? 'git-new-branch' : kind === 'clean' ? 'git-commit' : 'help'}
    intent={kind === 'changes' ? Intent.PRIMARY : Intent.NONE}
    title={title}
  >
    {viewable && <Button size="small" icon="eye-open" text={kind === 'changes' ? 'View changes' : 'View'} onClick={() => setOpen(true)}/>}
    <LocalChangesDialogLoader isOpen={open} onClose={() => setOpen(false)} output={output} output_ref={find_reference?.(output)}/>
  </Callout>;
};
