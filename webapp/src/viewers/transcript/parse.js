// Reads conversations with LLMs into one shape, whatever wrote them:
// - Inspect's eval logs as JSON (`inspect eval --log-format json`, `inspect log convert --to json`): the whole eval,
//   with its samples and epochs; or one sample (an EvalSample)
// - {messages: [...]} or a list of messages, in Inspect's, OpenAI's (chat completions, responses) or Anthropic's
//   shape; as JSON, or JSON lines with a message or a conversation per line
// Our words are Inspect's (https://inspect.aisi.org.uk/): sample, epoch, messages, scores.
//
// parseTranscript(text) returns
//   { format, eval: {task, model, created, status, metrics: [{scorer, name, value}]} | null, samples: [sample] }
//   sample:  { key, id, epoch, input, target, messages, tools, scores, usage, time, error, metadata }
//   message: { role: system|user|assistant|tool, text, reasoning, attachments: [type], tool_calls: [{id, name, arguments}],
//              tool_call_id, name, error, model, usage: {input, output, total} | null, time: seconds | null }
//   tool:    { name, description, parameters }
//   score:   { name, value, passed: true|false|null, answer, explanation }
// and throws an Error that says why when the text isn't a conversation.

const is_object = x => x !== null && typeof x === 'object' && !Array.isArray(x);
const as_text = x => x === undefined || x === null ? '' : typeof x === 'string' ? x : JSON.stringify(x, null, 2);

// Tool calls' arguments: an object. OpenAI sends them as a JSON string.
function parse_arguments(args) {
  if (typeof args !== 'string') return args ?? {};
  try {
    const parsed = JSON.parse(args);
    return is_object(parsed) ? parsed : args;
  } catch {
    return args;
  }
}

function tool_error(error) {
  if (!error) return null;
  if (typeof error === 'string') return error;
  return [error.type, error.message].filter(Boolean).join(': ') || JSON.stringify(error);
}

// The parts of a message's content: text, reasoning, tool calls and results (Anthropic keeps them in the content),
// and the kinds of what we don't show (images, audio...)
function read_content(content) {
  const out = { text: [], reasoning: [], attachments: [], tool_calls: [], tool_results: [] };
  if (typeof content === 'string') {
    out.text.push(content);
    return out;
  }
  if (is_object(content)) content = [content];
  if (!Array.isArray(content)) return out;
  for (const part of content) {
    if (typeof part === 'string') out.text.push(part);
    else if (!is_object(part)) continue;
    else if (['text', 'input_text', 'output_text', 'refusal'].includes(part.type) || (part.type === undefined && typeof part.text === 'string'))
      out.text.push(part.text ?? part.refusal ?? '');
    else if (part.type === 'reasoning' || part.type === 'thinking' || part.type === 'redacted_thinking')
      out.reasoning.push(part.redacted || part.type === 'redacted_thinking' ? '(redacted)' : part.reasoning ?? part.thinking ?? part.summary ?? '');
    else if (part.type === 'tool_use' && part.name)
      out.tool_calls.push({ id: part.id, name: part.name, arguments: parse_arguments(part.input ?? part.arguments) });
    else if (part.type === 'tool_result')
      out.tool_results.push({ tool_call_id: part.tool_use_id, text: read_content(part.content).text.join('\n'), error: part.is_error ? 'error' : null });
    else out.attachments.push(part.type || 'data');
  }
  return out;
}

const ROLES = { system: 'system', developer: 'system', user: 'user', human: 'user', assistant: 'assistant', ai: 'assistant', model: 'assistant', tool: 'tool', function: 'tool' };

// One message as written, to one or more of ours (Anthropic's tool results are parts of a user message)
function read_message(raw) {
  if (!is_object(raw)) return [];
  // OpenAI's responses API: items that aren't messages
  if (raw.type === 'function_call')
    return [message('assistant', { tool_calls: [{ id: raw.call_id ?? raw.id, name: raw.name, arguments: parse_arguments(raw.arguments) }] })];
  if (raw.type === 'function_call_output')
    return [message('tool', { text: as_text(raw.output), tool_call_id: raw.call_id })];
  if (raw.type === 'reasoning' && !raw.role)
    return [message('assistant', { reasoning: read_content(raw.summary ?? raw.content).text.join('\n') })];

  const role = ROLES[String(raw.role ?? '').toLowerCase()];
  if (!role) return [];
  const content = read_content(raw.content);
  const tool_calls = [
    ...content.tool_calls,
    ...(Array.isArray(raw.tool_calls) ? raw.tool_calls : []).filter(is_object).map(call => {
      // Inspect: {function: "name", arguments: {}}; OpenAI: {function: {name, arguments: "{}"}}
      const fn = is_object(call.function) ? call.function : { name: call.function ?? call.name, arguments: call.arguments };
      return { id: call.id, name: String(fn.name ?? '?'), arguments: parse_arguments(fn.arguments), ...(call.parse_error ? { error: call.parse_error } : {}) };
    }),
  ];
  const reasoning = [...content.reasoning, ...(typeof raw.reasoning_content === 'string' ? [raw.reasoning_content] : []), ...(typeof raw.reasoning === 'string' ? [raw.reasoning] : [])];
  const messages = [];
  // tool results first: they answer the previous message's calls
  for (const result of content.tool_results) messages.push(message('tool', result));
  const has_own = content.text.some(Boolean) || reasoning.length || tool_calls.length || content.attachments.length;
  if (has_own || !content.tool_results.length)
    messages.push(message(role, {
      id: raw.id,
      text: content.text.join('\n'),
      reasoning: reasoning.filter(Boolean).join('\n\n'),
      attachments: content.attachments,
      tool_calls,
      tool_call_id: raw.tool_call_id,
      name: role === 'tool' ? (typeof raw.function === 'string' ? raw.function : raw.name) : undefined,
      error: role === 'tool' ? tool_error(raw.error) : null,
      model: raw.model,
    }));
  return messages;
}

const message = (role, fields) => ({
  role, id: undefined, text: '', reasoning: '', attachments: [], tool_calls: [], tool_call_id: undefined, name: undefined, error: null,
  model: undefined, usage: null, time: null, ...fields,
});

// Tool results name their tool when the message doesn't: from the call they answer
function name_tool_results(messages) {
  const names = new Map();
  for (const m of messages) {
    for (const call of m.tool_calls) if (call.id) names.set(call.id, call.name);
    if (m.role === 'tool' && !m.name && names.has(m.tool_call_id)) m.name = names.get(m.tool_call_id);
  }
  return messages;
}

const read_messages = raw => name_tool_results((Array.isArray(raw) ? raw : []).flatMap(read_message));

// Tool definitions: Inspect's {name, description, parameters}, OpenAI's {type: function, function: {...}},
// Anthropic's {name, description, input_schema}
function read_tools(raw) {
  if (!Array.isArray(raw)) return [];
  return raw.filter(is_object).map(t => {
    const fn = is_object(t.function) ? t.function : t;
    return { name: String(fn.name ?? '?'), description: fn.description ?? '', parameters: fn.parameters ?? fn.input_schema ?? null };
  }).filter(t => t.name !== '?');
}

// Inspect's scores: "C" correct, "I" incorrect, "P" partial, "N" no answer; or numbers, booleans, objects
function passed(value) {
  if (value === 'C' || value === true || value === 1) return true;
  if (value === 'I' || value === 'N' || value === false || value === 0) return false;
  return null;
}
function read_scores(raw) {
  if (!is_object(raw)) return [];
  return Object.entries(raw).map(([name, score]) => {
    const s = is_object(score) && 'value' in score ? score : { value: score };
    return { name, value: s.value, passed: passed(s.value), answer: s.answer ?? null, explanation: s.explanation ?? null };
  });
}

const TOKENS = { input: 'input_tokens', output: 'output_tokens', total: 'total_tokens', reasoning: 'reasoning_tokens', cache_read: 'input_tokens_cache_read' };
function read_usage(raw) {
  if (!is_object(raw)) return null;
  const usage = {};
  for (const [key, field] of Object.entries(TOKENS)) {
    const value = raw[field] ?? (key === 'input' ? raw.prompt_tokens : key === 'output' ? raw.completion_tokens : undefined);
    if (Number.isFinite(value)) usage[key] = value;
  }
  if (usage.total === undefined && (usage.input !== undefined || usage.output !== undefined)) usage.total = (usage.input ?? 0) + (usage.output ?? 0);
  return Object.keys(usage).length ? usage : null;
}
// model_usage: {"<model>": usage}, summed
function sum_usage(by_model) {
  if (!is_object(by_model)) return null;
  const total = {};
  for (const raw of Object.values(by_model)) {
    const usage = read_usage(raw);
    for (const [k, v] of Object.entries(usage ?? {})) total[k] = (total[k] ?? 0) + v;
  }
  return Object.keys(total).length ? total : null;
}

// Inspect's events say what each model call cost and how long each tool took, and which tools the model was given
function read_events(events, messages) {
  const by_id = new Map(messages.filter(m => m.id).map(m => [m.id, m]));
  let tools = [];
  for (const event of Array.isArray(events) ? events : []) {
    if (!is_object(event)) continue;
    if (event.event === 'model') {
      if (Array.isArray(event.tools) && event.tools.length) tools = read_tools(event.tools);
      const output = event.output;
      const m = by_id.get(output?.choices?.[0]?.message?.id);
      if (m) {
        m.usage = read_usage(output.usage);
        m.time = Number.isFinite(output.time) ? output.time : Number.isFinite(event.working_time) ? event.working_time : null;
      }
    } else if (event.event === 'tool') {
      const m = by_id.get(event.message_id);
      if (m && Number.isFinite(event.working_time)) m.time = event.working_time;
    }
  }
  return tools;
}

function read_sample(raw, index) {
  const messages = read_messages(raw.messages);
  const event_tools = read_events(raw.events, messages);
  const input = Array.isArray(raw.input) ? read_messages(raw.input).filter(m => m.role === 'user').map(m => m.text).join('\n') : as_text(raw.input);
  const id = raw.id ?? index + 1;
  const epoch = Number.isFinite(raw.epoch) ? raw.epoch : 1;
  return {
    key: `${id}#${epoch}`,
    id, epoch, input,
    target: Array.isArray(raw.target) ? raw.target.join(', ') : as_text(raw.target),
    messages,
    tools: event_tools.length ? event_tools : read_tools(raw.tools),
    scores: read_scores(raw.scores),
    usage: sum_usage(raw.model_usage) ?? read_usage(raw.usage),
    time: Number.isFinite(raw.total_time) ? raw.total_time : Number.isFinite(raw.time) ? raw.time : null,
    error: tool_error(raw.error),
    metadata: is_object(raw.metadata) ? raw.metadata : {},
  };
}

// A conversation written as {messages} (or Anthropic's request {system, messages}): one sample
function read_conversation(raw, index = 0) {
  const system = raw.system !== undefined && !(Array.isArray(raw.messages) && raw.messages.some(m => m?.role === 'system'))
    ? [{ role: 'system', content: raw.system }] : [];
  return read_sample({ ...raw, messages: [...system, ...raw.messages] }, index);
}

function read_inspect_log(raw) {
  const metrics = [];
  for (const score of raw.results?.scores ?? [])
    for (const [name, metric] of Object.entries(score?.metrics ?? {}))
      if (Number.isFinite(metric?.value)) metrics.push({ scorer: score.name ?? score.scorer, name: metric.name ?? name, value: metric.value });
  return {
    format: 'inspect-log',
    eval: {
      task: raw.eval.task_display_name ?? raw.eval.task ?? null,
      model: raw.eval.model ?? null,
      created: raw.eval.created ?? null,
      status: raw.status ?? null,
      error: tool_error(raw.error),
      metrics,
    },
    samples: (raw.samples ?? []).filter(is_object).map(read_sample),
  };
}

const is_message = x => is_object(x) && (x.role !== undefined || ['function_call', 'function_call_output'].includes(x.type));
const is_conversation = x => is_object(x) && Array.isArray(x.messages);

function read_json(data) {
  if (is_object(data) && is_object(data.eval) && (Array.isArray(data.samples) || data.results || data.status)) {
    const log = read_inspect_log(data);
    if (!log.samples.length) throw new Error("this Inspect log has no samples: it was written with log_samples off, or it is only the header");
    return log;
  }
  if (is_conversation(data))
    return { format: data.epoch !== undefined || data.scores !== undefined ? 'inspect-sample' : 'messages', eval: null, samples: [read_conversation(data)] };
  if (Array.isArray(data) && data.length && data.every(is_conversation))
    return { format: 'messages', eval: null, samples: data.map(read_conversation) };
  if (Array.isArray(data) && data.some(is_message))
    return { format: 'messages', eval: null, samples: [read_sample({ messages: data }, 0)] };
  throw new Error("no messages: expected an Inspect log, {\"messages\": [...]} or a list of messages");
}

export function parseTranscript(text) {
  if (typeof text !== 'string') text = text === undefined || text === null ? '' : JSON.stringify(text);
  const trimmed = text.trim();
  if (!trimmed) throw new Error('the file is empty');
  let data;
  try {
    data = JSON.parse(trimmed);
  } catch (error) {
    // JSON lines: a message or a conversation per line
    const lines = trimmed.split('\n').map(l => l.trim()).filter(Boolean);
    try {
      data = lines.map(l => JSON.parse(l));
    } catch {
      throw new Error(`not JSON (${error.message})`);
    }
  }
  const transcript = read_json(data);
  const has_messages = transcript.samples.some(s => s.messages.length);
  if (!has_messages) throw new Error('no messages with a role we know (system, user, assistant, tool)');
  // the same id and epoch twice: keep them apart
  const seen = new Map();
  for (const sample of transcript.samples) {
    const n = seen.get(sample.key) ?? 0;
    seen.set(sample.key, n + 1);
    if (n) sample.key = `${sample.key}#${n + 1}`;
  }
  return transcript;
}

// What a sample is in numbers, for its summary
export function sampleStats(sample) {
  const calls = sample.messages.flatMap(m => m.tool_calls);
  return {
    messages: sample.messages.length,
    turns: sample.messages.filter(m => m.role === 'assistant').length,
    tool_calls: calls.length,
    tool_errors: sample.messages.filter(m => m.role === 'tool' && m.error).length + calls.filter(c => c.error).length,
    tokens: sample.usage?.total ?? null,
    time: sample.time,
  };
}
