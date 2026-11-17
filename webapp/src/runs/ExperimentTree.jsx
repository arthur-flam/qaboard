// The experiments of a project as a tree, folded by the slashes of their names. Clicking a level shows its runs
// and everything under it. Each level says how many runs it has, how many run or failed, and when it last ran.
import { useState } from "react";
import styled from "styled-components";
import { Button, Classes, Icon, InputGroup, Menu, MenuItem, Popover, Spinner, Tooltip } from "@blueprintjs/core";

import { Link } from "../router";
import { copyLink, copyWithToast } from "../utils/clipboard";
import { ancestors, path_parts } from "./tree";
import { runs_path } from "./state";
import { relative } from "./format";


const Nav = styled.nav`
  font-size: 13px;
  .qa-tree-row {
    display: flex;
    align-items: center;
    gap: 4px;
    min-height: 28px;
    padding-right: 2px;
    border-radius: 3px;
    color: inherit;
  }
  .qa-tree-row:hover { background: var(--qa-hover); text-decoration: none; }
  .qa-tree-row.active { background: var(--qa-selected, rgba(45, 114, 210, 0.15)); }
  .qa-tree-row.active .qa-tree-name { font-weight: 600; }
  .qa-tree-name {
    flex: 1 1 auto;
    min-width: 0;
    overflow: hidden;
    text-overflow: ellipsis;
    white-space: nowrap;
    color: inherit;
  }
  .qa-tree-name:hover { text-decoration: none; }
  .qa-tree-counts { display: flex; align-items: center; gap: 6px; flex: none; font-variant-numeric: tabular-nums; font-size: 12px; }
  .qa-tree-menu { opacity: 0; }
  .qa-tree-row:hover .qa-tree-menu, .qa-tree-row.active .qa-tree-menu, .qa-tree-menu[aria-expanded="true"], .qa-tree-menu:focus-visible { opacity: 1; }
  .qa-tree-toggle { flex: none; width: 20px; display: inline-flex; justify-content: center; cursor: pointer; background: none; border: none; color: inherit; padding: 0; }
`;


// "12 runs · 2 running · 1 failed · last 3h ago"
export const describe = totals => [
  `${totals.runs} run${totals.runs === 1 ? '' : 's'}`,
  totals.running > 0 && `${totals.running} running`,
  totals.pending > 0 && `${totals.pending} waiting`,
  totals.failed > 0 && `${totals.failed} failed`,
  totals.users.size > 0 && `by ${[...totals.users].slice(0, 3).join(', ')}${totals.users.size > 3 ? ` and ${totals.users.size - 3} more` : ''}`,
  totals.latest && `last ${relative(totals.latest)}`,
].filter(Boolean).join(' · ');

export const Counts = ({ totals }) => <span className="qa-tree-counts">
  {totals.running > 0 && <Tooltip content={`${totals.running} running`} hoverOpenDelay={300}>
    <span style={{ color: 'var(--qa-intent-primary)', display: 'inline-flex', alignItems: 'center', gap: 3 }}>
      <Spinner size={10} aria-hidden/>{totals.running}
    </span>
  </Tooltip>}
  {totals.failed > 0 && <Tooltip content={`${totals.failed} failed`} hoverOpenDelay={300}>
    <span style={{ color: 'var(--qa-intent-danger)', display: 'inline-flex', alignItems: 'center', gap: 2 }}>
      <Icon icon="cross" size={11}/>{totals.failed}
    </span>
  </Tooltip>}
  <span className={Classes.TEXT_MUTED}>{totals.runs}</span>
</span>;

// What people do to a whole experiment
const NodeMenu = ({ project, node, onPython }) => <Popover
  placement="bottom-end"
  content={<Menu>
    <MenuItem icon="link" text="Copy the link" onClick={() => copyLink({ url: `${window.location.origin}${runs_path(project, node.path)}`, text: node.path }, { what: 'the link to the experiment' })}/>
    <MenuItem icon="duplicate" text="Copy the name" label={<code style={{ fontSize: 11 }}>experiment=</code>} onClick={() => copyWithToast(node.path, { what: 'the name' })}/>
    <MenuItem icon="code" text="Read its runs from Python…" onClick={() => onPython(node.path)}/>
  </Menu>}
>
  <Button size="small" variant="minimal" icon="more" className="qa-tree-menu" aria-label={`Actions on ${node.path}`}/>
</Popover>;

const Node = ({ project, node, depth, selected, open, toggle, search, search_params, onPython }) => {
  const has_children = node.children.length > 0;
  const is_open = open.has(node.path) || !!search;
  const active = node.path === selected;
  // the names of merged levels are paths themselves: "detector/backbone"
  const parts = node.name.split('/');
  return <li>
    <div className={`qa-tree-row${active ? ' active' : ''}`} style={{ paddingLeft: depth * 14 }} title={`${node.path}\n${describe(node.totals)}`}>
      {has_children
        ? <button type="button" className="qa-tree-toggle" aria-label={`${is_open ? 'Fold' : 'Unfold'} ${node.path}`} aria-expanded={is_open} onClick={() => toggle(node.path)}>
            <Icon icon={is_open ? 'chevron-down' : 'chevron-right'} size={12} className={Classes.TEXT_MUTED}/>
          </button>
        : <span className="qa-tree-toggle"/>}
      <Icon icon={has_children ? (is_open ? 'folder-open' : 'folder-close') : 'lab-test'} size={13} className={Classes.TEXT_MUTED} style={{ flex: 'none' }}/>
      <Link className="qa-tree-name" to={runs_path(project, node.path) + search_params} aria-current={active ? 'page' : undefined}>
        {parts.length > 1 && <span className={Classes.TEXT_MUTED}>{parts.slice(0, -1).join('/')}/</span>}{parts.at(-1)}
      </Link>
      <Counts totals={node.totals}/>
      <NodeMenu project={project} node={node} onPython={onPython}/>
    </div>
    {has_children && is_open && <ul style={{ listStyle: 'none', margin: 0, padding: 0 }}>
      {node.children.map(child => <Node key={child.path} {...{ project, node: child, depth: depth + 1, selected, open, toggle, search, search_params, onPython }}/>)}
    </ul>}
  </li>;
};

// Only the levels whose path contains the search, with their ancestors
const matching = (node, needle) => {
  const children = node.children.map(child => matching(child, needle)).filter(Boolean);
  if (children.length > 0 || node.path.toLowerCase().includes(needle)) return { ...node, children };
  return null;
};

// search_params: the page's filters, kept when going to another level
export const ExperimentTree = ({ project, tree, selected, loading, has_more, onPython, search_params = '' }) => {
  // the levels people opened, and those that lead to the one selected
  const [open, setOpen] = useState(() => new Set(ancestors(tree, selected)));
  // ...also once the tree has it: the address can point deep in it. Not when the tree refreshes: people folded it.
  const path_to_selected = ancestors(tree, selected).join('\n');
  const [opened_for, setOpenedFor] = useState({ selected, path_to_selected });
  if (opened_for.selected !== selected || (opened_for.path_to_selected === '' && path_to_selected !== '')) {
    setOpenedFor({ selected, path_to_selected });
    setOpen(open => new Set([...open, ...ancestors(tree, selected)]));
  }
  const [search, setSearch] = useState('');
  const toggle = path => setOpen(open => {
    const next = new Set(open);
    if (!next.delete(path)) next.add(path);
    return next;
  });
  const needle = search.trim().toLowerCase();
  const shown = needle ? (matching(tree, needle) ?? { ...tree, children: [] }) : tree;
  const count = tree.children.length;
  return <Nav aria-label="Experiments">
    <div style={{ display: 'flex', alignItems: 'baseline', justifyContent: 'space-between', marginBottom: 6 }}>
      <h4 className={Classes.HEADING} style={{ margin: 0, fontSize: 14 }}>Experiments</h4>
      {loading && <Spinner size={12} aria-label="Loading the experiments"/>}
    </div>
    {path_parts(selected).length > 0 || count > 6
      ? <InputGroup size="small" leftIcon="search" placeholder="Find an experiment" value={search} onChange={e => setSearch(e.target.value)} style={{ marginBottom: 6 }} aria-label="Find an experiment"/>
      : null}
    <div className={`qa-tree-row${!selected ? ' active' : ''}`} title={describe(tree.totals)}>
      <span className="qa-tree-toggle"/>
      <Icon icon="layers" size={13} className={Classes.TEXT_MUTED} style={{ flex: 'none' }}/>
      <Link className="qa-tree-name" to={runs_path(project) + search_params} aria-current={!selected ? 'page' : undefined}>All runs</Link>
      <Counts totals={tree.totals}/>
      <span style={{ width: 24 }}/>
    </div>
    <ul aria-label="Experiments" style={{ listStyle: 'none', margin: 0, padding: 0 }}>
      {shown.children.map(node => <Node key={node.path} {...{ project, node, depth: 0, selected, open, toggle, search: needle, search_params, onPython }}/>)}
    </ul>
    {needle && shown.children.length === 0 && <p className={Classes.TEXT_MUTED} style={{ padding: '6px 4px' }}>No experiment matches “{search}”.</p>}
    {has_more && <p className={Classes.TEXT_MUTED} style={{ fontSize: 12, padding: 4 }}>Only the most recent experiments: shorten the time window to see others.</p>}
  </Nav>;
};

export default ExperimentTree;
