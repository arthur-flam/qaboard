// In the reference's bar: how the runs find their reference run. Hidden in the usual case: it shows when runs have no
// reference, when several reference runs were as close (tied), or when users changed the rules (src/matching/rules.js),
// in the URL so that the link shows the same pairs. It suggests the name changes that pair the runs without reference.
import { useMemo, useState } from "react";
import { Button, Classes, ControlGroup, InputGroup, Intent, PopoverNext, Tag } from "@blueprintjs/core";
import styled from "styled-components";

import { useComparison, useSiteConfig } from "../hooks";
import { updateSelected } from "../selection";
import { suggest_pairs, rule_text, name_changes, rule_of_change, paired_by } from "./names";
import { toaster } from "../toaster";
import { fact_text, rules_to_selection, rules_defaults } from "./rules";

const Panel = styled.div`
  width: min(420px, calc(100vw - 32px));
  padding: 12px;
  display: flex;
  flex-direction: column;
  gap: 12px;
  h4 { margin: 0 0 4px; font-size: 12px; font-weight: 600; }
  .row { display: flex; align-items: center; gap: 8px; justify-content: space-between; }
  .chips { display: flex; flex-wrap: wrap; gap: 4px; }
  code { white-space: nowrap; }
  .foot { border-top: 1px solid var(--qa-border); padding-top: 8px; display: flex; flex-direction: column; gap: 4px; font-size: 12px; }
`;

const plural = (n, word) => `${n} ${word}${n === 1 ? '' : 's'}`;

// Typed by users: "reg0x1A=05" and "reg0x1A=03". When they differ by one part, the rule is on that part.
const typed_rule = (from, to) => {
  const changes = name_changes(from, to);
  const rule = changes?.length === 1 ? rule_of_change(changes[0]) : null;
  return rule?.from && rule?.to ? rule : { from, to };
};

export const ReferenceMatching = () => {
  const comparison = useComparison();
  const { docs_root } = useSiteConfig();
  const [typing, setTyping] = useState(false);
  const [from, setFrom] = useState('');
  const [to, setTo] = useState('');
  const { new_batch, ref_batch, ref_lookup, selected } = comparison ?? {};
  const rules = selected?.reference_rules;

  const status = useMemo(() => {
    if (!new_batch || !ref_lookup?.outputs?.length) return null;
    const outputs = (new_batch.filtered?.outputs ?? [])
      .map(id => new_batch.outputs[id])
      .filter(o => o && !o.is_pending && o.output_type !== 'optim_iteration' && o.output_type !== 'batch');
    const without = outputs.filter(o => !o.reference_match);
    const tied = outputs.filter(o => o.reference_match?.tied > 0 && o.reference_match.by !== 'pin' && !o.reference_match.preferred).length;
    const suggestions = without.length
      ? suggest_pairs({ unmatched: without, outputs, index: ref_lookup, labels: { new: new_batch.label, reference: ref_batch?.label }, pairs: rules.pairs })
      : [];
    return { outputs, without: without.length, tied, suggestions };
  }, [new_batch, ref_batch, ref_lookup, rules]);

  if (!status || (!rules.custom && !status.tied && !status.without)) return null;

  const save = next => updateSelected(rules_to_selection({ ...rules, ...next }), { defaults: rules_defaults });
  const add_pair = rule => save({ pairs: [...rules.pairs.filter(p => p.from !== rule.from), rule] });
  const pins = Object.keys(rules.pins).length;

  const custom_parts = [
    ...rules.pairs.map(rule_text),
    ...rules.prefer.map(fact_text),
    pins && plural(pins, 'pick'),
  ].filter(Boolean);
  const label = rules.custom
    ? `Matching: ${custom_parts.length > 2 ? `${custom_parts.length} rules` : custom_parts.join(' · ')}`
    : status.without ? `${status.without} without reference` : `${plural(status.tied, 'run')} with a tie`;
  const attention = rules.custom
    ? [status.without && `${status.without} without reference`, status.tied && `${plural(status.tied, 'run')} with a tie`].filter(Boolean).join(' · ')
    : '';

  const panel = <Panel>
    {status.without > 0 && <div>
      <h4>{plural(status.without, 'run')} {status.without === 1 ? 'has' : 'have'} no reference run</h4>
      {status.suggestions.length > 0
        ? <>
          <p className={Classes.TEXT_MUTED} style={{ margin: '0 0 6px', fontSize: 12 }}>Pair them with the reference runs whose name differs by:</p>
          {status.suggestions.map((s, i) => <div className="row" key={`${s.rule.from}|${s.rule.to}`} style={{ marginBottom: 4 }}>
            <span><code>{rule_text(s.rule)}</code> <span className={Classes.TEXT_MUTED} style={{ fontSize: 12 }}>pairs {plural(s.pairs, 'run')}</span></span>
            <Button size="small" intent={i === 0 ? Intent.PRIMARY : undefined} text="Pair" onClick={() => add_pair(s.rule)}/>
          </div>)}
        </>
        : <p className={Classes.TEXT_MUTED} style={{ margin: 0, fontSize: 12 }}>No change of one part of their names would pair them. A run's tag lists the reference runs with a similar name.</p>}
    </div>}

    {rules.pairs.length > 0 && <div>
      <h4>Paired by name</h4>
      <div className="chips">{rules.pairs.map(p => <Tag key={`${p.from}|${p.to}`} minimal onRemove={() => save({ pairs: rules.pairs.filter(q => q !== p) })}><code>{rule_text(p)}</code></Tag>)}</div>
    </div>}
    {rules.prefer.length > 0 && <div>
      <h4>Preferred reference runs</h4>
      <div className="chips">{rules.prefer.map(p => <Tag key={`${p.key}=${p.value}`} minimal icon="star" onRemove={() => save({ prefer: rules.prefer.filter(q => q !== p) })}>{fact_text(p)}</Tag>)}</div>
    </div>}
    {pins > 0 && <div className="row">
      <span>{plural(pins, 'run')} compared with a reference run you picked</span>
      <Button size="small" variant="minimal" text="Clear" onClick={() => save({ pins: {} })}/>
    </div>}

    {typing
      ? <form onSubmit={event => {
          event.preventDefault();
          if (!from || !to || from === to) return;
          const rule = typed_rule(from.trim(), to.trim());
          const pairs = paired_by(rule, status.outputs, ref_lookup);
          if (!pairs) return toaster.show({ message: `No run has "${rule.from}" in its name where "${rule.to}" would make it a reference run's name. Type whole parts, between separators like _ - . = /`, intent: Intent.WARNING, timeout: 8000 });
          add_pair(rule);
          toaster.show({ message: `${plural(pairs, 'run')} compared by name`, icon: 'link' });
          setFrom(''); setTo(''); setTyping(false);
        }}>
        <h4>Pair names</h4>
        <ControlGroup fill>
          <InputGroup size="small" autoFocus placeholder="in the runs: reg0x1A=05" aria-label="Part of the runs' names" value={from} onValueChange={setFrom}/>
          <InputGroup size="small" placeholder="in the references: reg0x1A=03" aria-label="Part of the reference runs' names" value={to} onValueChange={setTo}/>
          <Button size="small" type="submit" text="Add" disabled={!from.trim() || !to.trim()}/>
        </ControlGroup>
      </form>
      : <Button size="small" variant="minimal" icon="add" alignText="start" text="Pair other names…" className={Classes.TEXT_MUTED} onClick={() => setTyping(true)} style={{ alignSelf: 'flex-start' }}/>}

    <div className="foot">
      <span className={Classes.TEXT_MUTED}>
        {status.outputs.length - status.without} of {plural(status.outputs.length, 'run')} {status.outputs.length === 1 ? 'has' : 'have'} a reference{status.tied > 0 && ` · ${status.tied} had reference runs as close as each other: we took the first`}.
        {rules.custom && ' Saved in the link. Checks from the CLI and the API use the default.'}
      </span>
      <span className="row">
        <a href={`${docs_root ?? ''}docs/user-guide/reference-runs`} target="_blank" rel="noopener noreferrer">How runs find their reference ↗</a>
        {rules.custom && <Button size="small" variant="minimal" text="Back to the default" onClick={() => save({ pairs: [], prefer: [], pins: {} })}/>}
      </span>
    </div>
  </Panel>;

  return <span style={{ display: 'inline-flex', alignItems: 'center', marginRight: 6 }}>
    <PopoverNext placement="bottom-end" content={panel}>
      <Button
        size="small"
        variant={rules.custom ? 'outlined' : 'minimal'}
        intent={rules.custom || status.without || status.tied ? Intent.WARNING : undefined}
        icon={rules.custom ? 'changes' : status.without ? 'link' : 'warning-sign'}
        endIcon="caret-down"
        text={label}
        title={[rules.custom ? "These runs aren't paired with their reference the default way" : 'How runs find their reference run', attention].filter(Boolean).join(': ')}
        aria-label={`${label}${attention ? `, ${attention}` : ''}`}
        style={{ maxWidth: 320 }}
        ellipsizeText
      />
    </PopoverNext>
    {rules.custom && <Button size="small" variant="minimal" icon="cross" aria-label="Back to the default matching" title="Back to the default matching" onClick={() => save({ pairs: [], prefer: [], pins: {} })}/>}
  </span>;
};
