// The code changes local runs recorded: what wasn't committed, or not pushed, when they ran (`qa run` and
// `qaboard.start_run()` send them as `output.data.local_changes`, CI runs don't). Design: docs/rfcs/local-changes.md.
// Everything here is untrusted text from whoever ran the code: shown as text, never as HTML.
import { createContext } from "react";

// The state all the runs shown share (state_key), when they share one: the batch's callout says it once, and the runs'
// tags don't repeat it. null elsewhere (e.g. History): each run shows its tag.
export const LocalChangesSharedContext = createContext(null);

// The commit page's view that lists the batch's distinct states (the sidebar's "Local changes")
export const local_changes_view = 'local-changes';

export const local_changes_of = output => {
  const changes = output?.data?.local_changes;
  return changes && typeof changes === 'object' ? changes : null;
};

export const short_sha = sha => typeof sha === 'string' ? sha.slice(0, 8) : '';

// true / false, or null when unknown (git was too slow, or failed)
export const is_dirty = changes => changes?.dirty === true ? true : changes?.dirty === false ? false : null;

const number = value => typeof value === 'number' && Number.isFinite(value) ? value : 0;

export const files_count = changes => number(changes?.files_total) || (Array.isArray(changes?.files) ? changes.files.length : 0);

// "+40 −12 · 3 files"
export const changes_summary = changes => {
  const files = files_count(changes);
  const lines = `+${number(changes?.added)} −${number(changes?.deleted)}`;
  return `${lines} · ${files} file${files === 1 ? '' : 's'}`;
};

// "3 files, +40 −12", for sentences
export const changes_sentence = changes => {
  const files = files_count(changes);
  return `${files} file${files === 1 ? '' : 's'}, +${number(changes?.added)} −${number(changes?.deleted)}`;
};

export const unpushed_count = changes => number(changes?.unpushed_commits);

// "· 2 unpushed commits" is said only when we know the commit isn't on the remote
export const unpushed_text = changes => {
  if (changes?.pushed !== false) return '';
  const n = unpushed_count(changes);
  return n > 0 ? `${n} unpushed commit${n === 1 ? '' : 's'}` : 'not pushed';
};

// What identifies a set of changes: the patch's content address. Runs with the same key ran the same code.
export const state_key = changes => {
  if (!changes) return null;
  const dirty = is_dirty(changes);
  if (dirty === null) return 'unknown';
  const unpushed = changes.pushed === false ? `+${changes.unpushed?.sha256 ?? unpushed_count(changes)}` : '';
  if (dirty === false) return `clean${unpushed}`;
  const sha256 = typeof changes.sha256 === 'string' && changes.sha256
    ? changes.sha256
    : `summary:${files_count(changes)}:${number(changes.added)}:${number(changes.deleted)}`;
  return `${sha256}${unpushed}`;
};

export const kind_of = changes => {
  const dirty = is_dirty(changes);
  return dirty === null ? 'unknown' : dirty ? 'changes' : 'clean';
};

// The distinct states of these runs, in one pass: { groups: [{ key, kind, changes, output, outputs, count }], missing,
// shared } — groups with the most runs first, `missing` the runs that didn't record their changes (CI, older runs),
// `shared` the key when all the runs recorded the same state.
export const group_local_changes = outputs => {
  const by_key = new Map();
  let missing = 0;
  let total = 0;
  for (const output of outputs) {
    if (!output) continue;
    total += 1;
    const changes = local_changes_of(output);
    if (!changes) {
      missing += 1;
      continue;
    }
    const key = state_key(changes);
    const group = by_key.get(key);
    if (group) {
      group.outputs.push(output);
      group.count += 1;
    } else {
      by_key.set(key, { key, kind: kind_of(changes), changes, output, outputs: [output], count: 1 });
    }
  }
  const groups = [...by_key.values()].sort((a, b) => b.count - a.count);
  const shared = groups.length === 1 && missing === 0 ? groups[0].key : null;
  return { groups, missing, total, shared, has_data: groups.length > 0 };
};

// Whether any run says its commit isn't pushed: { sha } of the first, or null. Stops at the first.
export const unpushed_commit = outputs => {
  for (const output of outputs) {
    const changes = local_changes_of(output);
    if (changes?.pushed === false) return { sha: changes.head ?? null, changes };
  }
  return null;
};

export const has_local_changes_data = outputs => {
  for (const output of outputs)
    if (local_changes_of(output)) return true;
  return false;
};


// A file the run saved, as a URL next to its other files. The path comes from the run: only a relative path inside
// its folder, nothing that would point elsewhere.
export const safe_relative_path = path => {
  if (typeof path !== 'string' || !path || path.length > 500) return null;
  if (path.startsWith('/') || path.includes('\\') || /^[a-z][a-z0-9+.-]*:/i.test(path)) return null;
  if (path.split('/').some(part => part === '..' || part === '')) return null;
  return path;
};

// which: 'patch' (uncommitted changes) or 'unpushed' (unpushed commits)
export const saved_file = (changes, which = 'patch') => {
  const file = changes?.[which];
  if (!file || typeof file !== 'object') return null;
  return safe_relative_path(file.path);
};

export const patch_url = (output, changes, which = 'patch') => {
  const path = saved_file(changes, which);
  if (!path || !output?.output_dir_url) return null;
  return `${output.output_dir_url}/${path.split('/').map(encodeURIComponent).join('/')}`;
};

const kb = bytes => Math.max(1, Math.round(number(bytes) / 1024));

// Why a patch isn't saved, in words. null when it is, or there was nothing to save.
export const omitted_message = (file, { what = 'patch' } = {}) => {
  const reason = file?.omitted;
  if (!reason) return null;
  if (reason === 'too_large')
    return file.bytes
      ? `The ${what} is ${kb(file.bytes)} KB: too large to save (outputs.local_changes.max_patch_kb).`
      : `The ${what} was too large to save (outputs.local_changes.max_patch_kb).`;
  if (reason === 'level') return "This server keeps only a summary of the code changes.";
  if (reason === 'timeout') return `git took too long to make the ${what}: it wasn't saved.`;
  if (reason === 'unreadable') return `Some changed files couldn't be read: the ${what} wasn't saved.`;
  if (reason === 'error') return `git failed to make the ${what}: it wasn't saved.`;
  return `The ${what} wasn't saved (${String(reason).slice(0, 40)}).`;
};

export const status_names = { M: 'modified', A: 'added', D: 'deleted', '?': 'new, untracked', R: 'renamed', C: 'copied', T: 'type changed', U: 'unmerged' };

// How to get the run's code back
export const restore_command = output => `qa restore ${output?.id ?? ''}`.trim();
export const git_commands = changes => {
  const head = typeof changes?.head === 'string' ? changes.head : '<commit>';
  const commands = [`git checkout ${head} && git apply local-changes.patch`];
  if (saved_file(changes, 'unpushed') && typeof changes?.base === 'string')
    commands.push(`git checkout ${changes.base} && git apply unpushed.patch && git apply local-changes.patch`);
  return commands;
};

// The lines of a patch, and where each file starts ("diff --git a/x b/x"): { lines, files: Map(path => line number) }.
// One pass, without splitting the text.
export const index_patch = text => {
  const files = new Map();
  if (typeof text !== 'string' || !text) return { lines: 1, files };
  let line = 1;
  let start = 0;
  while (start <= text.length) {
    if (text.startsWith('diff --git ', start)) {
      const end = text.indexOf('\n', start);
      const header = text.slice(start, end === -1 ? undefined : end);
      // "diff --git a/src/a.py b/src/a.py": the new path
      const b = header.lastIndexOf(' b/');
      if (b !== -1) {
        const path = header.slice(b + 3);
        if (!files.has(path)) files.set(path, line);
      }
    }
    const next = text.indexOf('\n', start);
    if (next === -1) break;
    line += 1;
    start = next + 1;
  }
  return { lines: line, files };
};
