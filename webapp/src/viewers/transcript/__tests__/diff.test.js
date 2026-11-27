import fs from 'fs';
import path from 'path';

import { parseTranscript } from '../parse';
import { alignMessages, diffCounts, differenceStarts, matchSample, matches, sideSegments, stableJson, toolChanges, wordDiff } from '../diff';

const fixture = name => parseTranscript(fs.readFileSync(path.join(import.meta.dirname, '../../../../../tests/fixtures/transcripts', name), 'utf8'));
const m = (role, text, extra = {}) => ({ role, text, reasoning: '', attachments: [], tool_calls: [], error: null, ...extra });
const call = (name, args) => ({ name, arguments: args });

describe('alignMessages', () => {
  it('finds the messages that changed, and those only one side has', () => {
    const reference = [m('system', 'Ask first.'), m('user', 'Reboot r2'), m('assistant', 'Do you confirm?'), m('user', 'Yes'), m('assistant', '', { tool_calls: [call('reboot', { robot: 'r2' })] }), m('tool', 'ok'), m('assistant', 'Done.')];
    const run = [m('system', 'Be brief.'), m('user', 'Reboot r2'), m('assistant', '', { tool_calls: [call('reboot', { robot: 'r2' })] }), m('tool', 'ok'), m('assistant', 'Done.')];
    const rows = alignMessages(run, reference);
    expect(rows.map(r => r.status)).toEqual(['changed', 'same', 'removed', 'removed', 'same', 'same', 'same']);
    expect(diffCounts(rows)).toEqual({ same: 4, changed: 1, added: 0, removed: 2 });
    expect(differenceStarts(rows)).toEqual([0, 2]);
    expect(rows[4]).toMatchObject({ index_new: 2, index_ref: 4 });
  });

  it("doesn't see a difference in the order of arguments, ids, tokens or times", () => {
    const a = m('assistant', '', { id: 'x', usage: { total: 3 }, time: 1, tool_calls: [{ id: '1', ...call('f', { a: 1, b: { c: 2, d: 3 } }) }] });
    const b = m('assistant', '', { id: 'y', usage: { total: 9 }, time: 2, tool_calls: [{ id: '2', ...call('f', { b: { d: 3, c: 2 }, a: 1 }) }] });
    expect(alignMessages([a], [b]).map(r => r.status)).toEqual(['same']);
    expect(stableJson({ b: 1, a: [{ d: 1, c: 2 }] })).toBe('{"a":[{"c":2,"d":1}],"b":1}');
  });

  it('pairs what it can when nothing is the same', () => {
    const rows = alignMessages([m('user', 'a'), m('assistant', 'b'), m('assistant', 'c')], [m('user', 'x'), m('assistant', 'y')]);
    expect(rows.map(r => r.status)).toEqual(['changed', 'changed', 'added']);
  });

  it('compares the samples of real Inspect logs', () => {
    const run = fixture('inspect-new.json'), reference = fixture('inspect-reference.json');
    const sample = run.samples[1], ref = matchSample(sample, reference.samples);
    expect(ref.key).toBe('battery-r9#1');
    // the system prompt and the last answer changed
    expect(alignMessages(sample.messages, ref.messages).map(r => r.status)).toEqual(['changed', 'same', 'same', 'same', 'changed']);
    expect(matchSample({ key: 'other#1', id: 'other', epoch: 1 }, reference.samples)).toBe(null);
  });
});

describe('wordDiff', () => {
  it('marks the words that differ', () => {
    const segments = wordDiff('r2 is rebooting and should be back soon.', 'r2 is rebooting, back soon.');
    expect(sideSegments(segments, 'reference').map(s => s.text).join('')).toBe('r2 is rebooting and should be back soon.');
    expect(sideSegments(segments, 'new').map(s => s.text).join('')).toBe('r2 is rebooting, back soon.');
    expect(segments.filter(s => s.op === 'removed').map(s => s.text.trim())).toEqual(['and should be']);
    expect(segments.filter(s => s.op === 'added').map(s => s.text)).toEqual([',']);
  });

  it('handles texts that are the same, empty, or very long', () => {
    expect(wordDiff('same', 'same')).toEqual([{ text: 'same', op: 'same' }]);
    expect(wordDiff('', 'new')).toEqual([{ text: 'new', op: 'added' }]);
    const long_a = Array.from({ length: 3000 }, (_, i) => `a${i}`).join(' '), long_b = Array.from({ length: 3000 }, (_, i) => `b${i}`).join(' ');
    expect(wordDiff(`start ${long_a} end`, `start ${long_b} end`).map(s => s.op)).toEqual(['same', 'removed', 'added', 'same']);
  });
});

it('finds the tools whose definition changed', () => {
  const tool = (name, description, parameters = null) => ({ name, description, parameters });
  expect(toolChanges(
    [tool('a', 'short'), tool('b', 'same', { x: 1 }), tool('c', 'new')],
    [tool('a', 'long description'), tool('b', 'same', { x: 1 }), tool('d', 'gone')],
  )).toEqual({ a: 'changed', c: 'added', d: 'removed' });
});

it('searches the text, the reasoning, and tool calls', () => {
  const message = m('assistant', 'Checking', { reasoning: 'Battery first', tool_calls: [call('battery', { robot: 'r2' })] });
  expect(['checking', 'battery first', 'battery', 'r2'].map(q => matches(message, q))).toEqual([true, true, true, true]);
  expect(matches(message, 'reboot')).toBe(false);
  expect(matches(message, '')).toBe(false);
});
