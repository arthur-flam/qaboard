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
// (except in views about the new commit only). Past `max_rows` references, the others are listed in a last row.
export const commit_bar_height = 75;
export const reference_tabs_height = 34;
export const ledger_row_height = 36;
export const ledger_more_height = 30;
export const ledger_padding = 4;
const max_rows = 3;
export const shows_reference_bar = selected_views => !['logs', 'tuning', 'groups'].some(view => selected_views.includes(view));
// The references with a row ({shown}: their indexes, in order), and the others ({hidden}). The one compared always has one.
export const visible_references = (references, ref_index) => {
  const all = references.map((_, index) => index);
  if (all.length <= max_rows) return { shown: all, hidden: [] };
  const shown = all.slice(0, max_rows);
  if (!shown.includes(ref_index)) shown[max_rows - 1] = ref_index;
  shown.sort((a, b) => a - b);
  return { shown, hidden: all.filter(index => !shown.includes(index)) };
};
export const commit_header_height = ({ selected_views, references }) => {
  const padding = 2 * ledger_padding;
  if (!shows_reference_bar(selected_views)) return padding + ledger_row_height;
  const ref_index = Math.max(0, references.findIndex(reference => reference.is_shown));
  const { shown, hidden } = visible_references(references, ref_index);
  return padding + (1 + shown.length) * ledger_row_height + (hidden.length ? ledger_more_height : 0);
};
