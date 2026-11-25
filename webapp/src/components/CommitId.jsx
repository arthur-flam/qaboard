// A commit's short id, in the comparison header's rows: click to copy the full id
import { Classes } from "@blueprintjs/core";

import { toaster } from "../toaster";


export const CommitId = ({ id }) => {
  if (!id) return <span className="qa-row-sha"/>;
  const short = id.slice(0, 7);
  const copy = () => navigator.clipboard?.writeText(id)
    .then(() => toaster.show({ message: <>Copied <code>{id}</code></>, icon: 'clipboard', timeout: 2000 }, 'copy-commit-id'))
    .catch(() => toaster.show({ message: `Could not copy ${id}`, intent: 'danger' }, 'copy-commit-id'));
  return <span className="qa-row-sha">
    <button className={`${Classes.MONOSPACE_TEXT} qa-sha`} title={`${id}: click to copy`} aria-label={`Copy the commit id ${short}`} onClick={copy}>{short}</button>
  </span>;
};
