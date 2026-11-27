// A viewer for conversations with LLMs: the messages, tool calls and their results, the reasoning, the scores, tokens
// and times of a run, aligned with the reference run's conversation. Messages that are the same are shown once; those
// that changed side by side, with the words that differ. It reads Inspect's JSON logs and chat messages (parse.js).
// Standalone: it imports only React, Blueprint and its own files (viewers/__tests__/isolation.test.js).
import { useEffect, useMemo, useRef, useState } from "react";
import { Button, ButtonGroup, Callout, HTMLSelect, Icon, InputGroup, Tag, Tooltip } from "@blueprintjs/core";

import { parseTranscript, sampleStats } from "./parse";
import {
  alignMessages, differenceStarts, diffCounts, matches, matchSample, sideSegments, singleRows, stableJson, toolChanges, wordDiff,
} from "./diff";
import "./transcript.css";

const ROLES = {
  system: { label: 'System', icon: 'cog' },
  user: { label: 'User', icon: 'person' },
  assistant: { label: 'Assistant', icon: 'chat' },
  tool: { label: 'Tool', icon: 'wrench' },
};
const STATUS = { changed: 'Changed', added: 'Only in new', removed: 'Only in reference' };
// longer texts are folded, unless they changed or match the search
const LONG_TEXT = 1600;
// runs of identical messages longer than this are folded when showing changes only
const CONTEXT = 1;

export const SHORTCUTS = [
  ['n  p', 'Next / previous difference'],
  ['/', 'Search'],
  ['c', 'Changes only, or everything'],
  ['e', 'Open or fold the system prompts, reasoning and long messages'],
  ['[  ]', 'Previous / next sample'],
];

const DEFAULT_LABELS = {
  new: 'New', reference: 'Reference',
  colors: { new: { css: 'var(--qa-side-0, #2d72d2)' }, reference: { css: 'var(--qa-side-1, #9752fe)' } },
};

const format_count = n => n === null || n === undefined ? '' : n >= 1e6 ? `${(n / 1e6).toFixed(1)}M` : n >= 1e4 ? `${Math.round(n / 1e3)}k` : n >= 1e3 ? `${(n / 1e3).toFixed(1)}k` : String(n);
const format_time = s => s === null || s === undefined ? '' : s >= 90 ? `${Math.floor(s / 60)}m ${Math.round(s % 60)}s` : s >= 10 ? `${s.toFixed(0)}s` : s >= 1 ? `${s.toFixed(1)}s` : `${Math.round(s * 1000)}ms`;
const format_value = value => typeof value === 'number' ? String(Math.round(value * 1000) / 1000) : typeof value === 'string' ? value : JSON.stringify(value);

// JSON on one line when it's short, else indented: long tool results are easier to read, and to diff, on several lines
function layout(indented) {
  const line = indented.replace(/\n\s*/g, ' ');
  return line.length <= 80 ? line : indented;
}
function pretty(text) {
  const trimmed = text.trim();
  if (!(trimmed.startsWith('{') || trimmed.startsWith('['))) return text;
  try {
    return layout(JSON.stringify(JSON.parse(trimmed), null, 2));
  } catch {
    return text;
  }
}
const call_text = call => typeof call.arguments === 'string' ? call.arguments : layout(stableJson(call.arguments, 2));

// Text as segments {text, op}, with the search's matches marked
const Segments = ({ segments, query }) => segments.map((segment, index) => {
  const className = segment.op === 'added' ? 'qa-transcript-added' : segment.op === 'removed' ? 'qa-transcript-removed' : undefined;
  let content = segment.text;
  if (query) {
    const lower = segment.text.toLowerCase();
    const parts = [];
    let from = 0, at;
    while ((at = lower.indexOf(query, from)) !== -1) {
      if (at > from) parts.push(segment.text.slice(from, at));
      parts.push(<mark key={at} className="qa-transcript-mark">{segment.text.slice(at, at + query.length)}</mark>);
      from = at + query.length;
    }
    if (parts.length) content = [...parts, segment.text.slice(from)];
  }
  return className ? <span key={index} className={className}>{content}</span> : <span key={index}>{content}</span>;
});

// The segments this side shows of a text: against the other side's when the message changed
function segments_of(text, other_text, side) {
  if (other_text === undefined || other_text === text) return text ? [{ text, op: 'same' }] : [];
  return side === 'new' ? sideSegments(wordDiff(other_text, text), 'new') : sideSegments(wordDiff(text, other_text), 'reference');
}

// Something folded behind its title
const Fold = ({ title, open: open_default, children, className = '' }) => {
  const [open, setOpen] = useState(open_default);
  return <div className={`qa-transcript-fold ${className}`}>
    <button type="button" className="qa-transcript-fold-title" aria-expanded={open} onClick={() => setOpen(!open)}>
      <Icon icon={open ? 'chevron-down' : 'chevron-right'} size={12}/>{title}
    </button>
    {open && children}
  </div>;
};

const Body = ({ text, other_text, side, query, mono, open }) => {
  const [show_all, setShowAll] = useState(false);
  const segments = segments_of(text, other_text, side);
  if (!segments.length) return null;
  const long = text.length > LONG_TEXT && !open && !show_all;
  return <div className={`qa-transcript-text ${mono ? 'qa-transcript-mono' : ''} ${long ? 'qa-transcript-clamped' : ''}`}>
    <Segments segments={segments} query={query}/>
    {long && <button type="button" className="qa-transcript-more" onClick={() => setShowAll(true)}>Show all ({format_count(text.length)} characters)</button>}
  </div>;
};

// One message of one side. `other`: the message it's compared with, when it changed.
const Message = ({ message, other, side, query, expand, status }) => {
  const role = ROLES[message.role];
  const differs = other !== undefined;
  const found = matches(message, query);
  const open = expand || differs || found;
  const words = text => text.split(/\s+/).filter(Boolean).length;
  const meta = [
    message.usage?.total !== undefined && message.usage !== null ? `${format_count(message.usage.total)} tokens` : null,
    message.time !== null ? format_time(message.time) : null,
  ].filter(Boolean).join(' · ');
  const text = message.role === 'tool' ? pretty(message.text) : message.text;
  const other_text = differs ? (other.role === 'tool' ? pretty(other.text) : other.text) : undefined;
  const body = <>
    {message.reasoning && <Fold key={`reasoning-${open}`} open={open} className="qa-transcript-reasoning" title={<>Reasoning <span className="qa-transcript-muted">{words(message.reasoning)} words</span></>}>
      <Body text={message.reasoning} other_text={differs ? other.reasoning : undefined} side={side} query={query} open/>
    </Fold>}
    <Body text={text} other_text={other_text} side={side} query={query} mono={message.role === 'tool'} open={open}/>
    {message.attachments.length > 0 && <div className="qa-transcript-muted">
      <Icon icon="paperclip" size={12}/> {message.attachments.join(', ')} (not shown)
    </div>}
    {message.error && <div className="qa-transcript-error"><Icon icon="error" size={12}/> {message.error}</div>}
    {message.tool_calls.map((call, index) => {
      const other_call = differs ? other.tool_calls[index] : undefined;
      const name_changed = differs && other_call?.name !== call.name;
      return <div key={index} className="qa-transcript-call">
        <div className="qa-transcript-call-name">
          <Icon icon="wrench" size={12}/>
          <span className={name_changed ? (side === 'new' ? 'qa-transcript-added' : 'qa-transcript-removed') : undefined}>
            <Segments segments={[{ text: call.name, op: 'same' }]} query={query}/>
          </span>
        </div>
        <Body text={call_text(call)} other_text={differs ? (other_call ? call_text(other_call) : '') : undefined} side={side} query={query} mono open={open}/>
        {call.error && <div className="qa-transcript-error"><Icon icon="error" size={12}/> {call.error}</div>}
      </div>;
    })}
  </>;
  const title = <>
    <Icon icon={message.role === 'tool' && message.error ? 'error' : role.icon} size={13}/>
    <span className="qa-transcript-role">{role.label}</span>
    {message.role === 'tool' && message.name && <code className="qa-transcript-tool-name">{message.name}</code>}
    {status && <span className={`qa-transcript-status qa-transcript-status-${status}`}>{STATUS[status]}</span>}
  </>;
  return <div className={`qa-transcript-message qa-transcript-${message.role} ${message.error ? 'qa-transcript-failed' : ''}`}>
    <div className="qa-transcript-head">
      {message.role === 'system'
        ? <Fold key={`system-${open}`} open={open} className="qa-transcript-system-fold" title={<>{title}<span className="qa-transcript-muted">{words(message.text)} words</span></>}>{body}</Fold>
        : <div className="qa-transcript-title">{title}</div>}
      <span className="qa-transcript-meta">
        {meta}
        <Tooltip content="Copy the message" placement="top" compact>
          <button type="button" className="qa-transcript-copy" aria-label="Copy the message" onClick={() => navigator.clipboard?.writeText([message.reasoning, message.text, ...message.tool_calls.map(c => `${c.name}(${stableJson(c.arguments)})`)].filter(Boolean).join('\n\n'))}>
            <Icon icon="duplicate" size={12}/>
          </button>
        </Tooltip>
      </span>
    </div>
    {message.role !== 'system' && body}
  </div>;
};

const Stat = ({ label, value, reference, format = format_count, bad }) => {
  if (value === null || value === undefined) return null;
  const differs = reference !== null && reference !== undefined && reference !== value;
  return <span className="qa-transcript-stat">
    <span className="qa-transcript-stat-label">{label}</span>
    <span className={bad ? 'qa-transcript-bad' : undefined}>{format(value)}</span>
    {differs && <span className="qa-transcript-stat-ref" title="Reference">{format(reference)}</span>}
  </span>;
};

const ScoreValue = ({ score }) => {
  if (!score) return <span className="qa-transcript-muted">—</span>;
  const plain = ['C', 'I', true, false].includes(score.value);
  return <span className={score.passed === true ? 'qa-transcript-good' : score.passed === false ? 'qa-transcript-bad' : undefined}>
    {score.passed !== null && <Icon icon={score.passed ? 'tick-circle' : 'cross-circle'} size={13}/>}
    {' '}{plain ? (score.passed ? 'Passed' : 'Failed') : format_value(score.value)}
  </span>;
};

const Scores = ({ sample, reference, labels, query }) => {
  const names = [...new Set([...sample.scores, ...(reference?.scores ?? [])].map(s => s.name))];
  if (!names.length && !sample.target && !sample.error) return null;
  return <div className="qa-transcript-scores">
    {sample.error && <div className="qa-transcript-error"><Icon icon="error" size={12}/> The sample failed: {sample.error}</div>}
    {names.map(name => {
      const score = sample.scores.find(s => s.name === name), ref = reference?.scores.find(s => s.name === name);
      const flipped = ref && score && ref.passed !== score.passed;
      return <div key={name} className={`qa-transcript-score ${flipped ? 'qa-transcript-score-flipped' : ''}`}>
        <span className="qa-transcript-score-name">{name}</span>
        <ScoreValue score={score}/>
        {reference && <span className="qa-transcript-stat-ref"><span className="qa-transcript-muted">{labels.reference}:</span> <ScoreValue score={ref}/></span>}
        {score?.answer && <span className="qa-transcript-score-answer"><span className="qa-transcript-muted">Answer:</span> {score.answer}</span>}
        {score?.explanation && score.explanation !== score.answer && <Fold open={score.passed === false || !!query} title="Explanation" className="qa-transcript-score-why">
          <div className="qa-transcript-text"><Segments segments={[{ text: score.explanation, op: 'same' }]} query={query}/></div>
        </Fold>}
      </div>;
    })}
    {sample.target && <div className="qa-transcript-score"><span className="qa-transcript-score-name">Target</span><span>{sample.target}</span></div>}
  </div>;
};

const Tools = ({ sample, reference, expand }) => {
  const ref_tools = reference?.tools ?? [];
  const changes = reference ? toolChanges(sample.tools, ref_tools) : {};
  const changed = Object.keys(changes).length;
  const names = [...sample.tools.map(t => t.name), ...ref_tools.map(t => t.name).filter(n => !sample.tools.some(t => t.name === n))];
  if (!names.length) return null;
  return <Fold key={`tools-${expand}`} open={expand || changed > 0} className="qa-transcript-tools" title={<>
    Tools the model was given <span className="qa-transcript-muted">{sample.tools.length}</span>
    {changed > 0 && <span className="qa-transcript-status qa-transcript-status-changed">{changed} differ from the reference</span>}
  </>}>
    {names.map(name => {
      const tool = sample.tools.find(t => t.name === name), ref = ref_tools.find(t => t.name === name);
      const shown = tool ?? ref;
      return <div key={name} className="qa-transcript-tooldef">
        <code className="qa-transcript-tool-name">{name}</code>
        {changes[name] && <span className={`qa-transcript-status qa-transcript-status-${changes[name]}`}>{STATUS[changes[name]]}</span>}
        <div className="qa-transcript-text">
          <Segments segments={tool && ref ? wordDiff(ref.description, tool.description) : [{ text: shown.description, op: 'same' }]}/>
        </div>
        {shown.parameters && <Fold open={false} title="Parameters"><div className="qa-transcript-text qa-transcript-mono">
          <Segments segments={tool && ref ? wordDiff(stableJson(ref.parameters, 2), stableJson(tool.parameters, 2)) : [{ text: stableJson(shown.parameters, 2), op: 'same' }]}/>
        </div></Fold>}
      </div>;
    })}
  </Fold>;
};

const sample_label = (sample, reference, many_epochs) => {
  const score = sample.scores.find(s => s.passed !== null);
  const ref_score = reference?.scores.find(s => s.name === score?.name);
  const mark = score ? (score.passed ? '✓' : '✗') : sample.error ? '!' : '';
  const flipped = score && ref_score && ref_score.passed !== null && ref_score.passed !== score.passed;
  return `${mark} ${sample.id}${many_epochs ? ` · epoch ${sample.epoch}` : ''}${flipped ? (score.passed ? '  (failed in reference)' : '  (passed in reference)') : ''}`.trim();
};

// Runs of identical messages become one line, but those next to a difference
function fold_same(rows, opened) {
  const items = [];
  let i = 0;
  while (i < rows.length) {
    if (rows[i].status !== 'same') {
      items.push({ row: rows[i], index: i++ });
      continue;
    }
    let end = i;
    while (end < rows.length && rows[end].status === 'same') end++;
    const from = i === 0 ? i : i + CONTEXT, to = end === rows.length ? end : end - CONTEXT;
    if (to - from < 2 || opened.has(i)) for (let k = i; k < end; k++) items.push({ row: rows[k], index: k });
    else {
      for (let k = i; k < from; k++) items.push({ row: rows[k], index: k });
      items.push({ folded: to - from, start: i, index: from });
      for (let k = to; k < end; k++) items.push({ row: rows[k], index: k });
    }
    i = end;
  }
  return items;
}

const Transcript = ({ transcript, reference, labels: labels_, height }) => {
  const labels = { ...DEFAULT_LABELS, ...labels_, colors: { ...DEFAULT_LABELS.colors, ...labels_?.colors } };
  const root = useRef(null);
  const search = useRef(null);
  const [sample_key, setSampleKey] = useState(null);
  const [view, setView] = useState('both');
  const [query_typed, setQuery] = useState('');
  const [changes_only, setChangesOnly] = useState(false);
  const [expand, setExpand] = useState(false);
  const [hidden_roles, setHiddenRoles] = useState([]);
  const [opened, setOpened] = useState([]);
  const [cursor, setCursor] = useState(-1);
  const [help, setHelp] = useState(false);

  const samples = transcript.samples;
  const sample = samples.find(s => s.key === sample_key) ?? samples[0];
  const ref_sample = matchSample(sample, reference?.samples);
  const compared = !!ref_sample && view === 'both';
  const query = query_typed.trim().toLowerCase();
  const many_epochs = new Set(samples.map(s => s.epoch)).size > 1;

  const all_rows = useMemo(
    () => compared ? alignMessages(sample.messages, ref_sample.messages) : singleRows(view === 'reference' && ref_sample ? ref_sample.messages : sample.messages),
    [compared, sample, ref_sample, view],
  );
  const role_of = row => (row.new ?? row.reference).role;
  const rows = all_rows.filter(row => !hidden_roles.includes(role_of(row)) && (!query || matches(row.new, query) || matches(row.reference, query)));
  const counts = diffCounts(all_rows);
  const differences = counts.changed + counts.added + counts.removed;
  const starts = differenceStarts(rows);
  const items = compared && changes_only && !query ? fold_same(rows, new Set(opened)) : rows.map((row, index) => ({ row, index }));
  const stats = sampleStats(sample), ref_stats = ref_sample ? sampleStats(ref_sample) : {};
  const side = view === 'reference' && ref_sample ? 'reference' : 'new';

  const scroll_to = index => root.current?.querySelector(`[data-row="${index}"]`)?.scrollIntoView?.({ block: 'center', behavior: 'smooth' });
  const go = step => {
    if (!starts.length) return;
    const next = cursor < 0 ? (step > 0 ? 0 : starts.length - 1) : (cursor + step + starts.length) % starts.length;
    setCursor(next);
    scroll_to(starts[next]);
  };
  const select_sample = key => {
    setSampleKey(key);
    setCursor(-1);
    setOpened([]);
  };
  const step_sample = step => {
    const at = samples.indexOf(sample) + step;
    if (at >= 0 && at < samples.length) select_sample(samples[at].key);
  };
  const on_key = event => {
    if (event.target.closest?.('input, textarea, select') || event.ctrlKey || event.metaKey || event.altKey) return;
    const actions = {
      n: () => go(1), p: () => go(-1), c: () => compared && setChangesOnly(!changes_only), e: () => setExpand(!expand),
      '/': () => search.current?.focus(), '[': () => step_sample(-1), ']': () => step_sample(1), '?': () => setHelp(!help),
    };
    if (actions[event.key]) {
      event.preventDefault();
      actions[event.key]();
    }
  };
  // shortcuts while the focus is in the viewer: on one of its buttons, or after a click in the conversation
  useEffect(() => {
    const element = root.current;
    element?.addEventListener('keydown', on_key);
    return () => element?.removeEventListener('keydown', on_key);
  });
  const toggle_role = role => setHiddenRoles(hidden_roles.includes(role) ? hidden_roles.filter(r => r !== role) : [...hidden_roles, role]);

  return <div
    className="qa-transcript" ref={root} role="group" aria-label="Conversation"
    style={{ '--transcript-new': labels.colors.new.css, '--transcript-reference': labels.colors.reference.css }}
  >
    {(transcript.eval || samples.length > 1) && <div className="qa-transcript-bar">
      {samples.length > 1 && <>
        <ButtonGroup>
          <Button size="small" icon="chevron-left" aria-label="Previous sample" disabled={samples[0] === sample} onClick={() => step_sample(-1)}/>
          <Button size="small" icon="chevron-right" aria-label="Next sample" disabled={samples[samples.length - 1] === sample} onClick={() => step_sample(1)}/>
        </ButtonGroup>
        <HTMLSelect aria-label="Sample" value={sample.key} onChange={e => select_sample(e.currentTarget.value)}>
          {samples.map(s => <option key={s.key} value={s.key}>{sample_label(s, matchSample(s, reference?.samples), many_epochs)}</option>)}
        </HTMLSelect>
        <span className="qa-transcript-muted">{samples.indexOf(sample) + 1} of {samples.length}</span>
      </>}
      {transcript.eval?.task && <Tag minimal>{transcript.eval.task}</Tag>}
      {transcript.eval?.model && <Tag minimal icon="cube">{transcript.eval.model}</Tag>}
      {transcript.eval?.metrics.filter(m => m.name !== 'stderr').map(m => <Tag key={`${m.scorer}/${m.name}`} minimal>{m.scorer} {m.name} {format_value(m.value)}</Tag>)}
    </div>}

    <div className="qa-transcript-stats">
      <Stat label="Turns" value={stats.turns} reference={ref_stats.turns}/>
      <Stat label="Tool calls" value={stats.tool_calls} reference={ref_stats.tool_calls}/>
      {(stats.tool_errors > 0 || ref_stats.tool_errors > 0) && <Stat label="Tool errors" value={stats.tool_errors} reference={ref_stats.tool_errors} bad={stats.tool_errors > 0}/>}
      <Stat label="Tokens" value={stats.tokens} reference={ref_stats.tokens}/>
      <Stat label="Time" value={stats.time} reference={ref_stats.time} format={format_time}/>
      {ref_sample && <span className="qa-transcript-stat">
        <span className="qa-transcript-stat-label">Against {labels.reference}</span>
        {differences === 0 ? <span className="qa-transcript-good">the same conversation</span> : <span>
          {[counts.changed && `${counts.changed} changed`, counts.added && `${counts.added} only in new`, counts.removed && `${counts.removed} only in reference`].filter(Boolean).join(', ')}
        </span>}
      </span>}
      {reference && !ref_sample && <span className="qa-transcript-muted">The reference doesn't have this sample</span>}
    </div>

    <Scores sample={side === 'reference' ? ref_sample : sample} reference={compared ? ref_sample : null} labels={labels} query={query}/>

    <div className="qa-transcript-bar">
      <InputGroup
        inputRef={search} size="small" leftIcon="search" placeholder="Search the messages" aria-label="Search the messages" value={query_typed}
        onChange={e => setQuery(e.target.value)} onKeyDown={e => e.key === 'Escape' && setQuery('')}
        rightElement={query ? <Tag minimal>{rows.length} of {all_rows.length}</Tag> : undefined}
      />
      {ref_sample && <ButtonGroup>
        <Button size="small" active={view === 'both'} onClick={() => setView('both')}>Compare</Button>
        <Button size="small" active={view === 'new'} onClick={() => setView('new')}>{labels.new}</Button>
        <Button size="small" active={view === 'reference'} onClick={() => setView('reference')}>{labels.reference}</Button>
      </ButtonGroup>}
      {compared && <>
        <Button size="small" active={changes_only} disabled={!differences} onClick={() => setChangesOnly(!changes_only)}>Changes only</Button>
        <ButtonGroup>
          <Button size="small" icon="arrow-up" aria-label="Previous difference" disabled={!starts.length} onClick={() => go(-1)}/>
          <Button size="small" icon="arrow-down" disabled={!starts.length} onClick={() => go(1)}>
            {starts.length ? (cursor < 0 ? 'First difference' : `Difference ${cursor + 1} of ${starts.length}`) : 'No difference'}
          </Button>
        </ButtonGroup>
      </>}
      <Button size="small" variant="minimal" icon={expand ? 'collapse-all' : 'expand-all'} onClick={() => setExpand(!expand)}>{expand ? 'Fold' : 'Open all'}</Button>
      <Button size="small" variant="minimal" icon="key-command" aria-label="Keyboard shortcuts" active={help} onClick={() => setHelp(!help)}/>
    </div>
    {help && <div className="qa-transcript-help">
      {SHORTCUTS.map(([keys, what]) => <span key={keys}><kbd>{keys}</kbd> {what}</span>)}
    </div>}

    <div className="qa-transcript-outline" role="group" aria-label="Outline of the conversation">
      {Object.entries(ROLES).filter(([role]) => all_rows.some(row => role_of(row) === role)).map(([role, { label }]) =>
        <button key={role} type="button" aria-pressed={!hidden_roles.includes(role)} className={`qa-transcript-legend qa-transcript-${role}`} onClick={() => toggle_role(role)} title={hidden_roles.includes(role) ? `Show ${label} messages` : `Hide ${label} messages`}>
          <i/>{label}
        </button>)}
      <span className="qa-transcript-cells">
        {rows.map((row, index) => <button
          key={index} type="button" className={`qa-transcript-cell qa-transcript-${role_of(row)} qa-transcript-cell-${row.status}`}
          title={`${index + 1}. ${ROLES[role_of(row)].label}${STATUS[row.status] ? `: ${STATUS[row.status].toLowerCase()}` : ''}`}
          aria-label={`Go to message ${index + 1}`}
          onClick={() => {
            if (changes_only && row.status === 'same') setChangesOnly(false);
            requestAnimationFrame(() => scroll_to(index));
          }}
        />)}
      </span>
    </div>

    {compared && <div className="qa-transcript-sides">
      <span style={{ color: 'var(--transcript-new)' }}>{labels.new}</span>
      <span style={{ color: 'var(--transcript-reference)' }}>{labels.reference}</span>
    </div>}
    <div className="qa-transcript-body" tabIndex={-1} style={height ? { maxHeight: height } : undefined}>
      <Tools sample={side === 'reference' ? ref_sample : sample} reference={compared ? ref_sample : null} expand={expand}/>
      {items.map(item => {
        if (item.folded) return <button key={`fold-${item.start}`} type="button" className="qa-transcript-same-run" onClick={() => setOpened([...opened, item.start])}>
          <Icon icon="expand-all" size={12}/> {item.folded} identical messages
        </button>;
        const { row, index } = item;
        const current = cursor >= 0 && starts[cursor] === index;
        if (row.status === 'same' || row.status === 'single')
          return <div key={index} data-row={index} className={`qa-transcript-row qa-transcript-row-${row.status}`}>
            <Message message={row.new} side={side} query={query} expand={expand}/>
          </div>;
        return <div key={index} data-row={index} className={`qa-transcript-row qa-transcript-row-pair ${current ? 'qa-transcript-row-current' : ''}`}>
          {row.new
            ? <Message message={row.new} other={row.reference ?? undefined} side="new" query={query} expand={expand} status={row.status}/>
            : <div className="qa-transcript-absent">Not in {labels.new}</div>}
          {row.reference
            ? <Message message={row.reference} other={row.new ?? undefined} side="reference" query={query} expand={expand} status={row.status === 'changed' ? undefined : row.status}/>
            : <div className="qa-transcript-absent">Not in {labels.reference}</div>}
        </div>;
      })}
      {!rows.length && <div className="qa-transcript-muted qa-transcript-empty">{query ? 'No message has this text.' : 'No messages to show.'}</div>}
    </div>
  </div>;
};

// - text, ref_text: the files' contents; without ref_text, the conversation alone
// - labels: {new, reference, colors: {new: {css}, reference: {css}}}, what to call the sides
// - fallback(message): what to show when `text` isn't a conversation we read, instead of an error
export const TranscriptReport = ({ text, ref_text, labels, path, height, fallback }) => {
  const parsed = useMemo(() => {
    try {
      return { transcript: parseTranscript(text) };
    } catch (error) {
      return { error: error.message };
    }
  }, [text]);
  const reference = useMemo(() => {
    if (ref_text === null || ref_text === undefined) return null;
    try {
      return parseTranscript(ref_text);
    } catch {
      return null;
    }
  }, [ref_text]);
  if (parsed.error) return fallback ? fallback(parsed.error) : <Callout compact intent="warning" icon="warning-sign">
    Can't read {path ? <code>{path}</code> : 'the file'} as a conversation: {parsed.error}.
  </Callout>;
  return <Transcript transcript={parsed.transcript} reference={reference} labels={labels} height={height}/>;
};

export default TranscriptReport;
