// The commit page's header: what we compare, one row per side, like a chart's legend.
//
//   ⇅  ● new      7e3b91c  Denoise: lower the threshold in dark areas · Dana, 2 hours ago    CI 47 · 1 failed   ⌕ Filter   …
//   vs ● develop  a3f9c21  Latest on develop · Merge "AWB: faster" · Noa, 5 hours ago        CI 48               ⌕ Filter   …  +
//
// - Every row has the same columns: the side's name, its commit, why it's there (src/comparison/provenance.js), its
//   batch, its filter, its actions. References are called by why they're there ("develop", "v2.3 release", "a3f9c21").
// - The first column has the actions people looked for in menus: ⇅ swaps new and the reference compared,
//   the radio buttons say which reference the metrics and viewers compare with (r / shift r: the next / previous).
// - With several references, each has its row and stays there: selecting one doesn't move it. Past 4 references, they
//   scroll: 3 rows and a half show, the one compared sticks to the top or bottom edge, a footer says how many are out
//   of sight and shows them all (layout.js).
// - Rows are dragged by their name: on the new results to swap them, on another reference to move it there.
//   Keyboard, on a row: F2 renames it, Delete removes it (with an undo), Alt+↑ / Alt+↓ move it.
// - The references compared but not selected are shown in the image viewers with the selected one (`together`):
//   their eye toggles it. Their batch and filter change once selected (they're loaded without their runs).
import { useEffect, useLayoutEffect, useRef, useState } from "react";
import styled from "styled-components";
import { useQueryClient } from "@tanstack/react-query";
import { Button, Classes, Icon, Menu, MenuDivider, MenuItem, Navbar, PopoverNext, Tag, Tooltip } from "@blueprintjs/core";
import { DateTime } from "luxon";

import { CommitNavbar } from "./CommitNavbar";
import { CommitId } from "./CommitId";
import { SideLabel } from "./SideLabel";
import { CommitAvatar } from "./avatars";
import { AddReferencePanel, removeReference, useAddMenu, usePrefetchNeighbors } from "./AddReference";
import { is_typing } from "./HelpSheet";
import { BatchLine, batch_line_item } from "./tuning/SelectBatches";
import {
  ledger_footer_height, ledger_padding, ledger_row_height, references_height, scrolls,
  shows_reference_bar, useLedgerStore,
} from "./layout";
import { sider_width } from "../AppSider";
import { useComparison, updateSelected } from "../hooks";
import { usePrefsStore } from "../stores/prefs";
import {
  add_reference, duplicate_reference, is_default_name, move_reference, rename_reference, set_anchor, set_together_mode,
  show_reference, swap_with_top, switch_new_and_reference, toggle_together,
} from "../selection";
import { commitQuery } from "../api/queries";
import { errorMessage } from "../api/http";
import { owner_name, results_project } from "../milestones/api";
import { available_batch } from "../selectors/batches";
import { reference_branch_of } from "../defaults";
import { toaster } from "../toaster";
import { useCommitTitleParts, commit_title, runs_status, runs_summary } from "../pageTitle";


const Ledger = styled(Navbar)`
  --qa-ledger-bg: #ffffff;
  .bp6-dark & {
    --qa-ledger-bg: #2f343c;
  }
  position: fixed !important;
  top: 0 !important;
  height: auto !important;
  padding: ${ledger_padding}px 12px ${ledger_padding}px calc(${sider_width} + 8px) !important;
  display: grid;
  /* slot, name, commit, context, batch, filter, actions */
  grid-template-columns: 30px minmax(60px, max-content) max-content minmax(0, 1fr) minmax(110px, max-content) minmax(140px, 220px) max-content;
  column-gap: 10px;
  align-items: center;
  container: commit-header / inline-size;

  .qa-row, .qa-refs {
    grid-column: 1 / -1;
    display: grid;
    grid-template-columns: subgrid;
  }
  .qa-row {
    align-items: center;
    height: ${ledger_row_height}px;
    margin: 0 -6px;
    padding: 0 6px;
    border-radius: 4px;
    position: relative;
  }
  .qa-row:focus-visible {
    outline: 2px solid var(--qa-intent-primary);
    outline-offset: -2px;
  }
  .qa-row + .qa-row::before, .qa-refs::before, .qa-refs .qa-row::before {
    content: "";
    position: absolute;
    top: 0;
    left: 6px;
    right: 6px;
    border-top: 1px solid var(--qa-border);
    opacity: .6;
  }
  .qa-refs .qa-row:first-child::before {
    display: none;
  }
  .qa-refs {
    position: relative;
    margin: 0 -6px;
    padding: 0 6px;
    overflow-y: auto;
    overscroll-behavior: contain;
    scrollbar-width: thin;
  }
  .qa-refs .qa-row {
    margin: 0;
    padding: 0;
  }
  .qa-row.is-selected {
    background: color-mix(in srgb, var(--side-color) 7%, var(--qa-ledger-bg));
  }
  /* while scrolling, the reference compared stays in sight, at the top or bottom edge */
  .qa-refs .qa-row.is-selected {
    position: sticky;
    top: 0;
    bottom: 0;
    z-index: 2;
  }
  .qa-row.is-other .qa-side-label-wrap {
    opacity: .8;
  }
  .qa-row.is-dragged {
    opacity: .4;
  }
  .qa-row.drop-swap {
    outline: 2px dashed var(--qa-intent-primary);
    outline-offset: -2px;
  }
  .qa-row.drop-before {
    box-shadow: inset 0 2px 0 var(--qa-intent-primary);
  }
  .qa-row.drop-after {
    box-shadow: inset 0 -2px 0 var(--qa-intent-primary);
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
  .qa-row-label[draggable="true"] {
    cursor: grab;
  }
  .qa-row-label > * {
    margin-right: 0 !important;
  }
  .qa-sha {
    border: 0;
    background: none;
    padding: 2px 4px;
    margin: 0 -4px;
    border-radius: 3px;
    font-size: 12px;
    color: var(--qa-text-muted);
    cursor: copy;
  }
  .qa-sha:hover {
    color: var(--qa-text);
    background: color-mix(in srgb, var(--qa-text) 8%, transparent);
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
    flex: none;
  }
  .qa-row-context .qa-sep {
    margin: 0 6px;
    opacity: .6;
    flex: none;
  }
  .qa-row-context .qa-text {
    overflow: hidden;
    text-overflow: ellipsis;
    min-width: 0;
    flex: 0 1 auto;
  }
  .qa-row-context .qa-main {
    color: var(--qa-text);
  }
  .qa-row-context .qa-optional {
    flex: none;
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
  .qa-filter-count {
    display: inline-block;
    font-size: 12px;
    line-height: 24px;
    padding: 0 4px;
    color: var(--qa-text-muted);
    white-space: nowrap;
  }
  .qa-filter-count.is-none {
    color: var(--qa-intent-warning, #c87619);
    font-weight: 600;
  }
  .qa-row-actions {
    display: flex;
    align-items: center;
    justify-content: flex-end;
  }
  /* the actions are in the same places on every row: those a row doesn't have leave their place */
  .qa-action-slot {
    display: inline-block;
    width: 24px;
    flex: none;
  }
  .qa-add-slot {
    display: inline-flex;
    justify-content: flex-end;
    width: 32px;
    flex: none;
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
    cursor: default;
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
  .qa-footer {
    grid-column: 1 / -1;
    display: flex;
    align-items: center;
    gap: 10px;
    height: ${ledger_footer_height}px;
    padding-left: 40px;
    font-size: 12px;
    color: var(--qa-text-muted);
    border-top: 1px solid var(--qa-border);
    position: relative;
  }
  /* there's more below: the last rows fade */
  .qa-footer.has-below::before {
    content: "";
    position: absolute;
    left: 0;
    right: 0;
    bottom: 100%;
    height: 18px;
    pointer-events: none;
    background: linear-gradient(transparent, var(--qa-ledger-bg));
  }
  .qa-footer b {
    color: var(--qa-text);
  }
  @container commit-header (max-width: 1000px) {
    .qa-row-filter .bp6-input-group {
      max-width: 160px;
    }
    .qa-hide-narrow, .qa-row-sha {
      display: none;
    }
  }
  @container commit-header (max-width: 1250px) {
    .qa-hide-medium {
      display: none;
    }
  }
`;

const ago = date => date ? DateTime.fromISO(date, { zone: 'utc' }).toRelative() : null;
const Sep = () => <span className="qa-sep">·</span>;
const BranchName = ({ branch }) => <><Icon icon="git-branch" size={12} style={{ verticalAlign: '-1px', marginRight: 3 }}/>{branch}</>;
// "Noa, 5 hours ago"
const WhoWhen = ({ commit, className = 'qa-hide-narrow' }) => {
  const when = ago(commit?.authored_datetime);
  if (!commit?.committer_name && !when) return null;
  return <span className={`qa-optional ${className}`}><Sep/>{[commit?.committer_name, when].filter(Boolean).join(', ')}</span>;
};
const Message = ({ commit }) => commit?.message
  ? <span className="qa-text qa-main" title={commit.message}>{commit.message.split('\n')[0]}</span>
  : null;

// What we say about the new commit: its message, its branch unless it's the reference branch, who and when
const NewContext = ({ commit, project, reference_branch }) => {
  if (!commit?.message) return null;
  return <>
    <CommitAvatar size='18px' commit={commit} project={project} style={{ marginRight: '6px', flex: 'none' }} />
    <Message commit={commit}/>
    {!!commit.branch && commit.branch !== reference_branch && <span className="qa-optional qa-hide-medium"><Sep/><BranchName branch={commit.branch}/></span>}
    <WhoWhen commit={commit}/>
  </>;
};

// What tells milestones apart, among those compared: their group, who saved them (when it's not the same for all)
export const milestone_fields = milestones => {
  const varies = field => milestones.length < 2 || new Set(milestones.map(m => field(m) ?? '')).size > 1;
  return { group: varies(m => m.group), owner: varies(owner_name) };
};

// A milestone: its notes (else its commit's message), then its group, who saved it and when
const MilestoneContext = ({ milestone, commit, fields }) => {
  const notes = milestone.notes?.trim();
  const text = notes ? notes.split('\n')[0] : commit?.message?.split('\n')[0];
  const owner = fields?.owner ? owner_name(milestone) : null;
  const saved = ago(milestone.created_at);
  const title = [notes, commit?.message && `Commit: ${commit.message}`].filter(Boolean).join('\n\n');
  return <>
    <Icon icon="star" size={12} style={{ color: 'var(--qa-export-settings)', marginRight: 6, flex: 'none' }} title="A milestone"/>
    {fields?.group && !!milestone.group && <><span className="qa-optional">{milestone.group}</span><Sep/></>}
    {text ? <span className={`qa-text ${notes ? 'qa-main' : ''}`} title={title}>{text}</span> : <b>Milestone</b>}
    {(owner || saved) && <span className="qa-optional qa-hide-narrow"><Sep/>saved {owner ? `by ${owner} ` : ''}{saved}</span>}
  </>;
};

// Why a reference is there, in words, then its commit
// reference: from useComparison().references. fields: see milestone_fields
const why_text = (reference, commit, new_label, fields) => {
  const origin = reference?.origin;
  if (reference?.milestone) return <MilestoneContext milestone={reference.milestone} commit={commit} fields={fields}/>;
  const then = <><Sep/><Message commit={commit}/><WhoWhen commit={commit}/></>;
  switch (origin?.kind) {
    case 'loading': return <span className={Classes.SKELETON}>Latest on the reference branch</span>;
    case 'error': return <><Tag minimal intent="danger" icon="error" style={{ marginRight: 6 }}>Error</Tag><span className="qa-text" title={String(reference.error ?? '')}>Could not load this commit{reference.error ? `: ${reference.error}` : ''}</span></>;
    case 'latest': return <><b>Latest on {origin.branch}</b>{then}</>;
    case 'behind': return <><b>Older on {origin.branch}</b>{then}</>;
    case 'on-branch': return <><b>On {origin.branch}</b>{then}</>;
    case 'earlier': return <><b>Earlier on this branch</b>{then}</>;
    case 'later': return <><b>Later on this branch</b>{then}</>;
    case 'same-commit': return <><b>Same commit as {new_label}</b>, another batch or filter</>;
    case 'same-runs': return <><b>The same runs as {new_label}</b>: nothing to compare</>;
    default: return commit?.message
      ? <><Message commit={commit}/>{!!commit.branch && <span className="qa-optional qa-hide-medium"><Sep/><BranchName branch={commit.branch}/></span>}<WhoWhen commit={commit}/></>
      : null;
  }
};

// Keyboard on a row (not while typing): F2 renames it, Delete removes it, Alt+↑ / Alt+↓ move it
const row_keys = actions => event => {
  if (is_typing(event.target) || event.defaultPrevented) return;
  if (event.key === 'F2') {
    event.preventDefault();
    event.currentTarget.querySelector('.qa-row-label [role="button"]')?.click();
  } else if ((event.key === 'Delete' || event.key === 'Backspace') && actions.remove) {
    event.preventDefault();
    actions.remove();
  } else if (event.altKey && (event.key === 'ArrowUp' || event.key === 'ArrowDown') && actions.move) {
    event.preventDefault();
    actions.move(event.key === 'ArrowUp' ? -1 : 1);
  }
};

// A reference that isn't the one compared: what it is, and actions. Its batch and filter change once it's selected.
const OtherReference = ({ reference, new_label, fields, actions, together_mode, row, handle, add_button }) => {
  const batch_label = reference.commit?.batches ? available_batch(reference.commit, reference.batch) : reference.batch;
  const batch = reference.commit?.batches?.[batch_label];
  // users named it; else what we call it
  const label = reference.label && !is_default_name(reference.label) && !reference.milestone ? reference.label : '';
  return <div
    className={`qa-row is-other ${row.className}`}
    style={{ '--side-color': reference.color.css }}
    data-index={reference.index}
    tabIndex={-1}
    {...row.props}
  >
    <span className="qa-row-slot">
      <Tooltip content="Compare with this one: the metrics and the viewers" hoverOpenDelay={300}>
        <button className="qa-radio" role="radio" aria-checked="false" aria-label={`Compare with ${reference.display_label}`} onClick={actions.select}/>
      </Tooltip>
    </span>
    <span className="qa-row-label qa-side-label-wrap" {...handle}>
      <SideLabel color={reference.color.css} label={label} fallback={reference.default_label} onRename={actions.rename}/>
    </span>
    <CommitId id={reference.commit?.id ?? reference.commit_id}/>
    <span className="qa-row-context">{why_text(reference, reference.commit, new_label, fields)}</span>
    <span className="qa-row-batch">
      <Button size="small" variant="minimal" onClick={actions.select} style={{ marginLeft: -8, minWidth: 0, maxWidth: '100%' }} title="Select it to change its batch">
        {batch ? <BatchLine item={batch_line_item(batch, batch_label)}/> : <span className={reference.commit ? Classes.TEXT_MUTED : Classes.SKELETON}>{batch_line_item(undefined, batch_label).title}</span>}
      </Button>
    </span>
    <span className="qa-row-filter">
      {reference.filter ? <Tag minimal icon="filter" title="Select it to change its filter" interactive onClick={actions.select}>{reference.filter}</Tag> : null}
    </span>
    <span className="qa-row-actions">
      {together_mode === 'all' ? <Tooltip content={reference.ticked ? "Shown in the image viewers with the reference selected: click to hide it" : "Hidden from the image viewers: click to show it"} hoverOpenDelay={300}>
        <Button size="small" variant="minimal" icon={reference.ticked ? 'eye-open' : 'eye-off'} className={Classes.TEXT_MUTED} aria-label={reference.ticked ? `Hide ${reference.display_label} from the image viewers` : `Show ${reference.display_label} in the image viewers`} onClick={actions.toggle}/>
      </Tooltip> : <span className="qa-action-slot"/>}
      <PopoverNext placement="bottom-end" content={<Menu>
        <MenuItem icon="selection" text="Compare with this one" onClick={actions.select}/>
        <MenuItem icon="swap-vertical" text={`Swap with ${new_label}`} disabled={!actions.swap} onClick={actions.swap}/>
        <MenuItem icon="duplicate" text="Compare with a copy" label="to change its batch or filter" onClick={actions.duplicate}/>
        <MenuItem icon="edit" text="Rename" label="F2" onClick={actions.startRename}/>
        <MenuItem icon="arrow-up" text="Move up" label="Alt ↑" disabled={!actions.canMove(-1)} onClick={() => actions.move(-1)} shouldDismissPopover={false}/>
        <MenuItem icon="arrow-down" text="Move down" label="Alt ↓" disabled={!actions.canMove(1)} onClick={() => actions.move(1)} shouldDismissPopover={false}/>
        <MenuDivider/>
        <MenuItem icon={together_mode === 'all' ? 'layers' : 'grid-view'} text={together_mode === 'all' ? 'Show one reference at a time' : 'Show the references together'} label="image viewers" onClick={actions.toggleMode}/>
        <MenuItem icon="cross" text="Remove" label="Del" onClick={actions.remove}/>
      </Menu>}>
        <Button size="small" variant="minimal" icon="more" className={Classes.TEXT_MUTED} aria-label={`Actions on ${reference.display_label}`}/>
      </PopoverNext>
      <span className="qa-add-slot">{add_button}</span>
    </span>
  </div>;
};


// The references' rows past max_rows: they scroll, the footer says how many are out of sight
const ScrollingReferences = ({ references, ref_index, children, add_button }) => {
  const expanded = useLedgerStore(state => state.expanded);
  const setExpanded = useLedgerStore(state => state.setExpanded);
  const ref = useRef(null);
  const [out_of_sight, setOutOfSight] = useState({ above: 0, below: 0 });
  const measure = () => {
    const element = ref.current;
    if (!element) return;
    const above = Math.round(element.scrollTop / ledger_row_height);
    const below = Math.max(0, Math.ceil((element.scrollHeight - element.clientHeight - element.scrollTop) / ledger_row_height - 0.05));
    if (above !== out_of_sight.above || below !== out_of_sight.below) setOutOfSight({ above, below });
  };
  useLayoutEffect(measure);
  // the reference compared is in sight, e.g. after r
  useEffect(() => {
    ref.current?.querySelector(`[data-index="${ref_index}"]`)?.scrollIntoView?.({ block: 'nearest' });
  }, [ref_index, expanded]);
  const height = references_height(references, expanded) - ledger_footer_height;
  const { above, below } = out_of_sight;
  return <>
    <div className="qa-refs" ref={ref} onScroll={measure} style={{ maxHeight: height }} role="radiogroup" aria-label="The references">
      {children}
    </div>
    <div className={`qa-footer ${below > 0 ? 'has-below' : ''}`}>
      <span><b>{references.length} references</b>{(above > 0 || below > 0) && <>{' · '}{[below > 0 && `${below} more below`, above > 0 && `${above} above`].filter(Boolean).join(', ')}</>}</span>
      <Button
        size="small" variant="minimal" endIcon={expanded ? 'chevron-up' : 'chevron-down'}
        onClick={() => setExpanded(!expanded)}
        style={{ minHeight: 20, fontSize: 12 }}
      >{expanded ? 'Show fewer' : 'Show all'}</Button>
      <span style={{ marginLeft: 'auto', paddingRight: 6 }}>{add_button}</span>
    </div>
  </>;
};


export const ComparisonHeader = () => {
  const queryClient = useQueryClient();
  const comparison = useComparison();
  const { project, project_data, selected, selected_views, new_commit, ref_commit, new_batch, ref_batch, references, compared, together_mode } = comparison;
  const setReferencesTogether = usePrefsStore(state => state.setReferencesTogether);
  const adding = useAddMenu(state => state.open);
  const setAdding = useAddMenu(state => state.setOpen);
  // the row dragged (its key: 'new' or a reference's index), where it would go: {key, kind: swap|before|after}
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
  const fields = milestone_fields(references.filter(r => r.milestone).map(r => r.milestone));

  // the actions need the commits' ids, e.g. the default reference's
  const resolved = { ...selected, references, new_commit_id: selected.new_commit_id || new_commit?.id || null };
  // r / shift r: what the metrics and viewers compare with. The image viewer's anchor stays.
  const show = index => updateSelected(show_reference(resolved, (index + references.length) % references.length));
  // selecting it: together, the images are compared with it
  const select = index => updateSelected({
    ...show_reference(resolved, index),
    ...(together_mode === 'all' ? set_anchor(resolved, 'reference') : {}),
  });
  const can_swap = index => !!references[index]?.commit_id && !!resolved.new_commit_id;
  const setTogetherMode = mode => {
    updateSelected(set_together_mode(resolved, mode));
    setReferencesTogether(mode === 'all');
  };
  const remove = index => removeReference(resolved, index, references[index].display_label);
  const can_move = (index, by) => index + by >= 0 && index + by < references.length;
  const move = (index, by) => can_move(index, by) && updateSelected(move_reference(resolved, index, index + by));
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

  // Rows are dragged by their name. Dropped on the new results' row, or the new results on a reference's: they swap.
  // Dropped on another reference's row: it goes before it, or after it, by the half of the row.
  const target_index = ({ key, kind }) => {
    const to = kind === 'after' ? key + 1 : key;
    return to > dragging ? to - 1 : to;
  };
  const handle = key => show_refs ? {
    draggable: true,
    onDragStart: event => {
      event.dataTransfer.effectAllowed = 'move';
      event.dataTransfer.setData('text/plain', String(key));
      const row = event.currentTarget.closest('.qa-row');
      if (row) event.dataTransfer.setDragImage?.(row, 20, ledger_row_height / 2);
      setDragging(key);
    },
    onDragEnd: () => {
      setDragging(null);
      setDrop(null);
    },
  } : {};
  const row_props = key => ({
    onDragOver: event => {
      if (dragging === null || dragging === key) return;
      const swapping = dragging === 'new' || key === 'new';
      if (swapping && !can_swap(key === 'new' ? dragging : key)) return;
      let kind = 'swap';
      if (!swapping) {
        const box = event.currentTarget.getBoundingClientRect();
        kind = box.height && event.clientY > box.top + box.height / 2 ? 'after' : 'before';
        // where it already is
        if (target_index({ key, kind }) === dragging) return setDrop(null);
      }
      event.preventDefault();
      if (drop?.key !== key || drop?.kind !== kind) setDrop({ key, kind });
    },
    onDrop: event => {
      event.preventDefault();
      if (dragging !== null && drop) {
        if (drop.kind === 'swap') updateSelected(swap_with_top(resolved, drop.key === 'new' ? dragging : drop.key));
        else updateSelected(move_reference(resolved, dragging, target_index(drop)));
      }
      setDragging(null);
      setDrop(null);
    },
  });
  const row_class = key => [dragging === key && 'is-dragged', drop?.key === key && dragging !== key && `drop-${drop.kind}`].filter(Boolean).join(' ');
  const row = (key, actions) => ({
    className: row_class(key),
    props: { ...row_props(key), onKeyDown: actions ? row_keys(actions) : undefined },
  });

  const add_button = <PopoverNext
    content={<AddReferencePanel actions={add_actions} can_duplicate={!!references[selected.ref_index]?.commit_id}/>}
    isOpen={adding}
    onInteraction={setAdding}
    placement="bottom-end"
  >
    <Tooltip content="Compare with another reference too: a branch, a milestone, a commit" hoverOpenDelay={300}>
      <Button size="small" variant="outlined" icon="plus" aria-label="Compare with another reference" style={{ borderRadius: 12 }}/>
    </Tooltip>
  </PopoverNext>;

  const scrolling = scrolls(references);
  const new_label = new_side.label;
  const reference_rows = references.map((reference, index) => {
    // the "+" is at the end of the last row, or in the footer
    const add_here = !scrolling && index === references.length - 1 ? add_button : null;
    const actions = {
      remove: several ? () => remove(index) : undefined,
      move: by => move(index, by),
    };
    if (!reference.is_shown) return <OtherReference
      key={reference.id}
      reference={reference}
      new_label={new_label}
      fields={fields}
      together_mode={together_mode}
      row={row(index, actions)}
      handle={handle(index)}
      add_button={add_here}
      actions={{
        ...actions,
        select: () => select(index),
        canMove: by => can_move(index, by),
        rename: label => updateSelected(rename_reference(resolved, index, label)),
        startRename: () => setTimeout(() => document.querySelector(`.qa-row[data-index="${index}"] .qa-row-label [role="button"]`)?.click()),
        toggle: () => updateSelected(toggle_together(resolved, index)),
        toggleMode: () => setTogetherMode(together_mode === 'all' ? 'one' : 'all'),
        duplicate: () => updateSelected(duplicate_reference(resolved, index, reference.display_label)),
        swap: can_swap(index) ? () => updateSelected(swap_with_top(resolved, index)) : undefined,
      }}
    />;
    const { className, props } = row(index, actions);
    return <div key={reference.id} className={`qa-row ${several ? 'is-selected' : ''} ${className}`} style={{ '--side-color': ref_side.color.css }} data-index={index} tabIndex={-1} {...props}>
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
        handle={handle(index)}
        slot={several
          ? <button className="qa-radio" role="radio" aria-checked="true" aria-label={`Comparing with ${ref_side.label}`} title="The metrics and the viewers compare with this one. r: the next one"/>
          : <span aria-hidden>vs</span>}
        context={reference.origin?.kind === 'error' ? undefined : why_text(reference, ref_commit, new_label, fields)}
        extra={<span className="qa-add-slot">{add_here}</span>}
      />
    </div>;
  });

  return <Ledger className="qa-comparison-header" role="region" aria-label="What we compare">
    <div className={`qa-row ${row_class('new')}`} style={{ '--side-color': new_side.color.css }} {...row_props('new')}>
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
        handle={handle('new')}
        slot={show_refs && <Tooltip content={`Swap ${new_label} and ${ref_side.label}`} hoverOpenDelay={300}>
          <Button className="qa-swap" size="small" variant="outlined" icon="swap-vertical" aria-label={`Swap ${new_label} and ${ref_side.label}`} onClick={() => updateSelected(switch_new_and_reference(selected))}/>
        </Tooltip>}
        context={<NewContext commit={new_commit} project={selected.new_project ?? project} reference_branch={reference_branch}/>}
        extra={show_refs ? <span className="qa-add-slot"/> : null}
      />
    </div>
    {show_refs && (scrolling
      ? <ScrollingReferences references={references} ref_index={selected.ref_index} add_button={add_button}>{reference_rows}</ScrollingReferences>
      : reference_rows)}
  </Ledger>;
};

// For the tests: what the header says about a reference
export { why_text };
