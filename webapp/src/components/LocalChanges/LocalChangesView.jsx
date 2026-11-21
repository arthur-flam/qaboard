// The sidebar's "Local changes": the distinct code states the batch's runs ran, with how many runs each.
import { useState } from "react";
import { Button, Classes, HTMLTable, NonIdealState, Tag } from "@blueprintjs/core";

import { LocalChangesDialogLoader } from "./LocalChangesTag";
import { changes_sentence, saved_file, short_sha, unpushed_text } from "./localChanges";

const max_runs_listed = 50;
const run_name = output => output.test_input_path || `run ${output.id}`;

const kind_text = group => group.kind === 'changes'
  ? `Uncommitted changes: ${changes_sentence(group.changes)}`
  : group.kind === 'clean' ? 'No local changes' : "Unknown: git couldn't tell";

const GroupRow = ({ group, ref_batch }) => {
  const [expanded, setExpanded] = useState(false);
  const [open, setOpen] = useState(false);
  const { changes, kind, count, outputs, output } = group;
  const unpushed = unpushed_text(changes);
  const viewable = kind !== 'clean' || !!saved_file(changes, 'unpushed');
  return <>
    <tr>
      <td>
        <Tag minimal intent={kind === 'changes' ? 'warning' : 'none'} icon={kind === 'changes' ? 'git-new-branch' : kind === 'clean' ? 'git-commit' : 'help'}>
          {kind_text(group)}
        </Tag>
      </td>
      <td><code>{short_sha(changes.head) || '?'}</code>{changes.branch && <> on <code>{String(changes.branch)}</code></>}</td>
      <td>{unpushed || (changes.pushed === true ? 'pushed' : <span className={Classes.TEXT_MUTED}>unknown</span>)}</td>
      <td style={{ whiteSpace: 'nowrap' }}>
        <Button size="small" variant="minimal" icon={expanded ? 'chevron-up' : 'chevron-down'} aria-expanded={expanded}
          text={`${count} run${count === 1 ? '' : 's'}`} onClick={() => setExpanded(!expanded)}/>
      </td>
      <td>{viewable && <Button size="small" icon="eye-open" text="View" aria-label={`View: ${kind_text(group)}`} onClick={() => setOpen(true)}/>}
        <LocalChangesDialogLoader isOpen={open} onClose={() => setOpen(false)} output={output} output_ref={ref_batch?.outputs?.[output.reference_id]}/>
      </td>
    </tr>
    {expanded && <tr>
      <td colSpan={5}>
        <ul className={Classes.LIST} style={{ margin: 0 }}>
          {outputs.slice(0, max_runs_listed).map(o => <li key={o.id}>{run_name(o)}</li>)}
        </ul>
        {count > max_runs_listed && <p className={Classes.TEXT_MUTED}>and {count - max_runs_listed} more</p>}
      </td>
    </tr>}
  </>;
};

// groups: group_local_changes(the runs shown)
const LocalChangesView = ({ groups, ref_batch }) => {
  if (!groups?.has_data)
    return <NonIdealState icon="git-new-branch" title="No local changes recorded" description="Runs started from your computer record their code changes. CI runs and older runs don't."/>;
  return <>
    <p>
      {groups.shared !== null ? 'The runs shown ran the same code.' : `The runs shown recorded ${groups.groups.length} different set${groups.groups.length === 1 ? '' : 's'} of local changes.`}
      {groups.missing > 0 && ` ${groups.missing} run${groups.missing === 1 ? '' : 's'} didn't record their local changes.`}
    </p>
    <HTMLTable compact striped style={{ width: '100%' }}>
      <thead><tr><th>Changes</th><th>Commit</th><th>Pushed</th><th>Runs</th><th><span className={Classes.TEXT_MUTED}>Details</span></th></tr></thead>
      <tbody>
        {groups.groups.map(group => <GroupRow key={group.key} group={group} ref_batch={ref_batch}/>)}
      </tbody>
    </HTMLTable>
  </>;
};

export default LocalChangesView;
