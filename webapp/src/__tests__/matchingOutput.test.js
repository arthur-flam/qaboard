import { matching_output, reference_candidates } from '../utils';

// like normalize_output's
const run = (id, { configurations = ['base'], extra_parameters = {}, ...extra } = {}) => ({
  id,
  input_path: 'photos/facade.png',
  test_input_path: 'photos/facade.png',
  test_input_metadata: {},
  configurations,
  configurations_str: JSON.stringify(configurations),
  extra_parameters,
  extra_parameters_str: JSON.stringify(extra_parameters),
  platform: 'linux',
  is_pending: false,
  ...extra,
});

const batch = outputs => ({
  outputs: Object.fromEntries(outputs.map(o => [o.id, o])),
  filtered: { outputs: outputs.map(o => o.id) },
});

describe('matching_output', () => {
  it('compares a run with the reference run of the same platform', () => {
    const output = run(1);
    for (const outputs of [[run(2, { platform: 'hw-model' }), run(3)], [run(3), run(2, { platform: 'hw-model' })]]) {
      const { output_ref, mismatch } = matching_output({ output, batch: batch(outputs) });
      expect(output_ref.id).toBe(3);
      expect(mismatch).toBeNull();
    }
  });

  it('falls back to another platform, and says so', () => {
    const { output_ref, mismatch } = matching_output({ output: run(1), batch: batch([run(2, { platform: 'hw-model' })]) });
    expect(output_ref.id).toBe(2);
    expect(mismatch.platform).toBe('hw-model');
  });

  it('prefers the same configuration over the same platform', () => {
    const outputs = [run(2, { configurations: ['low-light'] }), run(3, { platform: 'hw-model' })];
    expect(matching_output({ output: run(1), batch: batch(outputs) }).output_ref.id).toBe(3);
  });

  it('compares with the reference run whose configuration has the fewest different settings', () => {
    const long = 'conf/a/very/long/path/to/the/default/configuration/of/the/camera.yaml';
    const output = run(1, { configurations: [long, { gain: 4, exposure: 10 }] });
    const outputs = [
      // fewer different characters, but 2 different settings
      run(2, { configurations: [long, { gain: 3, exposure: 11 }] }),
      run(3, { configurations: [long.replace('camera', 'phone'), { gain: 4, exposure: 10 }] }),
    ];
    const { output_ref, mismatch } = matching_output({ output, batch: batch(outputs) });
    expect(output_ref.id).toBe(3);
    expect(mismatch.configurations).toEqual(outputs[1].configurations);
  });

  it('prefers the closest tuning parameters', () => {
    const output = run(1, { extra_parameters: { gain: 4, mode: 'fast' } });
    const outputs = [run(2, { extra_parameters: { gain: 3, mode: 'slow' } }), run(3, { extra_parameters: { gain: 4 } })];
    expect(matching_output({ output, batch: batch(outputs) }).output_ref.id).toBe(3);
  });

  it('breaks ties with the most similar values, and says other reference runs were as close', () => {
    const output = run(1, { configurations: ['base-v1'] });
    const outputs = [run(2, { configurations: ['base-blabla'] }), run(3, { configurations: ['base-v2'] }), run(4, { configurations: ['base-v2'] })];
    const { output_ref, mismatch } = matching_output({ output, batch: batch(outputs) });
    expect(output_ref.id).toBe(3);
    // run 4 has the same configuration as run 3
    expect(mismatch.alternatives).toBe(1);
  });

  it('counts reference runs on other inputs that are as close: two shots with the same metadata.id', () => {
    const output = run(1, { input_path: 'sensor-a/scene.png', test_input_path: 'sensor-a/scene.png', test_input_metadata: { id: 'scene' } });
    const shot = (id, path) => run(id, { input_path: path, test_input_path: path, test_input_metadata: { id: 'scene' } });
    const { output_ref, mismatch, match } = matching_output({ output, batch: batch([shot(2, 'sensor-b/scene-1.png'), shot(3, 'sensor-b/scene-2.png')]) });
    expect(output_ref.id).toBe(2);
    expect(mismatch.alternatives).toBe(1);
    expect(match).toEqual({ by: 'id', others: 1, tied: 1, preferred: false });
  });

  it('says how it matched', () => {
    expect(matching_output({ output: run(1), batch: batch([run(2)]) }).match).toMatchObject({ by: 'input', exact: true });
    expect(matching_output({ output: run(1), batch: batch([]) }).match).toBeNull();
  });

  const named = (id, path, extra) => run(id, { input_path: `/db/${path}`, test_input_path: path, ...extra });

  it('pairs runs by name, before the same input', () => {
    const output = named(1, 'street_reg05.png');
    const outputs = [named(2, 'street_reg05.png'), named(3, 'street_reg03.png')];
    expect(matching_output({ output, batch: batch(outputs) }).output_ref.id).toBe(2);
    const rules = { pairs: [{ from: 'reg05', to: 'reg03' }], prefer: [], pins: {} };
    const { output_ref, match } = matching_output({ output, batch: batch(outputs), rules });
    expect(output_ref.id).toBe(3);
    expect(match.by).toBe('pair');
  });

  it('prefers reference runs with the facts users picked', () => {
    const outputs = [run(2), run(3, { platform: 'arm' })];
    expect(matching_output({ output: run(1), batch: batch(outputs) }).output_ref.id).toBe(2);
    const rules = { pairs: [], prefer: [{ key: 'platform', value: 'arm' }], pins: {} };
    const { output_ref, match } = matching_output({ output: run(1), batch: batch(outputs), rules });
    expect(output_ref.id).toBe(3);
    expect(match.preferred).toBe(true);
  });

  it('compares with the reference run users picked, if it is still there', () => {
    const outputs = [run(2), run(3, { configurations: ['other'] })];
    expect(matching_output({ output: run(1), batch: batch(outputs), rules: { pairs: [], prefer: [], pins: { 1: '3' } } }))
      .toMatchObject({ output_ref: { id: 3 }, match: { by: 'pin' } });
    expect(matching_output({ output: run(1), batch: batch(outputs), rules: { pairs: [], prefer: [], pins: { 1: '99' } } }).output_ref.id).toBe(2);
  });

  it('lists the reference runs users can pick, similar names last', () => {
    const output = named(1, 'street_reg05.png');
    const outputs = [named(2, 'street_reg03.png'), named(3, 'street_reg05.png', { configurations: ['other'] }), named(4, 'office_reg09.png')];
    expect(reference_candidates({ output, batch: batch(outputs) }).map(c => [c.o.id, c.kind])).toEqual([[3, 'input'], [2, 'similar']]);
  });

  it("doesn't count as tied the reference runs the platform tells apart", () => {
    const output = run(1, { input_path: 'a/scene.png', test_input_path: 'a/scene.png', test_input_metadata: { id: 'scene' } });
    const shot = (id, path, platform = 'linux') => run(id, { input_path: path, test_input_path: path, test_input_metadata: { id: 'scene' }, platform });
    const { output_ref, match } = matching_output({ output, batch: batch([shot(2, 'b/scene-1.png', 'arm'), shot(3, 'b/scene-2.png')]) });
    expect(output_ref.id).toBe(3);
    expect(match.tied).toBe(0);
  });

  it('says a preference picked the reference run only when it changed the pick', () => {
    const rules = { pairs: [], prefer: [{ key: 'platform', value: 'linux' }], pins: {} };
    expect(matching_output({ output: run(1), batch: batch([run(2), run(3, { platform: 'arm' })]), rules }).match.preferred).toBe(false);
  });
});
