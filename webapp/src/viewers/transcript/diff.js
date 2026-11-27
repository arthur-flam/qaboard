// Compares two conversations: which messages are the same, changed, only in the new one or only in the reference,
// and inside a changed message, which words.

// Object keys in a fixed order: {a, b} and {b, a} are the same arguments
export function stableJson(value, indent) {
  const sort = x => Array.isArray(x) ? x.map(sort)
    : x !== null && typeof x === 'object' ? Object.fromEntries(Object.keys(x).sort().map(k => [k, sort(x[k])]))
    : x;
  return typeof value === 'string' ? value : JSON.stringify(sort(value), null, indent);
}

// What a message says, not what it cost: ids, tokens and times differ between any two runs
export function signature(message) {
  return [
    message.role,
    message.name ?? '',
    message.text,
    message.reasoning,
    message.error ?? '',
    message.attachments.join(','),
    ...message.tool_calls.map(call => `${call.name}(${stableJson(call.arguments)})`),
  ].join('\u0001');
}

// The pairs (i, j) of a longest common subsequence of a and b
function lcs(a, b) {
  const n = a.length, m = b.length;
  const width = m + 1;
  const table = new Uint32Array((n + 1) * width);
  for (let i = n - 1; i >= 0; i--)
    for (let j = m - 1; j >= 0; j--)
      table[i * width + j] = a[i] === b[j] ? table[(i + 1) * width + j + 1] + 1 : Math.max(table[(i + 1) * width + j], table[i * width + j + 1]);
  const pairs = [];
  let i = 0, j = 0;
  while (i < n && j < m) {
    if (a[i] === b[j]) pairs.push([i++, j++]);
    else if (table[(i + 1) * width + j] >= table[i * width + j + 1]) i++;
    else j++;
  }
  return pairs;
}

const MAX_CELLS = 4e6;

// Rows {status: same|changed|added|removed, new, reference, index_new, index_ref} in the order of the conversation.
// Messages that are the same anchor the alignment; between them, messages with the same role pair up as "changed".
export function alignMessages(new_messages, ref_messages) {
  const a = new_messages.map(signature), b = ref_messages.map(signature);
  // very long conversations: pair by position after the common start and end
  let anchors;
  if (a.length * b.length <= MAX_CELLS) anchors = lcs(a, b);
  else {
    anchors = [];
    let start = 0;
    while (start < a.length && start < b.length && a[start] === b[start]) anchors.push([start, start++]);
    const tail = [];
    let end = 0;
    while (end < a.length - start && end < b.length - start && a[a.length - 1 - end] === b[b.length - 1 - end]) tail.unshift([a.length - 1 - end, b.length - 1 - end++]);
    anchors.push(...tail);
  }
  const rows = [];
  let i = 0, j = 0;
  const gap = (i_end, j_end) => {
    while (i < i_end || j < j_end) {
      const m_new = i < i_end ? new_messages[i] : undefined, m_ref = j < j_end ? ref_messages[j] : undefined;
      if (m_new && m_ref && m_new.role === m_ref.role) rows.push({ status: 'changed', new: m_new, reference: m_ref, index_new: i++, index_ref: j++ });
      // roles differ: the side with more left to pair goes first, so that the rest can still line up
      else if (m_new && (!m_ref || i_end - i >= j_end - j)) rows.push({ status: 'added', new: m_new, reference: null, index_new: i++, index_ref: null });
      else rows.push({ status: 'removed', new: null, reference: m_ref, index_new: null, index_ref: j++ });
    }
  };
  for (const [i_same, j_same] of anchors) {
    gap(i_same, j_same);
    rows.push({ status: 'same', new: new_messages[i], reference: ref_messages[j], index_new: i++, index_ref: j++ });
  }
  gap(new_messages.length, ref_messages.length);
  return rows;
}

// Without a reference: every message is a row
export const singleRows = messages => messages.map((m, index) => ({ status: 'single', new: m, reference: null, index_new: index, index_ref: null }));

export const diffCounts = rows => rows.reduce((counts, row) => ({ ...counts, [row.status]: (counts[row.status] ?? 0) + 1 }), { same: 0, changed: 0, added: 0, removed: 0 });

// The indexes of the rows that start a run of differences: where "next difference" stops
export function differenceStarts(rows) {
  const starts = [];
  rows.forEach((row, index) => {
    const differs = row.status !== 'same' && row.status !== 'single';
    const previous = index > 0 && rows[index - 1].status !== 'same';
    if (differs && !previous) starts.push(index);
  });
  return starts;
}

const tokens = text => text.match(/\s+|\w+|[^\s\w]/g) ?? [];
const MAX_WORD_CELLS = 1e6;

// [{text, op: same|removed|added}] to go from `before` to `after`, word by word. `removed` is only in `before`.
export function wordDiff(before, after) {
  if (before === after) return before ? [{ text: before, op: 'same' }] : [];
  const a = tokens(before), b = tokens(after);
  let start = 0;
  while (start < a.length && start < b.length && a[start] === b[start]) start++;
  let end = 0;
  while (end < a.length - start && end < b.length - start && a[a.length - 1 - end] === b[b.length - 1 - end]) end++;
  const mid_a = a.slice(start, a.length - end), mid_b = b.slice(start, b.length - end);
  const out = [];
  const push = (text, op) => {
    if (!text) return;
    const last = out[out.length - 1];
    if (last?.op === op) last.text += text;
    else out.push({ text, op });
  };
  push(a.slice(0, start).join(''), 'same');
  if (mid_a.length * mid_b.length > MAX_WORD_CELLS) {
    push(mid_a.join(''), 'removed');
    push(mid_b.join(''), 'added');
  } else {
    let i = 0, j = 0;
    for (const [i_same, j_same] of lcs(mid_a, mid_b)) {
      push(mid_a.slice(i, i_same).join(''), 'removed');
      push(mid_b.slice(j, j_same).join(''), 'added');
      push(mid_a[i_same], 'same');
      i = i_same + 1;
      j = j_same + 1;
    }
    push(mid_a.slice(i).join(''), 'removed');
    push(mid_b.slice(j).join(''), 'added');
  }
  push(a.slice(a.length - end).join(''), 'same');
  return out;
}

// The segments one side shows: the new side hides what was removed, the reference what was added
export const sideSegments = (segments, side) => segments.filter(s => s.op === 'same' || s.op === (side === 'new' ? 'added' : 'removed'));

// The reference's sample for a new sample: same id and epoch, else the only one
export function matchSample(sample, ref_samples) {
  if (!sample || !ref_samples?.length) return null;
  return ref_samples.find(s => s.key === sample.key)
    ?? ref_samples.find(s => String(s.id) === String(sample.id) && s.epoch === sample.epoch)
    ?? (ref_samples.length === 1 ? ref_samples[0] : null);
}

// Tools whose definition differs from the reference's: {name: added|removed|changed}
export function toolChanges(tools, ref_tools) {
  const changes = {};
  const by_name = new Map(ref_tools.map(t => [t.name, t]));
  for (const tool of tools) {
    const ref = by_name.get(tool.name);
    if (!ref) changes[tool.name] = 'added';
    else if (ref.description !== tool.description || stableJson(ref.parameters) !== stableJson(tool.parameters)) changes[tool.name] = 'changed';
    by_name.delete(tool.name);
  }
  for (const name of by_name.keys()) changes[name] = 'removed';
  return changes;
}

// Does the message have the text searched (lower case)?
export function matches(message, query) {
  if (!query || !message) return false;
  return [message.text, message.reasoning, message.name ?? '', message.error ?? '', ...message.tool_calls.flatMap(c => [c.name, stableJson(c.arguments)])]
    .some(text => text.toLowerCase().includes(query));
}
