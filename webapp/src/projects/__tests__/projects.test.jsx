/**
 * New project (the dialog says, as people type, whether they may create it, else what to do), and the Access page's
 * Project section (who created it, archive). Run with: cd webapp && npm test -- projects
 */
import { screen, fireEvent, waitFor } from '@testing-library/react';

import { NewProjectDialog } from '../NewProjectDialog';
import { ProjectLifecycle } from '../Lifecycle';
import { projectIdProblem } from '../api';
import { renderWithProviders } from '../../test-utils';
import { history } from '../../router';

const json = (data, status = 200) => Promise.resolve(new Response(JSON.stringify(data), { status, headers: { 'Content-Type': 'application/json' } }));
const mockApi = routes => vi.spyOn(globalThis, 'fetch').mockImplementation((url, init = {}) => {
  const { pathname, searchParams } = new URL(url, 'http://localhost');
  const answer = routes[`${init.method ?? 'GET'} ${pathname}`];
  if (!answer) return json({});
  const [data, status] = typeof answer === 'function' ? answer(searchParams, init) : [answer, 200];
  return json(data, status);
});
const alice = { is_logged: true, user_id: 2, user_name: 'alice', full_name: 'Alice', is_admin: false, kind: 'human' };

afterEach(() => vi.restoreAllMocks());


test('project ids people type', () => {
  expect(projectIdProblem('alice/isp')).toBeNull();
  expect(projectIdProblem('')).toBeNull();
  expect(projectIdProblem('isp')).toMatch(/namespace\/name/);
  expect(projectIdProblem('alice/')).toMatch(/end/);
  expect(projectIdProblem('a/../b')).toMatch(/\.\./);
});

describe('New project', () => {
  test('says whom to ask, offers their own namespace, then creates the project', async () => {
    const fetch = mockApi({
      'GET /api/v1/projects/check': params => [params.get('project') === 'camera/isp'
        ? { project: 'camera/isp', allowed: false, exists: false, reason: 'project_create_denied', error: "Only the developers and owners of the namespace camera create its projects.",
            hint: "Ask an owner of camera (bob) for the developer role.", suggestion: 'alice/isp' }
        : { project: params.get('project'), allowed: true, exists: false, namespace: 'alice', namespace_kind: 'user' }, 200],
      'POST /api/v1/projects': () => [{ id: 'alice/isp' }, 201],
    });
    const push = vi.spyOn(history, 'push').mockImplementation(() => {});
    renderWithProviders(<NewProjectDialog isOpen onClose={() => {}}/>, { user: alice });
    const input = screen.getByLabelText('Project');
    expect(input.value).toBe('alice/');
    fireEvent.change(input, { target: { value: 'camera/isp' } });
    expect(await screen.findByText(/Ask an owner of camera \(bob\)/)).toBeInTheDocument();
    expect(screen.getByRole('button', { name: 'Create' })).toBeDisabled();
    fireEvent.click(screen.getByRole('button', { name: 'Use alice/isp' }));
    expect(await screen.findByText(/You can create it/)).toBeInTheDocument();
    fireEvent.click(screen.getByRole('button', { name: 'Create' }));
    await waitFor(() => expect(push).toHaveBeenCalledWith('/alice/isp'));
    const posted = fetch.mock.calls.find(([, init]) => init?.method === 'POST');
    expect(JSON.parse(posted[1].body)).toEqual({ project: 'alice/isp' });
  });
});

describe('Project section of the Access page', () => {
  test('owners archive, with a confirmation', async () => {
    let archived = false;
    const fetch = mockApi({
      'GET /api/v1/project/lifecycle': () => [{ id: 'alice/isp', created_at: '2026-11-01T00:00:00Z', created_by: 'alice', created_via: 'api',
        namespace: { path: 'alice', kind: 'user' }, repositories: [{ git_host: 'github.com', repo_path: 'alice/isp', subdir: '' }],
        archived, archived_at: archived ? '2026-11-02T00:00:00Z' : null, can_archive: true }, 200],
      'POST /api/v1/project/archive': (_, init) => { archived = JSON.parse(init.body).archived; return [{ archived }, 200]; },
    });
    renderWithProviders(<ProjectLifecycle project="alice/isp"/>, { user: alice });
    expect(await screen.findByText(/with New project/)).toBeInTheDocument();
    expect(screen.getByText(/github.com:alice\/isp/)).toBeInTheDocument();
    fireEvent.click(screen.getByRole('button', { name: 'Archive…' }));
    fireEvent.click(await screen.findByRole('button', { name: 'Archive' }));
    expect(await screen.findByRole('button', { name: 'Unarchive' })).toBeInTheDocument();
    expect(fetch.mock.calls.some(([, init]) => init?.method === 'POST')).toBe(true);
  });
});
