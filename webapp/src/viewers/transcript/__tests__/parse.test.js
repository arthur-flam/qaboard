// The conversations the viewer reads: Inspect's logs (real ones, tests/fixtures/transcripts/, written by
// make_inspect.py), and messages in OpenAI's and Anthropic's shapes
import fs from 'fs';
import path from 'path';

import { parseTranscript, sampleStats } from '../parse';

const fixture = name => fs.readFileSync(path.join(import.meta.dirname, '../../../../../tests/fixtures/transcripts', name), 'utf8');

describe('Inspect logs', () => {
  const log = parseTranscript(fixture('inspect-new.json'));

  it('reads the eval, its samples and epochs', () => {
    expect(log.format).toBe('inspect-log');
    expect(log.eval).toMatchObject({ task: 'fleet', model: 'mockllm/model', status: 'success' });
    expect(log.eval.metrics).toContainEqual({ scorer: 'includes', name: 'accuracy', value: 0.5 });
    expect(log.samples.map(s => s.key)).toEqual(['battery-r2#1', 'battery-r9#1', 'battery-r2#2', 'battery-r9#2']);
  });

  it('reads messages, tool calls, their results and errors', () => {
    const sample = log.samples[1];
    expect(sample.messages.map(m => m.role)).toEqual(['system', 'user', 'assistant', 'tool', 'assistant']);
    expect(sample.messages[2].tool_calls).toEqual([{ id: 'call_r9_1', name: 'battery', arguments: { robot: 'r9' } }]);
    expect(sample.messages[3]).toMatchObject({ role: 'tool', name: 'battery', tool_call_id: 'call_r9_1', error: 'unknown: unknown robot r9' });
    expect(sample.input).toBe("What is r9's battery?");
    expect(sample.target).toBe('unknown');
  });

  it('reads scores, usage, and what each model call cost from the events', () => {
    const sample = log.samples[1];
    expect(sample.scores).toEqual([{ name: 'includes', value: 'I', passed: false, answer: 'r9 is at 42%.', explanation: 'r9 is at 42%.' }]);
    expect(sample.usage.total).toBeGreaterThan(0);
    expect(sample.messages[4].usage.total).toBeGreaterThan(0);
    expect(sample.messages[4].time).toBeGreaterThan(0);
    expect(sample.tools).toEqual([expect.objectContaining({ name: 'battery', description: "Read a robot's battery level." })]);
    expect(sampleStats(sample)).toMatchObject({ messages: 5, turns: 2, tool_calls: 1, tool_errors: 1 });
  });

  it('says why a log without samples shows nothing', () => {
    expect(() => parseTranscript(JSON.stringify({ version: 2, status: 'success', eval: { task: 't' }, results: {} }))).toThrow(/no samples/);
  });
});

describe('chat messages', () => {
  it("reads OpenAI's chat completions: arguments as JSON text, developer messages", () => {
    const { samples: [sample], format } = parseTranscript(JSON.stringify({ messages: [
      { role: 'developer', content: 'Be brief.' },
      { role: 'user', content: [{ type: 'text', text: 'Battery of r2?' }, { type: 'image_url', image_url: { url: 'data:...' } }] },
      { role: 'assistant', content: null, tool_calls: [{ id: 'c1', type: 'function', function: { name: 'battery', arguments: '{"robot": "r2"}' } }] },
      { role: 'tool', tool_call_id: 'c1', content: '{"battery": 0.42}' },
      { role: 'assistant', content: '42%', reasoning_content: 'The tool says 0.42.' },
    ] }));
    expect(format).toBe('messages');
    expect(sample.messages.map(m => m.role)).toEqual(['system', 'user', 'assistant', 'tool', 'assistant']);
    expect(sample.messages[1]).toMatchObject({ text: 'Battery of r2?', attachments: ['image_url'] });
    expect(sample.messages[2].tool_calls).toEqual([{ id: 'c1', name: 'battery', arguments: { robot: 'r2' } }]);
    expect(sample.messages[3].name).toBe('battery');
    expect(sample.messages[4]).toMatchObject({ text: '42%', reasoning: 'The tool says 0.42.' });
  });

  it("reads Anthropic's messages: tool use and results in the content, the system prompt apart", () => {
    const { samples: [sample] } = parseTranscript(JSON.stringify({
      system: 'Be brief.',
      tools: [{ name: 'battery', description: 'Read the battery.', input_schema: { type: 'object' } }],
      messages: [
        { role: 'user', content: 'Battery of r2?' },
        { role: 'assistant', content: [{ type: 'thinking', thinking: 'I need the tool.' }, { type: 'text', text: 'Let me check.' }, { type: 'tool_use', id: 't1', name: 'battery', input: { robot: 'r2' } }] },
        { role: 'user', content: [{ type: 'tool_result', tool_use_id: 't1', content: [{ type: 'text', text: 'unknown robot' }], is_error: true }] },
        { role: 'assistant', content: [{ type: 'text', text: "I can't find it." }] },
      ],
    }));
    expect(sample.messages.map(m => m.role)).toEqual(['system', 'user', 'assistant', 'tool', 'assistant']);
    expect(sample.messages[2]).toMatchObject({ text: 'Let me check.', reasoning: 'I need the tool.', tool_calls: [{ id: 't1', name: 'battery', arguments: { robot: 'r2' } }] });
    expect(sample.messages[3]).toMatchObject({ role: 'tool', name: 'battery', text: 'unknown robot', error: 'error' });
    expect(sample.tools).toEqual([{ name: 'battery', description: 'Read the battery.', parameters: { type: 'object' } }]);
  });

  it("reads OpenAI's responses items, and a list of messages as JSON lines", () => {
    const { samples: [sample] } = parseTranscript([
      { role: 'user', content: 'Battery of r2?' },
      { type: 'function_call', call_id: 'c1', name: 'battery', arguments: '{"robot":"r2"}' },
      { type: 'function_call_output', call_id: 'c1', output: '0.42' },
      { type: 'message', role: 'assistant', content: [{ type: 'output_text', text: '42%' }] },
    ].map(m => JSON.stringify(m)).join('\n'));
    expect(sample.messages.map(m => [m.role, m.text])).toEqual([['user', 'Battery of r2?'], ['assistant', ''], ['tool', '0.42'], ['assistant', '42%']]);
    expect(sample.messages[2].name).toBe('battery');
  });

  it('reads several conversations, and keeps those with the same id apart', () => {
    const { samples } = parseTranscript(JSON.stringify([
      { id: 'a', messages: [{ role: 'user', content: 'x' }] },
      { id: 'a', messages: [{ role: 'user', content: 'y' }] },
    ]));
    expect(samples.map(s => s.key)).toEqual(['a#1', 'a#1#2']);
  });

  it.each([
    ['', /empty/],
    ['not json', /not JSON/],
    ['{"benchmarks": []}', /no messages/],
    ['[{"speaker": "a"}]', /no messages/],
  ])('says why %j is not a conversation', (text, why) => expect(() => parseTranscript(text)).toThrow(why));
});
