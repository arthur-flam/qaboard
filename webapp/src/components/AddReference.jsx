// Adding references to compare with (the comparison header's "+", components/ComparisonHeader.jsx), and loading the
// references around the one compared, so that "r" shows them at once.
import { useEffect, useState } from "react";
import { useQueryClient } from "@tanstack/react-query";
import { create } from "zustand";
import { Button, Classes, InputGroup, MenuDivider, MenuItem } from "@blueprintjs/core";

import { reference_branch_of } from '../defaults';
import { MilestonesPanel } from "../milestones/MilestonesPanel";
import { commitQuery, nb_outputs } from "../api/queries";
import { useComparison, useSiteConfig, updateSelected } from "../hooks";
import { remove_reference } from "../selection";
import { history } from "../router";
import { toaster } from "../toaster";


// The "+" menu is open: other menus open it too ("Stack another reference…")
export const useAddMenu = create(set => ({ open: false, setOpen: open => set({ open }) }));
export const openAddReference = () => useAddMenu.getState().setOpen(true);

// Bigger commits take long to download and much memory: we load them only when users want to see them
const prefetch_max_outputs = 5000;

// Once the reference shown is loaded and the page idle, the references around it, with their outputs
export function usePrefetchNeighbors(references, ref_index, ready) {
  const queryClient = useQueryClient();
  const n = references.length;
  const neighbors = [...new Set([(ref_index + 1) % n, (ref_index - 1 + n) % n])]
    .filter(index => index !== ref_index)
    .map(index => references[index])
    // we know how big they are once we have them without their outputs
    .filter(r => !!r.commit_id && !!r.commit?.batches && nb_outputs(r.commit) <= prefetch_max_outputs);
  const key = neighbors.map(r => `${r.project}/${r.commit_id}`).join(' ');
  useEffect(() => {
    if (!ready || !key || navigator.connection?.saveData) return;
    const idle = window.requestIdleCallback ?? (callback => setTimeout(callback, 200));
    const cancel = window.cancelIdleCallback ?? clearTimeout;
    const handle = idle(() => {
      for (const id of key.split(' ')) {
        const slash = id.lastIndexOf('/');
        queryClient.prefetchQuery(commitQuery({ project: id.slice(0, slash), id: id.slice(slash + 1) }));
      }
    }, { timeout: 5000 });
    return () => cancel(handle);
  }, [queryClient, ready, key]);
}

// Removes a reference, and says so with a way to undo it (the browser's back button does it too)
export const removeReference = (selected, index, name) => {
  const changes = remove_reference(selected, index);
  if (Object.keys(changes).length === 0) return;
  const before = `${window.location.pathname}${window.location.search}${window.location.hash}`;
  updateSelected(changes);
  toaster.show({
    message: `Removed ${name ?? 'the reference'} from the comparison`,
    icon: 'cross',
    action: { text: 'Undo', icon: 'undo', onClick: () => history.push(before) },
  }, 'remove-reference');
};

// Commit ids are hexadecimal, the rest are branches (or tags)
const is_commit_id = text => /^[0-9a-f]{6,40}$/i.test(text);

export const AddReferencePanel = ({ actions, can_duplicate }) => {
  const { docs_root } = useSiteConfig();
  const { project, project_data, references, selected } = useComparison();
  const [text, setText] = useState('');
  const shown = references[selected.ref_index];
  const reference_branch = reference_branch_of(project_data);
  const submit = () => {
    const value = text.trim();
    if (!value) return;
    if (is_commit_id(value)) actions.add({ project, commit_id: value });
    else actions.addBranch(value);
    setText('');
  };
  return <MilestonesPanel
    project={project}
    project_data={project_data}
    docs_root={docs_root}
    selected={{ project: shown.project, commit: shown.commit_id, batch: shown.batch, filter: shown.filter }}
    onSelect={actions.addMilestone}
    before={<>
      <MenuDivider title="Compare with another reference too"/>
      <li className={Classes.TEXT_MUTED} style={{ fontSize: 12, padding: '0 7px 6px', lineHeight: 1.4 }}>
        Compare with several references: the image viewer shows them together. The metrics and the other viewers compare with the one you select: its radio button, or <kbd className={Classes.KEY}>r</kbd>.
      </li>
      <li style={{ padding: '2px 0 6px' }}>
        <InputGroup
          leftIcon="git-commit"
          placeholder="Commit or branch"
          aria-label="Add a commit or a branch as reference"
          value={text}
          onChange={e => setText(e.target.value)}
          onKeyDown={e => e.key === 'Enter' && submit()}
          rightElement={<Button variant="minimal" icon="plus" aria-label="Add" disabled={!text.trim()} onClick={submit}/>}
        />
      </li>
      <MenuItem text={reference_branch} label="reference branch" icon="git-branch" onClick={() => actions.addBranch(reference_branch)}/>
      {can_duplicate && <MenuItem text={`A copy of ${shown.display_label}`} label="with another batch or filter" icon="duplicate" onClick={() => actions.duplicate(selected.ref_index)}/>}
    </>}
  />;
};
