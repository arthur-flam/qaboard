// The commit page's header: what we compare, one row per side, like a chart's legend.
//
//   ⇅  ● new        Denoise: lower the threshold in dark areas · Dana · 2 hours ago   CI 47 · 1 failed   ⌕ Filter   …
//   vs ● develop    Latest on develop · 5 hours ago                                    CI 48               ⌕ Filter   …  +
//
// - Every row has the same columns: the side's name, why it's there (src/comparison/provenance.js), its batch,
//   its filter, its actions. References are called by why they're there ("develop", "v2.3 release", "a3f9c21").
// - The first column has the actions people looked for in menus: ⇅ swaps new and the reference compared,
//   the radio buttons say which reference the metrics and viewers compare with (r / shift r: the next / previous).
// - With several references, each has its row and stays there: selecting one doesn't move it. Past 3 references,
//   the others are listed in a last row, the one compared is always shown.
// - Rows can be dragged: on the new results to swap them, on another reference to move before it.
// - The references compared but not selected are shown in the image viewers with the selected one (`together`):
//   their eye toggles it. Their batch and filter change once selected (they're loaded without their runs).
import { useEffect, useState } from "react";
import styled from "styled-components";
import { useQueryClient } from "@tanstack/react-query";
import { Button, Classes, Icon, Menu, MenuDivider, MenuItem, Navbar, PopoverNext, Tag, Tooltip } from "@blueprintjs/core";
import { DateTime } from "luxon";

import { CommitNavbar } from "./CommitNavbar";
import { SideLabel } from "./SideLabel";
import { CommitAvatar } from "./avatars";
import { AddReferencePanel, useAddMenu, usePrefetchNeighbors } from "./AddReference";
import { is_typing } from "./HelpSheet";
import { ledger_padding, ledger_row_height, shows_reference_bar, visible_references } from "./layout";
import { sider_width } from "../AppSider";
import { useComparison, updateSelected } from "../hooks";
import { usePrefsStore } from "../stores/prefs";
import {
  add_reference, duplicate_reference, move_reference, remove_reference, rename_reference, set_anchor, set_together_mode,
  show_reference, swap_with_top, switch_new_and_reference, toggle_together,
} from "../selection";
import { commitQuery } from "../api/queries";
import { errorMessage } from "../api/http";
import { results_project } from "../milestones/api";
import { available_batch } from "../selectors/batches";
import { reference_branch_of } from "../defaults";
import { pretty_label } from "../utils";
import { toaster } from "../toaster";
import { useCommitTitleParts, commit_title, runs_status, runs_summary } from "../pageTitle";


const Ledger = styled(Navbar)`
  position: fixed !important;
  top: 0 !important;
  height: auto !important;
  padding: ${ledger_padding}px 12px ${ledger_padding}px calc(${sider_width} + 8px) !important;
  display: grid;
  grid-template-columns: 30px minmax(60px, max-content) minmax(0, 1fr) minmax(110px, max-content) minmax(140px, 220px) max-content;
  column-gap: 10px;
  align-items: center;
  container: commit-header / inline-size;

  .qa-row {
    grid-column: 1 / -1;
    display: grid;
    grid-template-columns: subgrid;
    align-items: center;
    height: ${ledger_row_height}px;
    margin: 0 -6px;
    padding: 0 6px;
    border-radius: 4px;
    position: relative;
  }
  .qa-row + .qa-row::before {
    content: "";
    position: absolute;
    top: 0;
    left: 6px;
    right: 6px;
    border-top: 1px solid var(--qa-border);
    opacity: .6;
  }
  .qa-row.is-selected {
    background: color-mix(in srgb, var(--side-color) 7%, transparent);
  }
  .qa-row.is-other .qa-side-label-wrap {
    opacity: .75;
  }
  .qa-row.is-dragged {
    opacity: .4;
  }
  .qa-row.drop-swap {
    outline: 2px dashed var(--qa-intent-primary);
    outline-offset: -2px;
  }
  .qa-row.drop-before {
    box-shadow: 0 -2px 0 var(--qa-intent-primary);
  }
  .qa-row-slot {
    display: grid;
    place-items: center;
    color: var(--qa-text-muted);
    font-size: 12px;
  }
  .qa-row-label {
    display: flex;
    min-width: 0;
  }
  .qa-row-label > * {
    margin-right: 0 !important;
  }
  .qa-row-context {
    display: flex;
    align-items: center;
    min-width: 0;
    white-space: nowrap;
    overflow: hidden;
    color: var(--qa-text-muted);
  }
  .qa-row-context b {
    color: var(--qa-text);
    font-weight: 600;
  }
  .qa-row-context .qa-sep {
    margin: 0 6px;
    opacity: .6;
  }
  .qa-row-context .qa-text {
    overflow: hidden;
    text-overflow: ellipsis;
    min-width: 0;
  }
  .qa-row-batch {
    min-width: 0;
    display: flex;
  }
  .qa-row-batch .bp6-button {
    margin-left: -8px;
  }
  .qa-row-filter {
    min-width: 0;
  }
  .qa-row-actions {
    display: flex;
    align-items: center;
    justify-content: flex-end;
    gap: 0;
  }
  .qa-row-hover {
    opacity: 0;
  }
  .qa-row:hover .qa-row-hover, .qa-row:focus-within .qa-row-hover {
    opacity: 1;
  }
  .qa-radio {
    width: 16px;
    height: 16px;
    border-radius: 50%;
    border: 1.5px solid var(--qa-border);
    background: var(--qa-surface);
    padding: 0;
    cursor: pointer;
    display: grid;
    place-items: center;
  }
  .qa-radio:hover {
    border-color: var(--side-color);
  }
  .qa-radio[aria-checked="true"] {
    border-color: var(--side-color);
  }
  .qa-radio[aria-checked="true"]::after {
    content: "";
    width: 8px;
    height: 8px;
    border-radius: 50%;
    background: var(--side-color);
  }
  .qa-swap {
    width: 24px;
    height: 24px;
    min-width: 24px;
    min-height: 24px;
    border-radius: 50%;
  }
  .qa-more-row {
    height: 30px;
  }
  .qa-more-row .qa-row-context {
    grid-column: 2 / -1;
    gap: 6px;
  }
  @container commit-header (max-width: 1000px) {
    .qa-row-filter .bp6-input-group {
      max-width: 160px;
    }
    .qa-hide-narrow {
      display: none;
    }
  }
`;

const ago = date => date ? DateTime.fromISO(date, { zone: 'utc' }).toRelative() : null;
const Sep = () => <span className="qa-sep">·</span>;

// What we say about the new commit: its message, who and when, its branch unless it's the reference branch
const NewContext = ({ commit, project, reference_branch }) => {
  if (!commit?.message) return null;
  return <>
    <CommitAvatar size='18px' commit={commit} project={project} style={{ marginRight: '6px', flex: 'none' }} />
    <span className="qa-text" style={{ color: 'var(--qa-text)', flex: '0 1 auto' }} title={commit.message}>{commit.message}</span>
    {!!commit.branch && commit.branch !== reference_branch && <span className="qa-hide-narrow" style={{ flex: 'none' }}><Sep/><Icon icon="git-branch" size={12} style={{ verticalAlign: '-1px', marginRight: 3 }}/>{commit.branch}</span>}
    <span className="qa-hide-narrow" style={{ flex: 'none' }}><Sep/>{ago(commit.authored_datetime)}</span>
  </>;
};

// Why a reference is there, in words
const why_text = (origin, commit, new_label) => {
  const when = ago(commit?.authored_datetime);
  switch (origin?.kind) {
    case 'loading': return <span className={Classes.SKELETON}>Latest on the reference branch</span>;
    case 'milestone': return <><Icon icon="star" size={12} style={{ color: 'var(--qa-export-settings)', marginRight: 5 }}/><b>Milestone</b>{when && <><Sep/>{when}</>}</>;
    case 'latest': return <><b>Latest on {origin.branch}</b>{when && <><Sep/>{when}</>}</>;
    case 'behind': return <><b>On {origin.branch}</b>, before its latest commit{when && <><Sep/>{when}</>}</>;
    case 'earlier': return <><b>Earlier on this branch</b>{when && <><Sep/>{when}</>}<Sep/><span className="qa-text">{commit?.message}</span></>;
    case 'same-commit': return <><b>Same commit as {new_label}</b>, another batch or filter</>;
    case 'same-runs': return <><b>The same runs as {new_label}</b>: nothing to compare</>;
    default: return commit?.message
      ? <><span className="qa-text" style={{ color: 'var(--qa-text)' }} title={commit.message}>{commit.message}</span><span className="qa-hide-narrow" style={{ flex: 'none' }}><Sep/><Icon icon="git-branch" size={12} style={{ verticalAlign: '-1px', marginRight: 3 }}/>{commit.branch}<Sep/>{when}</span></>
      : null;
  }
};

// A reference that isn't the one compared: what it is, and actions. Its batch and filter change once it's selected.
const OtherReference = ({ reference, new_label, actions, together_mode, drag, drop, last, add_button }) => {
  const batch_label = reference.commit?.batches ? available_batch(reference.commit, reference.batch) : reference.batch;
  const batch = reference.commit?.batches?.[batch_label];
  const runs = batch ? (batch.valid_outputs ?? 0) : undefined;
  return <div
    className={`qa-row is-other ${drop ? `drop-${drop}` : ''}`}
    style={{ '--side-color': reference.color.css }}
    {...drag}
  >
    <span className="qa-row-slot">
      <Tooltip content="Compare with this one: the metrics and the viewers" hoverOpenDelay={300}>
        <button className="qa-radio" role="radio" aria-checked="false" aria-label={`Compare with ${reference.display_label}`} onClick={actions.show}/>
      </Tooltip>
    </span>
    <span className="qa-row-label qa-side-label-wrap">
      <SideLabel color={reference.color.css} label={reference.label && !reference.milestone ? reference.label : ''} fallback={reference.default_label} onRename={actions.rename}/>
    </span>
    <span className="qa-row-context">{why_text(reference.origin, reference.commit, new_label)}</span>
    <span className="qa-row-batch">
      <Button size="small" variant="minimal" onClick={actions.show} style={{ marginLeft: -8 }} title="Select it to change its batch">
        <span style={{ fontWeight: 500 }}>{pretty_label({ label: batch_label || 'default' })}</span>
        {runs !== undefined && <span className={Classes.TEXT_MUTED} style={{ fontSize: 12, marginLeft: 6 }}>{runs} run{runs === 1 ? '' : 's'}</span>}
      </Button>
    </span>
    <span className="qa-row-filter">
      {reference.filter ? <Tag minimal icon="filter" title="Select it to change its filter" interactive onClick={actions.show}>{reference.filter}</Tag> : null}
    </span>
    <span className="qa-row-actions">
      {together_mode === 'all' && <Tooltip content={reference.ticked ? "Shown in the image viewers with the reference selected: click to hide it" : "Hidden from the image viewers: click to show it"} hoverOpenDelay={300}>
        <Button size="small" variant="minimal" icon={reference.ticked ? 'eye-open' : 'eye-off'} className={Classes.TEXT_MUTED} aria-label={reference.ticked ? `Hide ${reference.display_label} from the image viewers` : `Show ${reference.display_label} in the image viewers`} onClick={actions.toggle}/>
      </Tooltip>}
      <PopoverNext placement="bottom-end" content={<Menu>
        <MenuItem icon="selection" text="Compare with this one" onClick={actions.show}/>
        <MenuItem icon="swap-vertical" text={`Swap with ${new_label}`} disabled={!actions.swap} onClick={actions.swap}/>
        <MenuItem icon="duplicate" text="Compare with a copy" label="to change its batch or filter" onClick={actions.duplicate}/>
        <MenuDivider/>
        <MenuItem icon={together_mode === 'all' ? 'layers' : 'grid-view'} text={together_mode === 'all' ? 'Show one reference at a time' : 'Show the references together'} label="image viewers" onClick={actions.toggleMode}/>
        <MenuItem icon="cross" text="Remove" onClick={actions.remove}/>
      </Menu>}>
        <Button size="small" variant="minimal" icon="more" className={Classes.TEXT_MUTED} aria-label={`Actions on ${reference.display_label}`}/>
      </PopoverNext>
      {last && add_button}
    </span>
  </div>;
};


export const ComparisonHeader = () => {
  const queryClient = useQueryClient();
  const comparison = useComparison();
  const { project, project_data, selected, selected_views, new_commit, ref_commit, new_batch, ref_batch, references, compared, together_mode } = comparison;
  const setReferencesTogether = usePrefsStore(state => state.setReferencesTogether);
  const adding = useAddMenu(state => state.open);
  const setAdding = useAddMenu(state => state.setOpen);
  const [dragging, setDragging] = useState(null);
  const [drop, setDrop] = useState(null);
  const update = attribute => e => {
    const value = (e?.target && e.target.value !== undefined) ? e.target.value : e;
    // typing filters shouldn't add browser history entries
    updateSelected({ [attribute]: value }, { replace: attribute.startsWith('filter') });
  };
  const show_refs = shows_reference_bar(selected_views);
  const [new_side, ref_side] = compared;
  const several = references.length > 1;
  const reference_branch = reference_branch_of(project_data);
  usePrefetchNeighbors(references, selected.ref_index, show_refs && several && !!ref_commit?.is_loaded);
  // the link to the page, as it pastes in chats: its name, the commit, the runs
  const title_parts = useCommitTitleParts();
  const link = { text: commit_title(title_parts), details: [title_parts.message, runs_summary(runs_status(title_parts.batch))].filter(Boolean).join(' · ') };
  // e.g. the latest commit of the reference branch, compared to itself
  const same_as_new = !!new_commit?.id && new_commit.id === ref_commit?.id
    && (selected.new_project ?? project) === (selected.ref_project ?? project)
    && new_batch?.label === ref_batch?.label
    && (selected.filter_batch_new ?? '') === (selected.filter_batch_ref ?? '');

  // the actions need the commits' ids, e.g. the default reference's
  const resolved = { ...selected, references, new_commit_id: selected.new_commit_id || new_commit?.id || null };
  const show = index => updateSelected({
    ...show_reference(resolved, (index + references.length) % references.length),
    // together, the reference selected is what the images are compared with
    ...(together_mode === 'all' ? set_anchor(resolved, 'reference') : {}),
  });
  const can_swap = index => !!references[index]?.commit_id && !!resolved.new_commit_id;
  const setTogetherMode = mode => {
    updateSelected(set_together_mode(resolved, mode));
    setReferencesTogether(mode === 'all');
  };
  const add = side => {
    setAdding(false);
    updateSelected(add_reference(resolved, side));
  };
  const addBranch = async branch => {
    try {
      const commit = await queryClient.fetchQuery(commitQuery({ project, branch }));
      queryClient.setQueryData(commitQuery({ project, id: commit.id }).queryKey, commit);
      add({ project, commit_id: commit.id });
    } catch (error) {
      toaster.show({ message: `Could not find the latest commit on ${branch}: ${errorMessage(error)}`, intent: 'danger' });
    }
  };
  const add_actions = {
    add,
    addBranch,
    addMilestone: milestone => {
      if (!milestone.commit && milestone.branch) return addBranch(milestone.branch);
      add({ project: results_project(milestone), commit_id: milestone.commit, batch: milestone.batch || 'default', filter: milestone.filter ?? '' });
    },
    duplicate: index => {
      setAdding(false);
      updateSelected(duplicate_reference(resolved, index, references[index].display_label));
    },
  };

  // r / shift r: the next / previous reference
  useEffect(() => {
    if (!show_refs || !several) return;
    const onKeyDown = event => {
      if (event.key.toLowerCase() !== 'r' || event.ctrlKey || event.metaKey || event.altKey || event.defaultPrevented || event.repeat) return;
      if (is_typing(event.target)) return;
      event.preventDefault();
      show(selected.ref_index + (event.shiftKey ? -1 : 1));
    };
    window.addEventListener('keydown', onKeyDown);
    return () => window.removeEventListener('keydown', onKeyDown);
  });

  // Drag a reference's row on the new results' to swap them, on another reference's to move it before it
  const drag_props = key => ({
    draggable: show_refs,
    onDragStart: event => {
      // not when selecting text, or from the inputs and buttons
      if (event.target.closest?.('input, button, a, .bp6-popover-target') && event.target !== event.currentTarget) return event.preventDefault();
      event.dataTransfer.effectAllowed = 'move';
      event.dataTransfer.setData('text/plain', String(key));
      setDragging(key);
    },
    onDragOver: event => {
      if (dragging === null || dragging === key) return;
      const swapping = dragging === 'new' || key === 'new';
      if (swapping && !can_swap(key === 'new' ? dragging : key)) return;
      event.preventDefault();
      const kind = swapping ? 'swap' : 'before';
      if (drop?.key !== key || drop?.kind !== kind) setDrop({ key, kind });
    },
    onDrop: event => {
      event.preventDefault();
      if (dragging !== null && drop) {
        if (drop.kind === 'swap') updateSelected(swap_with_top(resolved, drop.key === 'new' ? dragging : drop.key));
        else updateSelected(move_reference(resolved, dragging, drop.key > dragging ? drop.key - 1 : drop.key));
      }
      setDragging(null);
      setDrop(null);
    },
    onDragEnd: () => {
      setDragging(null);
      setDrop(null);
    },
  });
  const drop_on = key => drop?.key === key && dragging !== key ? drop.kind : undefined;
  const row_class = key => [dragging === key && 'is-dragged', drop_on(key) && `drop-${drop_on(key)}`].filter(Boolean).join(' ');

  const add_button = <PopoverNext
    content={<AddReferencePanel actions={add_actions} can_duplicate={!!references[selected.ref_index]?.commit_id}/>}
    isOpen={adding}
    onInteraction={setAdding}
    placement="bottom-end"
  >
    <Tooltip content="Compare with another reference too: a branch, a milestone, a commit" hoverOpenDelay={300}>
      <Button size="small" variant="outlined" icon="plus" aria-label="Compare with another reference" style={{ marginLeft: 6, borderRadius: 12 }}/>
    </Tooltip>
  </PopoverNext>;

  const { shown, hidden } = visible_references(references, selected.ref_index);
  const last_shown = shown[shown.length - 1];
  const new_label = new_side.label;

  return <Ledger className="qa-comparison-header" role="region" aria-label="What we compare">
    <div className={`qa-row ${row_class('new')}`} style={{ '--side-color': new_side.color.css }} {...drag_props('new')}>
      <CommitNavbar
        update={update}
        loading={!new_commit?.is_loaded}
        commit={new_commit}
        batch={new_batch}
        filter={selected.filter_batch_new}
        project={project}
        project_data={project_data}
        selected={selected}
        type="new"
        side={new_side}
        link={link}
        slot={show_refs && <Tooltip content={`Swap ${new_label} and ${ref_side.label}`} hoverOpenDelay={300}>
          <Button className="qa-swap" size="small" variant="outlined" icon="swap-vertical" aria-label={`Swap ${new_label} and ${ref_side.label}`} onClick={() => updateSelected(switch_new_and_reference(selected))}/>
        </Tooltip>}
        context={<NewContext commit={new_commit} project={selected.new_project ?? project} reference_branch={reference_branch}/>}
      />
    </div>
    {show_refs && shown.map(index => {
      const reference = references[index];
      if (!reference.is_shown) return <OtherReference
        key={reference.id}
        reference={reference}
        new_label={new_label}
        together_mode={together_mode}
        drag={drag_props(index)}
        drop={drop_on(index)}
        last={index === last_shown && hidden.length === 0}
        add_button={add_button}
        actions={{
          show: () => show(index),
          rename: label => updateSelected(rename_reference(resolved, index, label)),
          toggle: () => updateSelected(toggle_together(resolved, index)),
          toggleMode: () => setTogetherMode(together_mode === 'all' ? 'one' : 'all'),
          duplicate: () => updateSelected(duplicate_reference(resolved, index, reference.display_label)),
          remove: () => updateSelected(remove_reference(resolved, index)),
          swap: can_swap(index) ? () => updateSelected(swap_with_top(resolved, index)) : undefined,
        }}
      />;
      return <div key={reference.id} className={`qa-row ${several ? 'is-selected' : ''} ${row_class(index)}`} style={{ '--side-color': ref_side.color.css }} {...drag_props(index)}>
        <CommitNavbar
          update={update}
          loading={!ref_commit?.is_loaded}
          commit={ref_commit}
          batch={ref_batch}
          filter={selected.filter_batch_ref}
          project={project}
          project_data={project_data}
          selected={selected}
          type="ref"
          same_as_new={same_as_new && reference.origin?.kind !== 'same-runs'}
          references={references}
          side={ref_side}
          slot={several
            ? <button className="qa-radio" role="radio" aria-checked="true" aria-label={`Comparing with ${ref_side.label}`} title="The metrics and the viewers compare with this one. r: the next one"/>
            : <span aria-hidden>vs</span>}
          context={why_text(reference.origin, ref_commit, new_label)}
          extra={index === last_shown && hidden.length === 0 ? add_button : null}
        />
      </div>;
    })}
    {show_refs && hidden.length > 0 && <div className="qa-row qa-more-row">
      <span className="qa-row-slot"/>
      <span className="qa-row-context">
        {hidden.map(index => <Tooltip key={references[index].id} content={<span>Compare with it. {references[index].commit?.message}</span>} hoverOpenDelay={300}>
          <button style={{ cursor: 'pointer', border: 0, background: 'none', padding: 0 }} aria-label={`Compare with ${references[index].display_label}`} onClick={() => show(index)}>
            <SideLabel color={references[index].color.css} label="" fallback={references[index].display_label} editable={false}/>
          </button>
        </Tooltip>)}
        <span>{hidden.length} more reference{hidden.length === 1 ? '' : 's'}: click one to compare with it</span>
        <span style={{ marginLeft: 'auto' }}>{add_button}</span>
      </span>
    </div>}
  </Ledger>;
};

// For the tests: what the header says about a reference
export { why_text };
