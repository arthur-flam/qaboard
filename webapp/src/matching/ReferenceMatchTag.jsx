// On run cards, how the run found its reference run: nothing in the usual case (the same input and settings), a quiet
// tag when it's another input, a warning when there's a choice to make or no reference. It opens the reference picker,
// as does the run's menu (useReferencePicker). Cards are many: the popover only exists while it's open.
import { lazy, Suspense } from "react";
import { Classes, Intent, PopoverNext, Spinner, Tag } from "@blueprintjs/core";

import { useComparison } from "../hooks";
import { useReferencePicker } from "./pickerStore";
import { name_changes, rename, rule_of_change, similar_positions } from "./names";
import { fact_text, run_facts } from "./rules";

const ReferencePicker = lazy(() => import("./ReferencePicker"));

const tag_style = { marginLeft: 6, verticalAlign: 'baseline', cursor: 'pointer' };
const quiet_style = { ...tag_style, background: 'none' };

// The tag, or null when there's nothing to say: { text, title, intent, icon, quiet, clickable }
const describe = (output, match, reference, ref_lookup, rules) => {
  if (!match) {
    const name = rename(output.test_input_path, rules.pairs) ?? output.test_input_path;
    const similar = ref_lookup ? similar_positions(name, ref_lookup).length : 0;
    return {
      text: similar ? `no reference · ${similar} similar` : 'no reference',
      title: similar
        ? `No reference run on this input${output.test_input_metadata?.id ? ' or with its id' : ''}. ${similar} reference run${similar > 1 ? 's have' : ' has'} a similar name: click to pick one.`
        : 'No reference run on this input, with its id, or with a similar name',
      intent: Intent.WARNING, icon: 'disable', clickable: similar > 0,
    };
  }
  if (match.by === 'pin') return { text: 'picked', title: 'You picked this reference run, for this run only', icon: 'pin', clickable: true };
  if (match.preferred && reference) {
    const facts = run_facts(reference);
    const why = rules.prefer.filter(p => facts[p.key] === p.value).map(fact_text).join(', ');
    return { text: why, title: `Picked because you prefer reference runs with ${why}`, icon: 'star', clickable: true };
  }
  if (match.tied > 0) return {
    text: `${match.tied + 1} tied`,
    title: `${match.tied + 1} reference runs are as close to this run as each other: we took the first. Click to pick.`,
    intent: Intent.WARNING, icon: 'warning-sign', clickable: true,
  };
  if (match.by !== 'id' && match.by !== 'pair') return null;
  // another input: say which
  const changes = reference ? name_changes(output.test_input_path ?? '', reference.test_input_path ?? '') : null;
  const text = match.by === 'id'
    ? output.test_input_metadata?.id
    : changes?.length ? changes.map(c => rule_of_change(c).to).join(' ') : reference?.test_input_path;
  const others = match.others > 0 ? ` ${match.others} other reference run${match.others > 1 ? 's' : ''} could do: click to pick.` : '';
  return {
    text: `≈ ${text}${match.others > 0 ? ` · ${match.others + 1}` : ''}`,
    title: `Compared with ${reference?.test_input_path}, ${match.by === 'id' ? 'which has the same metadata.id' : 'paired by name'}.${others}`,
    quiet: true, clickable: match.others > 0,
  };
};

export const ReferenceMatchTag = ({ output }) => {
  const comparison = useComparison();
  const is_open = useReferencePicker(state => state.open_for === String(output.id));
  const open = useReferencePicker(state => state.open);
  const close = useReferencePicker(state => state.close);
  if (!comparison) return null;
  const { ref_batch, ref_lookup, selected } = comparison;
  // nothing to compare with: no reference batch, or a run that has no reference by nature
  const has_reference_runs = (ref_lookup?.outputs?.length ?? 0) > 0;
  if (!has_reference_runs || output.is_pending || output.output_type === 'optim_iteration' || output.output_type === 'batch') return null;

  const reference = ref_batch?.outputs?.[output.reference_id];
  const tag = describe(output, output.reference_match, reference, ref_lookup, selected.reference_rules);
  const target = tag
    ? <Tag
        minimal
        interactive={tag.clickable}
        intent={tag.intent}
        icon={tag.icon}
        className={tag.quiet ? Classes.TEXT_MUTED : undefined}
        style={tag.quiet ? quiet_style : { ...tag_style, cursor: tag.clickable ? 'pointer' : 'help' }}
        title={tag.title}
        onClick={tag.clickable ? () => open(output.id) : undefined}
        role={tag.clickable ? 'button' : undefined}
        tabIndex={tag.clickable ? 0 : undefined}
        onKeyDown={tag.clickable ? event => (event.key === 'Enter' || event.key === ' ') && open(output.id) : undefined}
        aria-haspopup={tag.clickable ? 'dialog' : undefined}
      >{tag.text}</Tag>
    // opened from the run's menu: the picker needs a target
    : <span aria-hidden="true"/>;
  if (!is_open) return target;
  return <PopoverNext
    isOpen
    placement="bottom-start"
    onClose={close}
    content={<Suspense fallback={<Spinner size={20} style={{ margin: 12 }}/>}><ReferencePicker output={output} onClose={close}/></Suspense>}
  >
    {target}
  </PopoverNext>;
};
