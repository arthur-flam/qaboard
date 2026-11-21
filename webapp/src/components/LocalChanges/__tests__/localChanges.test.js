/**
 * What local runs recorded about their code: wording, grouping, saved files.
 * Run with: cd webapp && npm test -- localChanges
 */
import {
  changes_summary, changes_sentence, git_commands, group_local_changes, index_patch, omitted_message, patch_url,
  safe_relative_path, state_key, unpushed_commit, unpushed_text,
} from '../localChanges';
import { filter_batch } from '../../../utils';
import { language } from '../../../viewers/text';
import { is_patch } from '../../../viewers/fileTypes';

const changes = (more = {}) => ({
  level: 'patch', scope: 'run', dirty: true, head: '3f2a1b9c0d1e2f30', branch: 'feature/x', upstream: 'origin/feature/x',
  files: [{ path: 'src/a.py', status: 'M', added: 30, deleted: 10 }, { path: 'src/b.py', status: 'A', added: 10, deleted: 0 }, { path: 'img.png', status: 'M', added: null, deleted: null, binary: true }],
  files_total: 3, added: 40, deleted: 12, sha256: 'aaa', patch: { path: '.qaboard/local-changes.patch', bytes: 1234 },
  ...more,
});
const run = (id, local_changes) => ({ id, test_input_path: `in${id}.raw`, output_dir_url: `/s/${id}`, data: local_changes === undefined ? {} : { local_changes } });

describe('wording', () => {
  it('counts lines and files', () => {
    expect(changes_summary(changes())).toBe('+40 −12 · 3 files');
    expect(changes_summary(changes({ files_total: 1, added: 1, deleted: 0 }))).toBe('+1 −0 · 1 file');
    expect(changes_sentence(changes())).toBe('3 files, +40 −12');
  });

  it('says unpushed commits only when known', () => {
    expect(unpushed_text(changes())).toBe('');
    expect(unpushed_text(changes({ pushed: true, unpushed_commits: 0 }))).toBe('');
    expect(unpushed_text(changes({ pushed: false, unpushed_commits: 2 }))).toBe('2 unpushed commits');
    expect(unpushed_text(changes({ pushed: false, unpushed_commits: 1 }))).toBe('1 unpushed commit');
  });

  it('explains why a patch is missing', () => {
    expect(omitted_message({ path: 'x' })).toBeNull();
    expect(omitted_message({ omitted: 'too_large', bytes: 3 * 1024 * 1024 })).toBe('The patch is 3072 KB: too large to save (outputs.local_changes.max_patch_kb).');
    expect(omitted_message({ omitted: 'level' })).toBe('This server keeps only a summary of the code changes.');
    expect(omitted_message({ omitted: 'timeout' })).toMatch(/took too long/);
    expect(omitted_message({ omitted: 'unreadable' })).toMatch(/couldn't be read/);
    expect(omitted_message({ omitted: 'error' })).toMatch(/git failed/);
  });

  it('says how to reproduce with git', () => {
    expect(git_commands(changes())).toEqual(['git checkout 3f2a1b9c0d1e2f30 && git apply local-changes.patch']);
    expect(git_commands(changes({ base: 'bbbb', pushed: false, unpushed: { path: '.qaboard/unpushed.patch', bytes: 10, sha256: 'u' } }))).toEqual([
      'git checkout 3f2a1b9c0d1e2f30 && git apply local-changes.patch',
      'git checkout bbbb && git apply unpushed.patch && git apply local-changes.patch',
    ]);
  });
});

describe('saved files', () => {
  it('only links to files inside the run', () => {
    expect(patch_url(run(1), changes())).toBe('/s/1/.qaboard/local-changes.patch');
    for (const path of ['../../etc/passwd', '/etc/passwd', 'https://evil.example/x', 'a//b', 'a\\b', ''])
      expect(safe_relative_path(path)).toBeNull();
    expect(patch_url(run(1), changes({ patch: { omitted: 'level' } }))).toBeNull();
  });
});

describe('group_local_changes', () => {
  it('groups runs by the content of their changes', () => {
    const outputs = [run(1, changes()), run(2, changes()), run(3, changes({ sha256: 'bbb' })), run(4, changes({ dirty: false })), run(5, changes({ dirty: null })), run(6)];
    const { groups, missing, shared, has_data } = group_local_changes(outputs);
    expect(has_data).toBe(true);
    expect(missing).toBe(1);
    expect(shared).toBeNull();
    expect(groups.map(g => [g.kind, g.count])).toEqual([['changes', 2], ['changes', 1], ['clean', 1], ['unknown', 1]]);
    expect(groups[0].outputs.map(o => o.id)).toEqual([1, 2]);
  });

  it('says when all the runs share one state', () => {
    expect(group_local_changes([run(1, changes()), run(2, changes())]).shared).toBe('aaa');
    expect(group_local_changes([run(1, changes({ dirty: false })), run(2, changes({ dirty: false }))]).shared).toBe('clean');
    // unpushed commits are part of the state
    expect(state_key(changes({ pushed: false, unpushed: { sha256: 'u1' } }))).not.toBe(state_key(changes({ pushed: false, unpushed: { sha256: 'u2' } })));
    expect(group_local_changes([run(1), run(2)])).toMatchObject({ has_data: false, shared: null });
  });

  it('is fast on many runs', () => {
    const outputs = Array.from({ length: 20000 }, (_, i) => run(i, changes({ sha256: `${i % 7}` })));
    const start = performance.now();
    expect(group_local_changes(outputs).groups).toHaveLength(7);
    expect(performance.now() - start).toBeLessThan(200);
  });

  it('finds unpushed commits', () => {
    expect(unpushed_commit([run(1, changes()), run(2, changes({ pushed: false }))])).toMatchObject({ sha: '3f2a1b9c0d1e2f30' });
    expect(unpushed_commit([run(1, changes({ pushed: true }))])).toBeNull();
  });
});

describe('index_patch', () => {
  it('finds where each file starts', () => {
    const text = 'diff --git a/src/a.py b/src/a.py\nindex 1..2\n--- a/src/a.py\n+++ b/src/a.py\n@@ -1 +1 @@\n-a\n+b\ndiff --git a/new.txt b/new.txt\n+x\n';
    const { lines, files } = index_patch(text);
    expect(lines).toBe(10);
    expect(files.get('src/a.py')).toBe(1);
    expect(files.get('new.txt')).toBe(8);
    expect(index_patch(undefined).files.size).toBe(0);
  });
});

describe('the run filter', () => {
  const batch = { outputs: { 1: run(1, changes()), 2: run(2, changes({ dirty: false })), 3: run(3), 4: run(4, changes({ dirty: null })) } };
  it('keeps runs with or without local changes', () => {
    expect(filter_batch(batch, 'changes:local').outputs).toEqual(['1']);
    expect(filter_batch(batch, 'changes:none').outputs).toEqual(['2']);
    expect(filter_batch(batch, 'changes:unknown').outputs).toEqual(['4']);
    expect(filter_batch(batch, '-changes:local').outputs).toEqual(['2', '3', '4']);
  });
});

describe('file viewers', () => {
  it('highlight patches as diffs', () => {
    expect(is_patch('.qaboard/local-changes.patch')).toBe(true);
    expect(language('fix.diff')).toBe('diff');
    expect(language('local-changes.patch')).toBe('diff');
    expect(language('config.yaml')).toBe('yaml');
    expect(language('log.txt')).toBe('plaintext');
  });
});
