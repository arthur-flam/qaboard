/**
 * Links that open Run Tests / Tuning filled in: what they can fill, and what they can't.
 * Run with: cd webapp && npm test -- prefill_link
 */
import { parse_prefill_link, form_from_link, describe_link, prefill_link, limits } from '../prefill_link';
import { parse_query } from '../../../selection';

const parse = data => parse_prefill_link(JSON.stringify(data));

describe('parse_prefill_link', () => {
  it('keeps the fields it knows, and ignores the others', () => {
    expect(parse({
      label: ' sweep ', batches: ['ci', ' night-* '], parameter_search: { a: [1, 2] }, search_type: 'sampler', n_iter: 5,
      platform: 'windows', overwrite: false, parameter_search_auto: 'evaluations: 3',
    })).toEqual({
      prefill: { label: 'sweep', batches: ['ci', 'night-*'], parameter_search: { a: [1, 2] }, search_type: 'sampler', n_iter: 5 },
      warnings: [],
    });
    // values that go together, matrices, and null as if not given
    expect(parse({ parameter_search: [{ a: [1] }, { a: [2], m: [[[0, 1], [1, 0]]] }], label: null })).toEqual({
      prefill: { parameter_search: [{ a: [1] }, { a: [2], m: [[[0, 1], [1, 0]]] }] }, warnings: [],
    });
  });

  it('leaves out the fields of the wrong type, and says why', () => {
    const { prefill, warnings } = parse({
      label: 3, batches: 'ci', parameter_search: { a: [1] }, search_type: 'optimize', n_iter: 1.5,
    });
    expect(prefill).toEqual({ parameter_search: { a: [1] } });
    expect(warnings).toEqual([
      'Batch label: not filled in, it is not text.',
      'Tests: not filled in, it is not a list of names.',
      'Runs: not filled in, it is neither "grid" nor "sampler".',
      `Sample size: not filled in, it is not a whole number from 1 to ${limits.n_iter}.`,
    ]);
    expect(parse({ batches: ['ci', 2] }).prefill).toEqual({});
    expect(parse({ batches: ['ci', ''] }).prefill).toEqual({});
    expect(parse({ batches: ['a, b'] }).warnings).toEqual(['Tests: not filled in, a name has a comma.']);
    expect(parse({ parameter_search: 'return {a: [1]}' }).prefill).toEqual({});
    expect(parse({ parameter_search: [{ a: [1] }, 2] }).prefill).toEqual({});
    expect(parse({ n_iter: 0 }).prefill).toEqual({});
  });

  it('caps sizes', () => {
    expect(parse({ label: 'x'.repeat(limits.label + 1) }).prefill).toEqual({});
    expect(parse({ batches: Array.from({ length: limits.batches + 1 }, (_, i) => `b${i}`) }).prefill).toEqual({});
    expect(parse({ parameter_search: Object.fromEntries(Array.from({ length: limits.parameters + 1 }, (_, i) => [`p${i}`, [1]])) }).prefill).toEqual({});
    expect(parse({ parameter_search: { a: Array.from({ length: limits.values + 1 }, (_, i) => i) } }).prefill).toEqual({});
    expect(parse({ parameter_search: Array.from({ length: limits.sets + 1 }, () => ({ a: [1] })) }).prefill).toEqual({});
    let deep = 1;
    for (let i = 0; i < 20; i++) deep = [deep];
    expect(parse({ parameter_search: { a: deep } }).warnings).toEqual(['Values to try: not filled in, its values are nested too deep.']);
    expect(parse_prefill_link(JSON.stringify({ label: 'a', pad: 'x'.repeat(limits.text) })).prefill).toEqual({});
  });

  it('reads nothing from what is not a JSON object', () => {
    for (const raw of ['{label: "a"}', '[1]', '"text"', 'null', undefined, ['{}', '{}'], { label: 'a' }]) {
      const { prefill, warnings } = parse_prefill_link(raw);
      expect(prefill).toEqual({});
      expect(warnings).toHaveLength(1);
      expect(warnings[0]).toMatch(/^The link's values could not be read: .*The form is as you left it\.$/);
    }
  });
});

describe('form_from_link', () => {
  it('fills the form like typing in it would', () => {
    expect(form_from_link({ label: 'my sweep!', batches: ['ci', 'night-*'], parameter_search: { a: [1, 2] }, search_type: 'sampler', n_iter: 3 })).toEqual({
      experiment_name: 'my-sweep-',
      selected_group: 'ci, night-*',
      search_type: 'sampler',
      parameter_search: '{\n  "a": [1, 2]\n}',
      values_mode: 'table',
      search_options: { n_iter: 3 },
    });
    // no tuning
    expect(form_from_link({ parameter_search: {} })).toMatchObject({ parameter_search: '', search_type: 'grid' });
    // only what is given changes
    expect(form_from_link({})).toEqual({});
    expect(form_from_link({ search_type: 'sampler', n_iter: 7 })).toEqual({ search_type: 'sampler', search_options: { n_iter: 7 } });
  });

  it('only fills JSON: the form would run other text as a function', () => {
    const { parameter_search } = form_from_link(parse({ parameter_search: { 'a"}; alert(1); //': ['"]; alert(1)'] } }).prefill);
    expect(JSON.parse(parameter_search)).toEqual({ 'a"}; alert(1); //': ['"]; alert(1)'] });
  });
});

describe('describe_link', () => {
  it('says what the link filled', () => {
    expect(describe_link({ label: 'sweep', batches: ['a', 'b', 'c', 'd', 'e', 'f', 'g'], parameter_search: { a: [1, 2], b: [1, 2, 3] }, search_type: 'sampler', n_iter: 4 })).toEqual([
      ['Batch label', 'sweep'],
      ['Tests', 'a, b, c, d, e and 2 more'],
      ['Values to try', 'a, b: 4 combinations of 6'],
      ['Runs', 'a random sample of 4'],
    ]);
    expect(describe_link({ parameter_search: {} })).toEqual([['Values to try', 'No tuning']]);
    expect(describe_link({})).toEqual([]);
  });
});

describe('prefill_link', () => {
  it('builds links the commit page reads back', () => {
    const prefill = { label: 'a&b=c', batches: ['ci'], parameter_search: { 'x+y': ['1 2', 1e-5] } };
    const link = prefill_link('isp/denoise', 'c0ffee', prefill);
    expect(link.startsWith('/isp/denoise/commit/c0ffee?selected_views=tuning&prefill=')).toBe(true);
    const query = parse_query(new URL(link, 'http://localhost').search);
    expect(query.selected_views).toBe('tuning');
    expect(parse_prefill_link(query.prefill)).toEqual({ prefill, warnings: [] });
  });
});
