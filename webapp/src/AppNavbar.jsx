import styled from "styled-components";

import { Suggest } from "@blueprintjs/select";
import {
  Classes,
  Intent,
  ControlGroup,
  Navbar,
  NavbarGroup,
  InputGroup,
  MenuItem,
  Button,
  Spinner,
  Tooltip,
} from "@blueprintjs/core";

import { Suspense, lazy, useRef, useState } from "react";
import { useRouter } from "./router";
import { path_segment } from "./selection";
import { ComparisonHeader } from "./components/ComparisonHeader";
import { SelectBatchesNav } from "./components/tuning/SelectBatches";
import { useBranches, useCommitsList, useComparison, useUrlText, updateSelected } from "./hooks";

import { sider_width } from './AppSider'
import { CopyLinkButton } from "./components/CopyLinkButton";
import { list_title, history_title } from "./pageTitle";

const DateRangeInput = lazy(() => import("./components/DateRangeInput"));




const renderBranch = (item, { handleClick, modifiers }) => {
  return (
    <MenuItem
      active={modifiers.active}
      icon="git-branch"
      key={item}
      onClick={handleClick}
      text={item}
    />
  );
};
const renderNewItem = (query, active, handleClick)  => {
  return <MenuItem
      icon="git-commit"
      text={<span><strong>Go to commit:</strong> {query}</span>}
      label="↓ then Enter"
      active={active}
      onClick={handleClick}
      shouldDismissPopover={false}
  />

}
                


// What we look for in branch names, in searches like "fix" or "speed branch:feature -wip"
export const branch_query = search => {
  const qualified = search.match(/(?:^|\s)branch:(?:"([^"]*)"?|(\S+))/i);
  if (qualified) return (qualified[1] ?? qualified[2]).trim();
  const words = search.trim().split(/\s+/).filter(word => !word.startsWith('-') && !word.includes(':'));
  return words.length === 1 ? words[0].replace(/"/g, '') : '';
}

export function filterBranch(query, branch) {
  const text = branch_query(query ?? '');
  // e.g. "fix crash" or "committer:alice" are not about branches
  if (!text) return !(query ?? '').trim();
  return branch.toLowerCase().indexOf(text.toLowerCase()) >= 0;
}


// One row that never wraps: the search boxes get narrower with the content area (cqi: % of the navbar's width)
const StyledNavbar = styled(Navbar)`
   position: fixed !important;
   top: 0;
   padding-left: ${sider_width} !important;
   display: flex;
   align-items: center;
   gap: 12px;
   container-type: inline-size;
   > .${Classes.NAVBAR_GROUP} {
     float: none;
   }
   > .${Classes.NAVBAR_GROUP}:last-child {
     margin-left: auto;
     min-width: 0;
     gap: 8px;
     --batch-select-max-width: clamp(150px, 26cqi, 360px);
   }
`

// When comparing commits: a row for each side (components/ComparisonHeader.jsx)
const CommitsNavbars = () => <ComparisonHeader/>;


// Lists of commits: pick the dates, filter, go to a branch or a commit
const CommitsListNavbar = () => {
  const { history } = useRouter();
  const list = useCommitsList();
  const { project, date_range } = list;
  const { selected, new_commit, new_batch } = useComparison();
  // We only fetch branches once users start searching
  const [wants_branches, setWantsBranches] = useState(false);
  const [today] = useState(() => new Date());
  // The search goes in the URL when users pause typing: it searches commits (CiCommitList) and branches
  const [search, onSearchChange] = useUrlText(selected.search, query => updateSelected({ search: query || undefined }, { replace: true }), 300);
  const [filter, onFilterChange] = useUrlText(selected.filter_batch_new, value => updateSelected({ filter_batch_new: value }, { replace: true }));
  const branch_search = branch_query(selected.search);
  const { data: branches = [] } = useBranches(project, branch_search, { enabled: wants_branches && (!!branch_search || !selected.search.trim()) });
  // Enter keeps filtering: it only goes to a branch or a commit after users picked one with the arrow keys
  const picked = useRef(false);
  const onSearchKeyDownCapture = event => {
    if (event.key === 'ArrowDown' || event.key === 'ArrowUp')
      picked.current = true;
    else if (event.key === 'Enter' && !picked.current)
      event.stopPropagation();
  };

  const handleBranchChange = branch => {
    if (branch.commit !== undefined && branch.commit !== null)
      history.push(`/${path_segment(project)}/commit/${path_segment(branch.commit)}`);
    else
      updateSelected({ branch });
  };
  const changeDates = new_date_range => {
    if (new_date_range[0] === null && new_date_range[1] === null)
      return
    updateSelected({ from: new_date_range[0] ?? date_range[0], to: new_date_range[1] ?? date_range[1] }, { replace: true })
  }

  const { route } = selected;
  const date_input_props = {style: {width:'100px'}}
  // searches look at all the commits (see useCommitsList)
  const is_searching = route.is_commits && !!selected.search?.trim()
  return (
    <StyledNavbar>
      <NavbarGroup style={{marginLeft: '20px'}}>
        {/* Until the picker loads, the same inputs, so that the navbar doesn't move */}
        <Suspense fallback={<ControlGroup>
          {date_range.map((date, index) => <InputGroup key={index} {...date_input_props} value={date?.toLocaleDateString() ?? ""} disabled readOnly/>)}
        </ControlGroup>}>
        <Tooltip content="Searches look at all the commits, whatever the dates." disabled={!is_searching}>
        <DateRangeInput
          endInputProps={{...date_input_props, 'aria-label': 'End date'}}
          startInputProps={{...date_input_props, 'aria-label': 'Start date'}}
          disabled={is_searching}
          value={date_range}
          maxDate={today}
          allowSingleDayRange
          formatDate={date => date == null ? "" : date.toLocaleDateString()}
          parseDate={str => new Date(str)}
          onChange={changeDates}
          shortcuts
        />
        </Tooltip>
        </Suspense>
        <div style={{marginLeft: '5px'}}>
          <Tooltip content="Check for new commits">
            <Button icon="refresh" aria-label="Check for new commits" disabled={list.isFetching} minimal onClick={() => list.refetch()}/>
          </Tooltip>
        </div>
        <CopyLinkButton text={route.is_history ? history_title({ project, branch: selected.branch }) : list_title({ project, branch: selected.branch, committer: selected.committer })}/>
        {list.isFetching && <div style={{marginLeft: '15px'}}><Spinner size={16} /></div>}
      </NavbarGroup>
      <NavbarGroup align="right">
        {route.is_history && <SelectBatchesNav
          commit={new_commit}
          batch={new_batch}
          onChange={event => updateSelected({ selected_batch_new: event.target.value, selected_batch_ref: event.target.value })}
          hide_counts
        />}
        {route.is_commits && <div style={{display: 'contents'}} onKeyDownCapture={onSearchKeyDownCapture}>
            <Suggest
              query={search}
              itemPredicate={filterBranch}
              createNewItemFromQuery={query => ({commit: query.trim()})}
              createNewItemRenderer={renderNewItem}
              items={branches}
              itemRenderer={renderBranch}
              inputValueRenderer={item => typeof item === 'string' ? item : item.commit}
              noResults={<MenuItem disabled={true} text="No results." />}
              onItemSelect={handleBranchChange}
              inputProps={{
                leftIcon: 'filter',
                intent: !!search ? Intent.PRIMARY : null,
                // Suggest only reads the placeholder from inputProps
                placeholder: "Search commits, or go to a branch or commit…",
                style: {width: 'clamp(180px, 40cqi, 350px)'},
              }}
              onQueryChange={query => {
                picked.current = false;
                setWantsBranches(true);
                onSearchChange(query);
              }}
            />
        </div>}
        {route.is_history &&
          <InputGroup
            value={filter}
            placeholder="Filter runs: path, tag, key:value…"
            title="Filter runs by path, configuration, platform, tag or tuning parameters (key:value)"
            aria-label="Filter runs"
            intent={filter ? Intent.PRIMARY : undefined}
            onChange={onFilterChange}
            type="text"
            leftIcon="filter"
            rightElement={filter ? <Button size="small" variant="minimal" icon="cross" aria-label="Clear the filter" onClick={() => onFilterChange('')}/> : undefined}
            style={{width: 'clamp(150px, 30cqi, 320px)'}}
          />}
      </NavbarGroup>
    </StyledNavbar>
  );
}


const AppNavbar = () => {
  const { selected } = useComparison();
  // the Access page and the project's other Admin pages have nothing to search or filter
  if (selected.route.is_access || selected.route.is_project_admin) return <StyledNavbar aria-hidden="true"/>;
  return selected.route.is_commit ? <CommitsNavbars/> : <CommitsListNavbar/>;
}

export default AppNavbar;
