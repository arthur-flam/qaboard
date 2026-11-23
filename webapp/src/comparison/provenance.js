// Why a reference is there, from what we know about its commit, and what we call it when users didn't name it.
// The comparison header (components/ComparisonHeader.jsx) says it next to each reference, and the references are
// called by it everywhere (useReferences in src/hooks.js): "develop", "a3f9c21", "nightly"... instead of "ref 2".
//
// kind:
//   'loading'      we don't have the commit yet
//   'milestone'    a milestone (it's called by its name, see side_label in src/hooks.js)
//   'same-runs'    the new results themselves: nothing to compare
//   'same-commit'  the new commit, with another batch or filter
//   'latest'       the latest commit of the reference branch
//   'behind'       an older commit of the reference branch
//   'earlier'      a commit of the new commit's own branch
//   'commit'       any other commit
import { pretty_label } from "../utils";

export const short_sha = id => (id ?? '').slice(0, 7);

// side: { batch, filter, milestone }. context: { commit, new_commit, new_batch, new_filter, reference_branch, head }
// head: the latest commit of the reference branch, if we know it
export const provenance = (side, { commit, new_commit, new_batch, new_filter, reference_branch, head }) => {
  if (side.milestone) return { kind: 'milestone' };
  if (!commit?.id) return { kind: 'loading' };
  const batch = side.batch || 'default';
  if (new_commit?.id && commit.id === new_commit.id) {
    if (batch === (new_batch || 'default') && (side.filter ?? '') === (new_filter ?? ''))
      return { kind: 'same-runs', name: 'same runs' };
    return { kind: 'same-commit', name: batch !== (new_batch || 'default') ? pretty_label({ label: batch }) : `filter ${side.filter || 'none'}` };
  }
  if (reference_branch && commit.branch === reference_branch) {
    if (head?.id && head.id === commit.id) return { kind: 'latest', name: reference_branch, branch: reference_branch };
    return { kind: 'behind', name: short_sha(commit.id), branch: reference_branch };
  }
  if (new_commit?.branch && commit.branch === new_commit.branch)
    return { kind: 'earlier', name: short_sha(commit.id), branch: commit.branch };
  return { kind: 'commit', name: short_sha(commit.id), branch: commit.branch };
};

// Two references with the same name: we add what tells them apart, their batch, else their filter.
// `taken`: names they can't have, e.g. the new results': those get none (the caller falls back to "ref", "ref 2"...)
export const distinct_names = (names, sides, taken = []) => names.map(name => taken.includes(name) ? undefined : name).map((name, index, names) => {
  if (!name || names.filter(other => other === name).length < 2) return name;
  const side = sides[index];
  const batch = side.batch || 'default';
  if (batch !== 'default') return `${name} · ${pretty_label({ label: batch })}`;
  if (side.filter) return `${name} · ${side.filter}`;
  const before = names.slice(0, index).filter(other => other === name).length;
  return before === 0 ? name : `${name} (${before + 1})`;
});
