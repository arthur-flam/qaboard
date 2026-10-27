/**
 * What's saved of the sensor for everyone (the `savedSensor` props, e.g. the project's): where it comes in the
 * analysis, and the Sensor dialog's states: read-only, saving, forgetting, failing.
 * Run with: cd webapp && npm test -- savedSensor
 */
import { describe, it, expect, vi } from 'vitest';
import { act, fireEvent, render, screen, within } from '@testing-library/react';

import { normalizeCfa } from '../cfa';
import { analyzeImage, normalizeSensorSetting, setsSensor, withOwnSetting } from '../imageAnalysis';
import { SensorCard, describeSetting } from '../sensorPanel';

// A flat 64×64 16-bit image: no mosaic to guess from its pixels
const flatImage = meta => {
  const image = { url: `/flat-${Math.random()}.raw` };
  const getPixels = async (_, { w, h }) => ({ data: new Uint16Array(w * h).fill(200), width: w, height: h, channels: 1, mode: 'I;16', sensor: meta });
  return { image, getPixels };
};
const region = { x: 0, y: 0, w: 64, h: 64 };
const size = { width: 64, height: 64 };
const analyze = async ({ meta, setting, saved }) => {
  const { image, getPixels } = flatImage(meta);
  return (await analyzeImage(getPixels, image, region, size, normalizeSensorSetting(setting), { saved })).sensor;
};
const project = { pattern: 'GBRG', block: 2, blackLevel: 64 };

describe("the analysis, with what's saved for everyone", () => {
  it('uses it for images without metadata', async () => {
    const sensor = await analyze({ saved: project });
    expect([sensor.kind, sensor.cfa.pattern, sensor.cfa.block, sensor.black]).toEqual(['mosaic', 'GBRG', 2, 64]);
    expect(sensor.sources).toMatchObject({ kind: 'saved', cfa: 'saved', black: 'saved' });
  });

  it("comes after the image's metadata", async () => {
    const sensor = await analyze({ saved: project, meta: { cfa: { pattern: 'RGGB', block: 1 }, blackLevel: 32 } });
    expect([sensor.cfa.pattern, sensor.cfa.block, sensor.black]).toEqual(['RGGB', 1, 32]);
    expect(sensor.sources).toMatchObject({ cfa: 'metadata', black: 'metadata' });
    // what the metadata doesn't say
    const without_black = await analyze({ saved: project, meta: { cfa: { pattern: 'RGGB', block: 1 } } });
    expect([without_black.sources.cfa, without_black.black, without_black.sources.black]).toEqual(['metadata', 64, 'saved']);
    // monochrome sensors
    expect((await analyze({ saved: project, meta: { mono: true } })).kind).toBe('mono');
  });

  it('loses to what users set', async () => {
    const sensor = await analyze({ saved: project, setting: { blackLevel: 16, pattern: 'BGGR' } });
    // the cell size they didn't set: the saved one
    expect([sensor.cfa.pattern, sensor.cfa.block, sensor.black]).toEqual(['BGGR', 2, 16]);
    expect(sensor.sources).toMatchObject({ cfa: 'user', black: 'user' });
    expect((await analyze({ saved: { kind: 'mono' }, setting: { kind: 'mosaic' } })).sources.kind).toBe('user');
  });

  it('is only the sensor: without how users group the channels', () => {
    expect(setsSensor({ grouping: 'sub' })).toBe(false);
    expect(setsSensor({ blackLevel: 0 })).toBe(true);
    expect(withOwnSetting(project, { blackLevel: 60, grouping: 'sub', kind: 'auto' })).toEqual({ pattern: 'GBRG', block: 2, blackLevel: 60 });
    expect(describeSetting(project)).toBe('GBRG · Tetra 2×2, black 64');
    expect(describeSetting({ kind: 'mono', blackLevel: 0 })).toBe('one channel, black 0');
    expect(describeSetting({ block: 3, offset: { x: 1, y: 0 } })).toBe('Nona 3×3, from (1, 0)');
  });
});


// The card of an image whose CFA and black level are the project's
const savedSensor = {
  kind: 'mosaic', cfa: normalizeCfa({ pattern: 'GBRG', block: 2 }), bits: 10, white: 1023, black: 64, mode: 'I;16', max: 1000,
  sources: { kind: 'saved', cfa: 'saved', bits: 'pixels', white: 'pixels', black: 'saved' },
  guess: null, meta: null, check: null, levels: null, patch: { x: 0, y: 0, w: 64, h: 64 },
};
const card = ({ own = {}, storage = {}, sensor = savedSensor } = {}) => {
  const onSettingChange = vi.fn();
  const state = { status: 'ready', results: { a: { sensor }, b: null }, guessAgain: vi.fn() };
  render(<SensorCard state={state} setting={normalizeSensorSetting(own)} onSettingChange={onSettingChange} names={{ a: 'new', b: 'reference' }} comparing={false} storage={storage} />);
  fireEvent.click(screen.getByRole('button', { name: 'Sensor' }));
  return { onSettingChange, dialog: within(screen.getByRole('dialog')) };
};
const project_storage = { remembered: 'in this browser, for the project', saved: project, savedFor: 'project', savedNote: 'by Bob B, Oct 27, 2026' };

describe('the Sensor dialog', () => {
  it('says where the values come from', () => {
    card({ storage: project_storage });
    const tags = screen.getAllByTitle('Saved for the project by Bob B, Oct 27, 2026');
    // the pattern and the black level
    expect(tags.map(tag => tag.textContent)).toEqual(['project', 'project']);
    expect(screen.getByTitle(/Guessed from the pixels/).textContent).toBe('guessed');
  });

  it('without anything saved nor a way to save, is as before', () => {
    const { dialog } = card({ storage: { remembered: 'for the project, in this browser' } });
    expect(dialog.getByText('It applies to both images, and what you set is remembered for the project, in this browser.')).toBeInTheDocument();
    expect(dialog.queryByText(/For the project|For everyone/)).not.toBeInTheDocument();
    expect(dialog.queryByRole('button', { name: /Save for/ })).not.toBeInTheDocument();
  });

  it("shows what's saved, read-only for users who can't save", () => {
    const { dialog } = card({ own: { blackLevel: 70 }, storage: { ...project_storage, saveUnavailable: "The project's developers and owners can save it" } });
    expect(dialog.getByText('For the project')).toBeInTheDocument();
    expect(dialog.getByText('GBRG · Tetra 2×2, black 64')).toBeInTheDocument();
    expect(dialog.getByText('by Bob B, Oct 27, 2026')).toBeInTheDocument();
    expect(dialog.getByText("The project's developers and owners can save it")).toBeInTheDocument();
    expect(dialog.queryByRole('button', { name: 'Save for the project' })).not.toBeInTheDocument();
    expect(dialog.queryByRole('button', { name: "Forget the project's" })).not.toBeInTheDocument();
    // the black level they set, over the project's
    expect(dialog.getByRole('spinbutton', { name: 'Black level' })).toHaveValue(70);
    expect(dialog.getByText('yours')).toBeInTheDocument();
  });

  it("falls back to the project's black level", () => {
    const { dialog } = card({ storage: project_storage });
    expect(dialog.getByRole('spinbutton', { name: 'Black level' })).toHaveAttribute('placeholder', '64');
    expect(dialog.getByText("the project's")).toBeInTheDocument();
  });

  it('saves what users set over what is saved, then forgets theirs', async () => {
    const onSave = vi.fn(() => Promise.resolve());
    const { dialog, onSettingChange } = card({ own: { blackLevel: 60, grouping: 'sub' }, storage: { ...project_storage, onSave } });
    const save = dialog.getByRole('button', { name: 'Save for the project' });
    expect(save).toHaveAttribute('title', "Save GBRG · Tetra 2×2, black 60 for everyone who opens the project's images");
    await act(async () => fireEvent.click(save));
    expect(onSave).toHaveBeenCalledWith({ pattern: 'GBRG', block: 2, blackLevel: 60 });
    // theirs is the project's now: only how they group the channels stays
    expect(onSettingChange).toHaveBeenCalledWith({ grouping: 'sub' });
    expect(dialog.getByText('Saved for the project.')).toBeInTheDocument();
  });

  it('offers to save only what users set', () => {
    const { dialog } = card({ storage: { ...project_storage, onSave: vi.fn() } });
    expect(dialog.queryByRole('button', { name: 'Save for the project' })).not.toBeInTheDocument();
    expect(dialog.getByRole('button', { name: "Forget the project's" })).toBeInTheDocument();
  });

  it("forgets the project's, and keeps what users set", async () => {
    const onSave = vi.fn(() => Promise.resolve());
    const { dialog, onSettingChange } = card({ own: { blackLevel: 60 }, storage: { ...project_storage, onSave } });
    await act(async () => fireEvent.click(dialog.getByRole('button', { name: "Forget the project's" })));
    expect(onSave).toHaveBeenCalledWith(null);
    expect(onSettingChange).not.toHaveBeenCalled();
    expect(dialog.getByText('Forgotten for the project.')).toBeInTheDocument();
  });

  it("says why it couldn't save, and keeps what users set", async () => {
    const onSave = vi.fn(() => Promise.reject(new Error("You need the developer role to save the viewers' settings of group/repo: you're a viewer.")));
    const { dialog, onSettingChange } = card({ own: { pattern: 'RGGB' }, storage: { saved: null, savedFor: 'project', onSave } });
    expect(dialog.getByText('nothing saved')).toBeInTheDocument();
    await act(async () => fireEvent.click(dialog.getByRole('button', { name: 'Save for the project' })));
    expect(onSave).toHaveBeenCalledWith({ pattern: 'RGGB' });
    expect(dialog.getByRole('alert')).toHaveTextContent("You need the developer role to save the viewers' settings of group/repo: you're a viewer.");
    expect(onSettingChange).not.toHaveBeenCalled();
  });

  it('works without saying who it is saved for', async () => {
    const onSave = vi.fn(() => Promise.resolve());
    const { dialog } = card({ own: { blackLevel: 1 }, storage: { saved: null, onSave } });
    expect(dialog.getByText('For everyone')).toBeInTheDocument();
    await act(async () => fireEvent.click(dialog.getByRole('button', { name: 'Save for everyone' })));
    expect(onSave).toHaveBeenCalledWith({ blackLevel: 1 });
  });
});
