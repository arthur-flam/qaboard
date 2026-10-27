/**
 * The sensor of the project's raw images, saved for everyone by its developers and owners: how QA-Board's image
 * viewer gets it (projectSensor.js), and what it passes to the viewer (QaboardImageViewer.jsx).
 * Run with: cd webapp && npm test -- projectSensor
 */
import { describe, it, expect, vi } from 'vitest';
import { act, renderHook, waitFor } from '@testing-library/react';
import { QueryClientProvider } from '@tanstack/react-query';
import { unstable_HistoryRouter as HistoryRouter } from 'react-router';

import { fromServer, savedNote, toServer, useProjectSensor } from '../projectSensor';
import { projectQuery } from '../../../api/queries';
import { history, RouteMatch, matchRoutes } from '../../../router';
import { routes } from '../../../routes';
import { makeQueryClient, renderWithProviders } from '../../../test-utils';
import { usePrefsStore } from '../../../stores/prefs';

// the viewer, to see what QA-Board gives it
const viewers = [];
vi.mock('../../../image-viewer', () => ({ ImageViewer: props => { viewers.push(props); return null; } }));
const { default: ImageViewerForRuns } = await import('../QaboardImageViewer');

const json = (data, status = 200) => Promise.resolve(new Response(JSON.stringify(data), { status, headers: { 'Content-Type': 'application/json' } }));
const mockApi = routes => vi.spyOn(globalThis, 'fetch').mockImplementation((url, init = {}) => {
  const { pathname } = new URL(url, 'http://localhost');
  const answer = routes[`${init.method ?? 'GET'} ${pathname}`];
  if (!answer) return json({});
  const [data, status] = typeof answer === 'function' ? answer(url, init) : [answer, 200];
  return json(data, status);
});
const calls = (fetch, method, path) => fetch.mock.calls.filter(([url, init]) => (init?.method ?? 'GET') === method && url.startsWith(path));

const project = 'group/repo';
const server_sensor = {
  kind: 'mosaic', pattern: 'GRBG', block: 2, offset: { x: 1, y: 0 }, black_level: 64,
  updated_by: { user_name: 'bob', full_name: 'Bob B' }, updated_at: '2026-10-27T10:00:00Z',
};
const access = (role, can) => ({ project, my_role: role, via: 'grant', can_manage: role === 'owner', can_save_viewer_settings: can, visibility: {}, owners: [] });

afterEach(() => {
  vi.restoreAllMocks();
  viewers.length = 0;
});


describe("the server's sensor", () => {
  it("is the viewer's SensorSetting", () => {
    expect(fromServer(server_sensor)).toEqual({ kind: 'mosaic', pattern: 'GRBG', block: 2, offset: { x: 1, y: 0 }, blackLevel: 64 });
    expect(fromServer({ black_level: 0 })).toEqual({ blackLevel: 0 });
    expect(fromServer(null)).toBe(null);
    // only who saved it: nothing
    expect(fromServer({ updated_by: { user_name: 'bob' } })).toBe(null);
  });

  it('is saved without what is a way to look', () => {
    expect(toServer({ kind: 'auto', pattern: 'GRBG', block: 2, offset: null, blackLevel: 64, grouping: 'sub' })).toEqual({ pattern: 'GRBG', block: 2, black_level: 64 });
    expect(toServer({ kind: 'mono', blackLevel: 0 })).toEqual({ kind: 'mono', black_level: 0 });
    expect(toServer(null)).toBe(null);
  });

  it('says who saved it, and when', () => {
    expect(savedNote(server_sensor)).toBe('by Bob B, Oct 27, 2026');
    expect(savedNote({ updated_by: { user_name: 'bob' } })).toBe('by bob');
    expect(savedNote(null)).toBe(undefined);
  });
});


const renderProjectSensor = ({ signed_in = true, queryClient = makeQueryClient() } = {}) => {
  const wrapper = ({ children }) => <QueryClientProvider client={queryClient}>{children}</QueryClientProvider>;
  return { queryClient, ...renderHook(() => useProjectSensor(project, signed_in), { wrapper }) };
};

describe("the project's sensor, for the viewer", () => {
  it('comes with the project, and developers save it', async () => {
    const fetch = mockApi({
      'GET /api/v1/project': { qatools_config: {}, settings: { sensor: server_sensor } },
      'GET /api/v1/project/access': access('developer', true),
      'POST /api/v1/project/settings': { settings: { sensor: { ...server_sensor, black_level: 60, updated_at: '2026-10-28T10:00:00Z' } } },
    });
    const { result } = renderProjectSensor();
    await waitFor(() => expect(result.current.onSaveSensor).toBeDefined());
    expect(result.current).toMatchObject({
      savedSensor: { kind: 'mosaic', pattern: 'GRBG', block: 2, offset: { x: 1, y: 0 }, blackLevel: 64 },
      savedSensorFor: 'project',
      savedSensorNote: 'by Bob B, Oct 27, 2026',
      saveSensorUnavailable: undefined,
    });
    await act(() => result.current.onSaveSensor({ pattern: 'GRBG', block: 2, offset: { x: 1, y: 0 }, blackLevel: 60, kind: 'mosaic' }));
    const [[, init]] = calls(fetch, 'POST', '/api/v1/project/settings');
    expect(JSON.parse(init.body)).toEqual({ project, sensor: { kind: 'mosaic', pattern: 'GRBG', block: 2, offset: { x: 1, y: 0 }, black_level: 60 } });
    // everyone's viewers of the project get it, without asking the server again
    await waitFor(() => expect(result.current.savedSensor.blackLevel).toBe(60));
    expect(result.current.savedSensorNote).toBe('by Bob B, Oct 28, 2026');
    expect(calls(fetch, 'GET', '/api/v1/project?')).toHaveLength(1);
  });

  it('is forgotten with null', async () => {
    const fetch = mockApi({
      'GET /api/v1/project': { settings: { sensor: server_sensor } },
      'GET /api/v1/project/access': access('owner', true),
      'POST /api/v1/project/settings': { settings: {} },
    });
    const { result } = renderProjectSensor();
    await waitFor(() => expect(result.current.onSaveSensor).toBeDefined());
    await act(() => result.current.onSaveSensor(null));
    expect(JSON.parse(calls(fetch, 'POST', '/api/v1/project/settings')[0][1].body)).toEqual({ project, sensor: null });
    await waitFor(() => expect(result.current.savedSensor).toBe(null));
  });

  it('is read-only for viewers', async () => {
    mockApi({
      'GET /api/v1/project': { settings: { sensor: server_sensor } },
      'GET /api/v1/project/access': access('viewer', false),
    });
    const { result } = renderProjectSensor();
    await waitFor(() => expect(result.current.saveSensorUnavailable).toBeDefined());
    expect(result.current.onSaveSensor).toBeUndefined();
    expect(result.current.saveSensorUnavailable).toBe("The project's developers and owners can save it for everyone who opens the project");
    expect(result.current.savedSensor.blackLevel).toBe(64);
  });

  it('is read-only when signed out, without asking for the role', async () => {
    const fetch = mockApi({ 'GET /api/v1/project': { settings: { sensor: server_sensor } } });
    const { result } = renderProjectSensor({ signed_in: false });
    await waitFor(() => expect(result.current.savedSensor).not.toBe(null));
    expect(result.current.onSaveSensor).toBeUndefined();
    expect(result.current.saveSensorUnavailable).toMatch(/^Sign in/);
    expect(calls(fetch, 'GET', '/api/v1/project/access')).toHaveLength(0);
  });

  it("says why the server refused, and doesn't change what's saved", async () => {
    mockApi({
      'GET /api/v1/project': { settings: { sensor: server_sensor } },
      'GET /api/v1/project/access': access('developer', true),
      'POST /api/v1/project/settings': () => [{ error: "You need the developer role to save the viewers' settings of group/repo: you're a viewer.", reason: 'missing_role' }, 403],
    });
    const { result, queryClient } = renderProjectSensor();
    await waitFor(() => expect(result.current.onSaveSensor).toBeDefined());
    await expect(result.current.onSaveSensor({ blackLevel: 1 })).rejects.toThrow(/You need the developer role/);
    expect(queryClient.getQueryData(projectQuery(project).queryKey).data.settings.sensor.black_level).toBe(64);
  });
});


describe("QA-Board's image viewer", () => {
  const output = { id: 1, output_dir_url: '/s/runs/1', test_input_path: 'a.raw', deleted: false };
  const renderViewer = user => {
    act(() => history.replace(`/${project}/commit/abc`));
    const { match } = matchRoutes(routes, `/${project}/commit/abc`);
    return renderWithProviders(<HistoryRouter history={history}><RouteMatch match={match}>
      <ImageViewerForRuns output_new={output} output_ref={output} path="a.raw" manifests={{ new: { 'a.raw': {} }, reference: { 'a.raw': {} } }} />
    </RouteMatch></HistoryRouter>, { user, siteConfig: { image_servers: {} } });
  };

  it("passes the project's sensor, and saving it to developers", async () => {
    mockApi({
      'GET /api/v1/project': { settings: { sensor: server_sensor } },
      'GET /api/v1/project/access': access('developer', true),
      'POST /api/v1/project/settings': { settings: {} },
    });
    usePrefsStore.getState().setImageSensor(project, { blackLevel: 70, grouping: 'sub' });
    renderViewer({ is_logged: true, user_name: 'bob' });
    await waitFor(() => expect(viewers.at(-1).onSaveSensor).toBeDefined());
    const props = viewers.at(-1);
    expect(props.sensor).toEqual({ blackLevel: 70, grouping: 'sub' });
    expect(props.sensorRemembered).toBe('in this browser, for the project');
    expect(props.savedSensor).toMatchObject({ pattern: 'GRBG', blackLevel: 64 });
    expect(props.savedSensorFor).toBe('project');
    // forgetting theirs once saved: the viewer does it, with onSensorChange
    act(() => props.onSensorChange({ grouping: 'sub' }));
    expect(usePrefsStore.getState().imageSensor[project]).toEqual({ grouping: 'sub' });
  });

  it('is read-only for others', async () => {
    mockApi({ 'GET /api/v1/project': { settings: { sensor: server_sensor } }, 'GET /api/v1/project/access': access('viewer', false) });
    renderViewer({ is_logged: true, user_name: 'carol' });
    await waitFor(() => expect(viewers.at(-1).saveSensorUnavailable).toBeDefined());
    expect(viewers.at(-1).onSaveSensor).toBeUndefined();
    expect(viewers.at(-1).savedSensor).toMatchObject({ pattern: 'GRBG' });
  });
});
