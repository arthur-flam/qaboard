import { create } from "zustand";
import styled from "styled-components";


// The page's content: its width follows the content area (the window minus the sidebar), not the window,
// so that pages look the same whatever the sidebar takes. The side margins grow with the content area.
const Container = styled.div`
  box-sizing: border-box;
  width: 100%;
  margin-top: 20px;
  margin-bottom: 10px;
  /* percentages are of the content area's width */
  padding-left: clamp(16px, 4%, 96px);
  padding-right: clamp(16px, 4%, 96px);
`;

// A view of the page: as wide as the content area, wider if its content needs it (e.g. a table with many metrics)
const Section = styled.div`
  margin-bottom: 40px;
  margin-top: 30px;
  width: fit-content;
  min-width: 100%;
  box-sizing: border-box;
`;



const Layout = styled.div`
  display: flex;
  flex: auto;
  flex-direction: row;
  box-sizing: border-box;
  // style
  background: var(--qa-background);
  min-height: 750px;
`

export { Container, Section, Layout };


// The commit page's fixed header (components/ComparisonHeader.jsx): a row for the new commit, one for each reference
// (except in views about the new commit only). Past `max_rows` references, they scroll: 3 rows and a half show (the
// cut row says there's more), with a footer that says how many are out of sight, and can show them all.

export const ledger_row_height = 36;
export const ledger_footer_height = 26;
export const ledger_padding = 4;
export const max_rows = 4;
export const scrolled_rows = 3.5;
export const expanded_rows = 12;
export const shows_reference_bar = selected_views => !['logs', 'tuning', 'groups'].some(view => selected_views.includes(view));
// whether users show all the references, past max_rows
export const useLedgerStore = create(set => ({ expanded: false, setExpanded: expanded => set({ expanded }) }));
export const scrolls = references => references.length > max_rows;
// the height of the references' rows
export const references_height = (references, expanded) => !scrolls(references)
  ? references.length * ledger_row_height
  : (expanded ? Math.min(references.length, expanded_rows) : scrolled_rows) * ledger_row_height + ledger_footer_height;
export const commit_header_height = ({ selected_views, references, expanded = false }) => {
  const padding = 2 * ledger_padding;
  if (!shows_reference_bar(selected_views)) return padding + ledger_row_height;
  return padding + ledger_row_height + references_height(references, expanded);
};
