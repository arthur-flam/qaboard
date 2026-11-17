// Experiments as a tree, folded by the slashes of their names: "backbone/lr-sweep" is under "backbone".
// Rows come from GET /api/v1/experiments: { experiment, runs, running, pending, failed, batches, latest, users }.
// Pure, tested in __tests__/runs.test.js.

const empty_totals = () => ({ runs: 0, running: 0, pending: 0, failed: 0, batches: 0, latest: null, users: new Set(), from_code: false });

const add = (totals, row) => {
  totals.runs += row.runs ?? 0;
  totals.running += row.running ?? 0;
  totals.pending += row.pending ?? 0;
  totals.failed += row.failed ?? 0;
  totals.batches += row.batches ?? 0;
  if (row.latest && (!totals.latest || row.latest > totals.latest)) totals.latest = row.latest;
  for (const user of row.users ?? []) totals.users.add(user);
  totals.from_code = totals.from_code || !!row.from_code;
};

// A batch's experiment: its label without the "@user| " of runs made outside the CI (backend/backend/runs_list.py)
export const experiment_of = label => String(label ?? 'default').replace(/^@[^|]+\| /, '');

// Only slashes fold: names with "|" or spaces show as they are. Empty parts ("a//b") are ignored.
export const path_parts = experiment => String(experiment ?? '').split('/').filter(part => part !== '');

// { name, path, children: [nodes], own: the row of this exact experiment or null, totals: own and everything under it }
// Siblings: the latest first. A level with a single child and no runs of its own is merged with it, like folders in
// code editors ("detector/backbone" rather than two levels to open), unless `compact` is false.
export const build_tree = (rows, { compact = true } = {}) => {
  const root = { name: '', path: '', children: new Map(), own: null, totals: empty_totals() };
  for (const row of rows ?? []) {
    const parts = path_parts(row.experiment);
    if (parts.length === 0) continue;
    let node = root;
    add(root.totals, row);
    parts.forEach((part, index) => {
      if (!node.children.has(part)) {
        const path = parts.slice(0, index + 1).join('/');
        node.children.set(part, { name: part, path, children: new Map(), own: null, totals: empty_totals() });
      }
      node = node.children.get(part);
      add(node.totals, row);
    });
    node.own = row;
  }
  const finish = node => {
    let children = [...node.children.values()].map(finish);
    children.sort((a, b) => (b.totals.latest ?? '').localeCompare(a.totals.latest ?? '') || a.name.localeCompare(b.name));
    let result = { ...node, children };
    while (compact && result !== root && result.children.length === 1 && !result.own && result.path) {
      const [only] = result.children;
      result = { ...only, name: `${result.name}/${only.name}` };
    }
    return result;
  };
  const tree = finish(root);
  // the root itself is never merged
  return { ...tree, children: tree.children };
};

// The node at a path, or null
export const find_node = (tree, path) => {
  const target = path_parts(path).join('/');
  if (!target) return tree;
  const visit = node => {
    if (node.path === target) return node;
    // a level merged with its only child ("detector" in "detector/backbone") is that node
    const merged = node.children.find(child => child.path.startsWith(`${target}/`));
    if (merged && path_parts(target).length > path_parts(node.path).length) return merged;
    for (const child of node.children)
      if (target === child.path || target.startsWith(`${child.path}/`)) {
        const found = visit(child);
        if (found) return found;
      }
    return null;
  };
  return visit(tree);
};

// The paths to open so that `path` shows: its ancestors' paths in the tree
export const ancestors = (tree, path) => {
  const target = path_parts(path).join('/');
  const found = [];
  const visit = node => {
    for (const child of node.children)
      if (target === child.path || target.startsWith(`${child.path}/`)) {
        found.push(child.path);
        visit(child);
        return;
      }
  };
  if (target) visit(tree);
  return found.filter(p => p !== target);
};

// What a run's experiment is called under a node: "warmup" under "backbone/lr-sweep", "" for the node's own runs
export const relative_experiment = (experiment, node_path) => {
  const parts = path_parts(experiment);
  const node = path_parts(node_path);
  if (node.length === 0) return parts.join('/');
  if (node.every((part, i) => parts[i] === part)) return parts.slice(node.length).join('/');
  return parts.join('/');
};
