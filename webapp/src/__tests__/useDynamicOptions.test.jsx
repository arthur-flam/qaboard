import { act, renderHook } from '@testing-library/react';

import { useDynamicOptions } from '../useDynamicOptions';
import { mergeCompatibleOptions } from '../utils/dynamicOptions';

const option = values => ({ name: 'frame', values, defaultValue: values[0], views: ['Output'] });
const config = { outputs: { visualizations: [{ name: 'Output', path: ':frame/out.png' }] } };

beforeEach(() => vi.useFakeTimers());
afterEach(() => vi.useRealTimers());


test('merges what output cards register, a few times per second', () => {
  const { result } = renderHook(props => useDynamicOptions(props), { initialProps: { config, reset_key: 'a', files_key: '' } });
  act(() => {
    result.current.register(1, { frame: option(['1', '2']) }, { '1/out.png': {} });
    result.current.register(2, { frame: option(['2', '3']) }, {});
  });
  expect(result.current.dynamic_options).toEqual({});
  act(() => vi.advanceTimersByTime(200));
  expect(result.current.dynamic_options.frame.values).toEqual(['1', '2', '3']);
  expect([...result.current.visualizations_with_files]).toEqual(['Output']);
});

test('registrations that change nothing keep the same objects', () => {
  const { result } = renderHook(props => useDynamicOptions(props), { initialProps: { config, reset_key: 'a', files_key: '' } });
  act(() => result.current.register(1, { frame: option(['1', '2']) }));
  act(() => vi.advanceTimersByTime(200));
  const before = result.current.dynamic_options;
  act(() => result.current.register(2, { frame: option(['1', '2']) }));
  act(() => vi.advanceTimersByTime(200));
  expect(result.current.dynamic_options).toBe(before);
});

test('another commit or batch starts over, a new filter forgets which views have files', () => {
  const { result, rerender } = renderHook(props => useDynamicOptions(props), { initialProps: { config, reset_key: 'a', files_key: '' } });
  act(() => result.current.register(1, { frame: option(['1']) }, { '1/out.png': {} }));
  act(() => vi.advanceTimersByTime(200));
  rerender({ config, reset_key: 'a', files_key: 'scene' });
  expect(result.current.visualizations_with_files.size).toBe(0);
  expect(result.current.dynamic_options.frame).toBeDefined();
  rerender({ config, reset_key: 'b', files_key: 'scene' });
  expect(result.current.dynamic_options).toEqual({});
});

test("mergeCompatibleOptions doesn't take output ids for options", () => {
  const merged = mergeCompatibleOptions([{ output_id: 1, frame: option(['1']) }, { output_id: 2, frame: option(['1', '2']) }]);
  expect(Object.keys(merged)).toEqual(['frame']);
  expect(merged.frame.values).toEqual(['1', '2']);
});
