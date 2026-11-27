// Links that open a commit's Run Tests / Tuning filled in, e.g. prepared by an assistant (`qa mcp`'s prepare_tuning):
//   /<project>/commit/<commit_id>?selected_views=tuning&prefill=<URL-encoded JSON>
// The JSON is {label, batches, parameter_search, search_type, n_iter}, all optional, other keys ignored. People check
// the form and click Start themselves: a link only fills what they could type in it, and never starts anything.
import { path_segment } from '../../selection';
import { model_from_parameters } from './model';
import { prefill_form, clean_label } from './prefill';
import { describe_request } from './history';

export const limits = { text: 50000, label: 200, batches: 50, batch: 200, sets: 20, parameters: 100, key: 200, values: 1000, depth: 8, n_iter: 10000 };

const is_object = value => value !== null && typeof value === 'object' && !Array.isArray(value);
// lists in lists in objects... a matrix is 2 levels, a value's own nesting is capped
const within_depth = (value, depth) => value === null || typeof value !== 'object'
  || (depth > 0 && Object.values(value).every(v => within_depth(v, depth - 1)));

// Each field: (value) => null when it can be used, else why not
const checks = {
  label: value => typeof value !== 'string' ? 'it is not text'
    : value.trim().length > limits.label ? `it is longer than ${limits.label} characters` : null,
  batches: value => !Array.isArray(value) || value.some(name => typeof name !== 'string' || !name.trim()) ? 'it is not a list of names'
    : value.length > limits.batches ? `it has more than ${limits.batches} batches`
    : value.some(name => name.trim().length > limits.batch) ? `a name is longer than ${limits.batch} characters`
    // the form reads "a, b" as two batches
    : value.some(name => name.includes(',')) ? 'a name has a comma' : null,
  parameter_search: value => {
    const sets = Array.isArray(value) ? value : [value];
    if (!sets.every(is_object)) return 'it is not {"parameter": [values]}, or a list of those';
    if (sets.length > limits.sets) return `it has more than ${limits.sets} sets of values`;
    const keys = sets.flatMap(Object.keys);
    if (keys.length > limits.parameters) return `it has more than ${limits.parameters} parameters`;
    if (keys.some(key => key.length > limits.key)) return `a parameter's name is longer than ${limits.key} characters`;
    if (sets.some(set => Object.values(set).some(values => Array.isArray(values) && values.length > limits.values))) return `a parameter has more than ${limits.values} values`;
    if (!within_depth(value, limits.depth)) return 'its values are nested too deep';
    return null;
  },
  search_type: value => ['grid', 'sampler'].includes(value) ? null : 'it is neither "grid" nor "sampler"',
  n_iter: value => Number.isInteger(value) && value >= 1 && value <= limits.n_iter ? null : `it is not a whole number from 1 to ${limits.n_iter}`,
};
// as the notice names them (describe_link)
const names = { label: 'Batch label', batches: 'Tests', parameter_search: 'Values to try', search_type: 'Runs', n_iter: 'Sample size' };

/**
 * What a link's `prefill` can fill: {prefill, warnings}. `prefill` has the fields that can be used, `warnings` says
 * in a sentence each why the others were left out. null fields are as if not given.
 */
export const parse_prefill_link = raw => {
  const unreadable = why => ({ prefill: {}, warnings: [`The link's values could not be read: ${why}. The form is as you left it.`] });
  if (typeof raw !== 'string') return unreadable('they are not JSON');
  if (raw.length > limits.text) return unreadable(`they are longer than ${limits.text} characters`);
  let data;
  try {
    data = JSON.parse(raw);
  } catch {
    return unreadable('they are not JSON');
  }
  if (!is_object(data)) return unreadable('they are not a JSON object');
  const prefill = {};
  const warnings = [];
  for (const [field, check] of Object.entries(checks)) {
    const value = data[field];
    if (value === undefined || value === null) continue;
    const why = check(value);
    if (why) warnings.push(`${names[field]}: not filled in, ${why}.`);
    else prefill[field] = typeof value === 'string' ? value.trim() : field === 'batches' ? value.map(name => name.trim()) : value;
  }
  return { prefill, warnings };
};

// The form runs text that isn't JSON as a function (model.js): what a link fills must stay JSON
const is_json = text => {
  try {
    JSON.parse(text);
    return true;
  } catch {
    return false;
  }
};

/** What to put in the form's stored state (stores/prefs.js), from what parse_prefill_link kept */
export const form_from_link = ({ label, batches, parameter_search, search_type, n_iter }) => {
  const model = parameter_search === undefined ? undefined : model_from_parameters(parameter_search);
  const search_options = n_iter ? { n_iter } : undefined;
  const form = prefill_form({ label: label === undefined ? undefined : clean_label(label), batches, model, search_type, search_options });
  if (form.parameter_search && !is_json(form.parameter_search)) {
    delete form.parameter_search;
    delete form.values_mode;
  }
  // without values to try, a link can still choose how to run the form's
  if (!model && search_type) form.search_type = search_type;
  if (search_options) form.search_options = search_options;
  return form;
};

const shown_names = (list, max = 5) => list.length > max ? `${list.slice(0, max).join(', ')} and ${list.length - max} more` : list.join(', ');

/** What the link filled, for people to check: [[what, value]] */
export const describe_link = ({ label, batches, parameter_search, search_type, n_iter }) => {
  const how = search_type === 'sampler' ? `a random sample${n_iter ? ` of ${n_iter}` : ''}` : search_type === 'grid' ? 'every combination' : null;
  return [
    label !== undefined && ['Batch label', clean_label(label) || '(empty)'],
    batches !== undefined && ['Tests', batches.length ? shown_names(batches) : '(none)'],
    parameter_search !== undefined && ['Values to try', describe_request({ tuning_search: { search_type, parameter_search, search_options: { n_iter } } })],
    how && ['Runs', how],
  ].filter(Boolean);
};

/** The link that opens a commit's Run Tests / Tuning filled with `prefill` */
export const prefill_link = (project, commit_id, prefill) =>
  `/${path_segment(project)}/commit/${path_segment(commit_id)}?selected_views=tuning&prefill=${encodeURIComponent(JSON.stringify(prefill))}`;
