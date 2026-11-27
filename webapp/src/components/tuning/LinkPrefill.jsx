// Links that open Run Tests / Tuning filled in (prefill_link.js): the commit page fills the form once, and the form
// says what the link filled, until people dismiss it or undo it.
import { useEffect, useRef } from "react";
import { create } from "zustand";
import { Button, Callout, Classes, Intent } from "@blueprintjs/core";

import { usePrefsStore } from "../../stores/prefs";
import { useSelected, updateSelected } from "../../selection";
import { track } from "../../analytics";
import { parse_prefill_link, form_from_link, describe_link } from "./prefill_link";

// What the last link filled: {project, prefill, warnings, previous}. Not saved: a reload doesn't show it again.
const useLinkPrefillStore = create(() => ({ notice: null }));

/** On the commit page: fills the form from ?prefill=, shows it, and takes ?prefill= out of the URL */
export const usePrefillLink = project => {
  const { query } = useSelected();
  const raw = query.prefill;
  const applied = useRef(null);
  useEffect(() => {
    // once per link, also when React runs effects twice
    if (raw === undefined) applied.current = null;
    if (raw === undefined || !project || applied.current === raw) return;
    applied.current = raw;
    const { prefill, warnings } = parse_prefill_link(raw);
    const patch = form_from_link(prefill);
    const form = usePrefsStore.getState().tuning[project] ?? {};
    const previous = Object.fromEntries(Object.keys(patch).map(key => [key, form[key]]));
    usePrefsStore.getState().updateTuning(project, patch);
    const says_something = Object.keys(prefill).length > 0 || warnings.length > 0;
    useLinkPrefillStore.setState({ notice: says_something ? { project, prefill, warnings, previous } : null });
    track('tuning_link_prefill', { fields: Object.keys(prefill), ignored: warnings.length }, { project_id: project });
    // a reload, or going back, doesn't fill it again
    updateSelected({ prefill: undefined, selected_views: 'tuning' }, { replace: true });
  }, [raw, project]);
};

/** Above the form: what the link filled, and that nothing runs before people click Start */
export const LinkPrefillNotice = ({ project }) => {
  const notice = useLinkPrefillStore(state => state.notice);
  if (!notice || notice.project !== project) return null;
  const { prefill, warnings, previous } = notice;
  const items = describe_link(prefill);
  const dismiss = () => useLinkPrefillStore.setState({ notice: null });
  const undo = () => {
    usePrefsStore.getState().updateTuning(project, previous);
    dismiss();
  };
  return <Callout
    intent={warnings.length > 0 ? Intent.WARNING : Intent.PRIMARY}
    icon="link"
    title={items.length === 0 ? "This link could not fill the form" : "Filled in from a link"}
    style={{ marginBottom: 18 }}
    data-testid="tuning-link-prefill"
  >
    {items.length > 0 && <>
      <p>Someone, or an assistant, prepared this link. It filled:</p>
      <ul className={Classes.LIST} style={{ margin: '0 0 8px' }}>
        {items.map(([what, value]) => <li key={what}><strong>{what}</strong>: {value}</li>)}
      </ul>
    </>}
    {warnings.length > 0 && <ul className={Classes.LIST} style={{ margin: '0 0 8px' }} data-testid="tuning-link-prefill-warnings">
      {warnings.map(warning => <li key={warning}>{warning}</li>)}
    </ul>}
    {items.length > 0 && <p>Check the form: nothing runs until you click <strong>Start</strong>.</p>}
    <div style={{ display: 'flex', gap: 6 }}>
      {items.length > 0 && <Button size="small" icon="undo" text="Undo" onClick={undo} data-testid="tuning-link-prefill-undo"/>}
      <Button size="small" variant="minimal" text="Dismiss" onClick={dismiss}/>
    </div>
  </Callout>;
};
