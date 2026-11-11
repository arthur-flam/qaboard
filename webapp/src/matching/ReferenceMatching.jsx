// Above the runs, next to the settings they share (src/configurations/ConfigurationOverview.jsx): how the runs find
// their reference run. Hidden in the usual case: it shows when runs have no reference, when several reference runs were
// as close (tied), or when users changed the rules (src/matching/rules.js), in the URL so that links show the same pairs.
// It suggests the changes of name that pair the runs without reference.
import { useMemo, useState } from "react";
import { AnchorButton, Button, Classes, ControlGroup, Icon, InputGroup, Intent, Tag } from "@blueprintjs/core";

import { useComparison, useSiteConfig } from "../hooks";
import { updateSelected } from "../selection";
import { toaster } from "../toaster";
import { TextChange } from "../configurations/ConfigurationsTags";
import { suggest_pairs, rule_text, name_changes, rule_of_change, paired_by } from "./names";
import { fact_text, rules_to_selection, rules_defaults } from "./rules";

const plural = (n, word) => `${n} ${word}${n === 1 ? '' : 's'}`;
const line_style = { display: 'flex', flexWrap: 'wrap', alignItems: 'center', gap: '4px 8px', margin: '1px 0' };
const code_style = { whiteSpace: 'nowrap' };

// Typed by users: "reg0x1A=05" and "reg0x1A=03". When they differ by one part, the rule is on that part.
const typed_rule = (from, to) => {
  const changes = name_changes(from, to);
  const rule = changes?.length === 1 ? rule_of_change(changes[0]) : null;
  return rule?.from && rule?.to ? rule : { from, to };
};

// What there is to say about how runs find their reference, null when there's nothing
export const useReferenceMatching = () => {
  const comparison = useComparison();
  const { new_batch, ref_batch, ref_lookup, selected } = comparison ?? {};
  const rules = selected?.reference_rules;
  return useMemo(() => {
    if (!new_batch || !rules || !ref_lookup?.outputs?.length) return null;
    const outputs = (new_batch.filtered?.outputs ?? [])
      .map(id => new_batch.outputs[id])
      .filter(o => o && !o.is_pending && o.output_type !== 'optim_iteration' && o.output_type !== 'batch');
    const without = outputs.filter(o => !o.reference_match);
    const tied = outputs.filter(o => o.reference_match?.tied > 0 && o.reference_match.by !== 'pin' && !o.reference_match.preferred).length;
    if (!rules.custom && !tied && !without.length) return null;
    const suggestions = without.length
      ? suggest_pairs({ unmatched: without, outputs, index: ref_lookup, labels: { new: new_batch.label, reference: ref_batch?.label }, pairs: rules.pairs })
      : [];
    return { rules, outputs, without: without.length, tied, suggestions, ref_lookup };
  }, [new_batch, ref_batch, ref_lookup, rules]);
};

const save_rules = (rules, next) => updateSelected(rules_to_selection({ ...rules, ...next }), { defaults: rules_defaults });

// The row's values: one line per thing to know, with what users can do about it
export const ReferenceMatchingValues = ({ status }) => {
  const [typing, setTyping] = useState(false);
  const [all_suggestions, setAllSuggestions] = useState(false);
  const [from, setFrom] = useState('');
  const [to, setTo] = useState('');
  const { rules, suggestions } = status;
  const save = next => save_rules(rules, next);
  const add_pair = rule => save({ pairs: [...rules.pairs.filter(p => p.from !== rule.from), rule] });
  const pins = Object.keys(rules.pins).length;
  const shown_suggestions = all_suggestions ? suggestions : suggestions.slice(0, 1);

  return <>
    {status.without > 0 && <div style={line_style}>
      <span>
        <Icon icon="disable" size={12} intent={Intent.WARNING} style={{ marginRight: 5 }}/>
        {plural(status.without, 'run')} {status.without === 1 ? 'has' : 'have'} no reference run.
        {suggestions.length === 0 && <span className={Classes.TEXT_MUTED}> No change of one part of their names pairs them: their tag lists similar names.</span>}
      </span>
      {shown_suggestions.map((s, i) => <span key={`${s.rule.from}|${s.rule.to}`} style={line_style}>
        {i === 0 && <span className={Classes.TEXT_MUTED}>Pair them by name:</span>}
        <code style={code_style}><TextChange before={s.rule.from} after={s.rule.to}/></code>
        <span className={Classes.TEXT_MUTED}>pairs {plural(s.pairs, 'run')}</span>
        <Button size="small" intent={i === 0 ? Intent.PRIMARY : undefined} text="Pair" aria-label={`Pair ${rule_text(s.rule)}`} onClick={() => add_pair(s.rule)}/>
      </span>)}
      {suggestions.length > 1 && <Tag round minimal interactive icon={all_suggestions ? 'chevron-up' : 'more'} onClick={() => setAllSuggestions(!all_suggestions)}>
        {all_suggestions ? 'Show less' : `${plural(suggestions.length - 1, 'other change')}`}
      </Tag>}
    </div>}

    {rules.pairs.length > 0 && <div style={line_style}>
      <span className={Classes.TEXT_MUTED}>Paired by name:</span>
      {rules.pairs.map(p => <Tag key={`${p.from}|${p.to}`} minimal intent={Intent.PRIMARY} onRemove={() => save({ pairs: rules.pairs.filter(q => q !== p) })} aria-label={`Paired by name: ${rule_text(p)}`}>
        <code style={code_style}><TextChange before={p.from} after={p.to}/></code>
      </Tag>)}
    </div>}
    {rules.prefer.length > 0 && <div style={line_style}>
      <span className={Classes.TEXT_MUTED}>Preferred reference runs:</span>
      {rules.prefer.map(p => <Tag key={`${p.key}=${p.value}`} minimal intent={Intent.PRIMARY} icon="star" onRemove={() => save({ prefer: rules.prefer.filter(q => q !== p) })}>{fact_text(p)}</Tag>)}
    </div>}
    {pins > 0 && <div style={line_style}>
      <span>{plural(pins, 'run')} compared with a reference run you picked</span>
      <Button size="small" variant="minimal" text="Clear" onClick={() => save({ pins: {} })}/>
    </div>}
    {status.tied > 0 && <div style={line_style} className={Classes.TEXT_MUTED}>
      <span>
        <Icon icon="warning-sign" size={12} intent={Intent.WARNING} style={{ marginRight: 5 }}/>
        {plural(status.tied, 'run')} had reference runs as close as each other: we took the first. Click their tag to pick.
      </span>
    </div>}

    {typing
      ? <form style={{ ...line_style, maxWidth: 560 }} onSubmit={event => {
          event.preventDefault();
          if (!from.trim() || !to.trim() || from.trim() === to.trim()) return;
          const rule = typed_rule(from.trim(), to.trim());
          const pairs = paired_by(rule, status.outputs, status.ref_lookup);
          if (!pairs) return toaster.show({ message: `No run has "${rule.from}" in its name where "${rule.to}" would make it a reference run's name. Type whole parts, between separators like _ - . = /`, intent: Intent.WARNING, timeout: 8000 });
          add_pair(rule);
          toaster.show({ message: `${plural(pairs, 'run')} compared by name`, icon: 'link' });
          setFrom(''); setTo(''); setTyping(false);
        }}>
        <ControlGroup fill style={{ flex: '1 1 auto' }}>
          <InputGroup size="small" autoFocus placeholder="in the runs: reg0x1A=05" aria-label="Part of the runs' names" value={from} onValueChange={setFrom}/>
          <InputGroup size="small" placeholder="in the references: reg0x1A=03" aria-label="Part of the reference runs' names" value={to} onValueChange={setTo}/>
          <Button size="small" type="submit" text="Pair" disabled={!from.trim() || !to.trim()}/>
          <Button size="small" variant="minimal" text="Cancel" onClick={() => setTyping(false)}/>
        </ControlGroup>
      </form>
      : (status.without > 0 || rules.pairs.length > 0) && <Button size="small" variant="minimal" icon="add" text="Pair other names…" className={Classes.TEXT_MUTED} onClick={() => setTyping(true)} style={{ marginLeft: -6 }}/>}
  </>;
};

export const ReferenceMatchingActions = ({ status }) => {
  const { docs_root } = useSiteConfig();
  return <>
    {status.rules.custom && <Button
      size="small"
      variant="minimal"
      icon="reset"
      text="Back to the default"
      title="Compare each run with the reference run on the same input, or with the same metadata.id. The checks from the CLI and the API do."
      onClick={() => save_rules(status.rules, { pairs: [], prefer: [], pins: {} })}
    />}
    <AnchorButton
      size="small"
      variant="minimal"
      icon="help"
      className={Classes.TEXT_MUTED}
      aria-label="How runs find their reference run"
      title="How runs find their reference run"
      href={`${docs_root ?? ''}docs/user-guide/reference-runs`}
      target="_blank"
      rel="noopener noreferrer"
    />
  </>;
};
