/**
 * The projects list: sites have hundreds of projects, we render them progressively.
 * Run with: cd webapp && npm test -- ProjectsList
 */
import { act, fireEvent, screen, waitFor } from '@testing-library/react';
import { MemoryRouter } from 'react-router';

import ProjectsList from '../ProjectsList';
import { usePrefsStore } from '../stores/prefs';
import { renderWithProviders } from '../test-utils';


const nb_projects = 120;
const project_ids = Array.from({ length: nb_projects }, (_, i) => `group/project-${String(i).padStart(3, '0')}`);
// what /api/v1/projects?summary=true gives
const projects = Object.fromEntries(project_ids.map((id, i) => [id, {
  data: { git: { path_with_namespace: id, name: id }, qatools_config: { project: { name: id } } },
  latest_output_datetime: null,
  // the first projects have the most recent commits
  latest_commit_datetime: new Date(Date.UTC(2026, 9, 1) - i * 3600 * 1000).toISOString(),
  total_commits: 10,
}]));

const fetchMock = vi.fn(async url => {
  if (String(url).startsWith('/api/v1/projects'))
    return new Response(JSON.stringify(projects), { headers: { 'Content-Type': 'application/json' } });
  return new Response(JSON.stringify({ is_authenticated: false }), { headers: { 'Content-Type': 'application/json' } });
});

// getAllByRole is slow with many elements
const shown_projects = () => [...document.querySelectorAll('h5')].map(h => h.textContent);

const renderList = () => renderWithProviders(<MemoryRouter><ProjectsList/></MemoryRouter>, { siteConfig: {} });


describe('ProjectsList', () => {
  beforeEach(() => {
    vi.stubGlobal('fetch', fetchMock);
    usePrefsStore.setState({ favorites: {} });
  });
  afterEach(() => vi.unstubAllGlobals());

  it('asks for a summary of the projects', async () => {
    renderList();
    await screen.findByText('group/project-000');
    expect(fetchMock).toHaveBeenCalledWith('/api/v1/projects?summary=true', expect.anything());
  });

  it('renders the first projects, and more on demand', async () => {
    renderList();
    await screen.findByText('group/project-000');
    expect(shown_projects()).toHaveLength(50);
    expect(screen.getByText(`${nb_projects} projects`)).toBeInTheDocument();
    fireEvent.click(screen.getByText('Show more (70)'));
    expect(shown_projects()).toHaveLength(100);
    fireEvent.click(screen.getByText('Show more (20)'));
    expect(shown_projects()).toHaveLength(nb_projects);
    expect(screen.queryByText(/Show more/)).not.toBeInTheDocument();
  });

  it('filters all the projects, not only those shown', async () => {
    renderList();
    await screen.findByText('group/project-000');
    fireEvent.change(screen.getByPlaceholderText('filter projects...'), { target: { value: 'project-11' } });
    expect(shown_projects()).toEqual(project_ids.filter(id => id.includes('project-11')));
    expect(screen.getByText('10 filtered projects')).toBeInTheDocument();
  });

  it('shows favorites first, even those that would not be shown yet', async () => {
    usePrefsStore.setState({ favorites: { 'group/project-119': true } });
    renderList();
    await screen.findByText('group/project-119');
    expect(shown_projects()[0]).toBe('group/project-119');
    expect(shown_projects()[1]).toBe('group/project-000');
  });

  it('loads more when users scroll to the end', async () => {
    let observer_callback;
    vi.stubGlobal('IntersectionObserver', class {
      constructor(callback) { observer_callback = callback; }
      observe() {}
      disconnect() {}
    });
    renderList();
    await screen.findByText('group/project-000');
    expect(shown_projects()).toHaveLength(50);
    act(() => observer_callback([{ isIntersecting: true }]));
    await waitFor(() => expect(shown_projects()).toHaveLength(100));
  });
});
