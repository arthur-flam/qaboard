import { useEffect, useMemo, useRef, useState } from "react";
import { useQuery, keepPreviousData } from "@tanstack/react-query";

import { http, errorMessage, isArtifactsError } from "../../api/http";
import { requestLogin } from "../authentication/login";
import { useUser, useRefreshCommit, updateSelected, useSiteConfig } from "../../hooks";
import { usePrefsStore, useTuningForm } from "../../stores/prefs";

import MonacoEditor from "../MonacoEditor";

import {
  Classes,
  Callout,
  Intent,
  Button,
  FormGroup,
  HTMLSelect,
  InputGroup,
  Radio,
  RadioGroup,
  SegmentedControl,
  Tag,
  Tooltip,
  Icon,
} from "@blueprintjs/core";
import styled from "styled-components";
import { toaster } from "../../toaster"
import { track } from "../../analytics"


import templates from './templates'
import OptimizeSetup from './OptimizeSetup'
import { template as optimize_template, parse as parse_optimize, check as check_optimize, continuation, from_space, grid_to_space, recommended_evaluations, known_parameters } from './optimize_config'
import Learn from './learn'
import BatchPicker, { parse_selection, useBatches } from './BatchPicker'
import ParametersTable from './ParametersTable'
import StartFrom from './StartFrom'
import { model_from_text, model_to_parameters, count_combinations, count_runs } from './model'
import { transformCDERegisters } from './registers'
import { form_from_request } from './history'
import { BatchesForThisRunDialog, BatchesForThisRunCallout, changed_batches, expand_selection, snapshot_for_this_run } from './BatchesForThisRun'
import { LegacyTuningForm } from './LegacyTuningForm'
import { useTuningFormVersion, NewFormActions, PreviousFormBanner } from './FormVersion'


const MAX_RUNS = 5000;

// How to choose the parameters' values: a two-way switch next to the step's title, and one line on the chosen one
const Modes = styled.div`
  display: inline-flex;
  flex-wrap: wrap;
  gap: 2px;
  padding: 3px;
  border-radius: 8px;
  background: var(--qa-surface-alt);
  border: 1px solid var(--qa-border);
  .qa-tuning-mode { display: inline-flex; align-items: center; gap: 7px; padding: 5px 12px; border: 0; border-radius: 6px; background: none; font: inherit; font-weight: 500; color: var(--qa-text-secondary); cursor: pointer; }
  .qa-tuning-mode:hover { color: var(--qa-text); background: var(--qa-hover); }
  .qa-tuning-mode[aria-checked="true"] { background: var(--qa-surface); color: var(--qa-text); box-shadow: 0 1px 2px rgba(0, 0, 0, 0.18), 0 0 0 1px var(--qa-border); }
  .qa-tuning-mode[aria-checked="true"] .bp6-icon { color: var(--qa-intent-primary); }
  .qa-tuning-mode:focus-visible { outline: 2px solid var(--qa-intent-primary); outline-offset: 1px; }
`;
const modes = [
  {
    id: 'manual', icon: 'grid-view', title: 'Choose the values',
    text: 'Run the tests as they are, or with the values you list: every combination, or a sample.',
  },
  {
    id: 'optimize', icon: 'predictive-analysis', title: 'Search automatically',
    text: 'Automated tuning: QA-Board tries values, learns from each result and homes in on the best for the metrics you choose.',
  },
];

// Three numbered steps, separated by a line instead of nested boxes, and a bar that keeps Start in view
const Sections = styled.div`
  .qa-tuning-top { display: flex; flex-wrap: wrap; gap: 10px; align-items: center; justify-content: space-between; margin-bottom: 18px; }
  .qa-tuning-top-text { color: var(--qa-text-secondary); }
  .qa-step { padding: 20px 0 24px; border-top: 1px solid var(--qa-border); }
  .qa-step:first-of-type { border-top: 0; padding-top: 4px; }
  .qa-step-head { display: flex; flex-wrap: wrap; align-items: center; gap: 10px 14px; margin-bottom: 12px; }
  .qa-step-head h3 { display: flex; align-items: center; gap: 10px; margin: 0; font-size: 16px; font-weight: 600; }
  .qa-step-number { display: inline-flex; align-items: center; justify-content: center; flex: none; width: 24px; height: 24px; border-radius: 50%; font-size: 13px; font-weight: 600; color: #fff; background: var(--qa-intent-primary); }
  .qa-step-number[data-done="true"] { background: var(--qa-text-success); }
  .qa-step-side { margin-left: auto; }
  .qa-step-lead { margin: -4px 0 14px; color: var(--qa-text-secondary); }
  .qa-tuning-bar {
    position: sticky; bottom: 0; z-index: 5;
    display: flex; flex-wrap: wrap; align-items: center; gap: 8px 16px;
    margin: 0 -20px; padding: 12px 20px;
    background: var(--qa-surface); border-top: 1px solid var(--qa-border);
    box-shadow: 0 -6px 12px -8px rgba(0, 0, 0, 0.25);
  }
  .qa-tuning-total { font-size: 15px; }
  .qa-tuning-bar-actions { display: flex; flex-wrap: wrap; align-items: center; gap: 8px 12px; margin-left: auto; }
  .qa-tuning-hint { flex-basis: 100%; font-size: 13px; }
  .qa-tuning-footer { display: flex; flex-wrap: wrap; align-items: center; gap: 4px; margin-top: 12px; font-size: 12px; }
`;

// A step's title, with its number, or a tick once it has what it needs
const StepTitle = ({ id, number, done, children }) => <h3 id={id}>
  <span className="qa-step-number" data-done={done} aria-hidden="true">{done ? <Icon icon="tick" size={14}/> : number}</span>
  {children}
</h3>;

// Text examples, for people who write the values as text
const examples = [
  ['no tuning', 'No tuning'],
  ['simple-combinations', 'Every combination'],
  ['list-of-combinations', 'Values that go together'],
  ['1x2 matrix', 'A matrix'],
  ['function', 'A function'],
];

// How many runs a batch of the commit has, not counting an automated tuning's iterations
const batch_runs = batch => {
  const outputs = Object.values(batch?.outputs ?? {}).filter(o => o.output_type !== 'optim_iteration');
  if (outputs.length > 0) return outputs.length;
  return ['valid_outputs', 'failed_outputs', 'running_outputs', 'pending_outputs'].reduce((total, key) => total + (Number(batch?.[key]) || 0), 0);
};

// "sweep" => "sweep-2", "sweep-2" => "sweep-3": a label no batch of the commit has
export const unused_label = (label, batches = {}) => {
  if (!label || !batches?.[label]) return label;
  const base = label.replace(/-\d+$/, '');
  let index = 2;
  while (batches[`${base}-${index}`]) index++;
  return `${base}-${index}`;
};

// A label for people who don't know what to call it: what it tunes, or what it runs
export const suggest_label = ({ selection = [], sets, search_type }) => {
  const clean = text => String(text).replace(/[^\w.]+/g, '-').replace(/^-+|-+$/g, '');
  const first_parameter = (sets ?? []).flatMap(rows => rows).find(row => row.key.trim() && row.values.length > 1)?.key;
  const tests = selection.length > 0 ? clean(selection[0].replace(/[*?]/g, '')) : '';
  if (search_type === 'optimize') return ['auto-tuning', tests].filter(Boolean).join('-');
  if (first_parameter) return `tune-${clean(first_parameter.split(/[.|/]/).pop())}`;
  return tests ? `run-${tests}` : '';
};

// In qaboard.yaml's tuning.branches, {subproject} is the project's folder in the repository, {subproject.name} its last part
const expand_subproject = (pattern, subproject) => String(pattern)
  .replaceAll('{subproject.name}', subproject.split('/').pop())
  .replaceAll('{subproject}', subproject);

// Whether a branch matches one of the globs of qaboard.yaml's tuning.branches, e.g. ["develop", "*/*{subproject.name}*"].
// "*" matches any characters, "/" included, and "?" one character.
export const branch_matches = (branch, patterns, subproject = '') => patterns.some(pattern => {
  const escape = text => text.replace(/[.+^${}()|[\]\\]/g, '\\$&');
  const regex = expand_subproject(pattern, subproject).split('*').map(part => part.split('?').map(escape).join('.')).join('.*');
  return new RegExp(`^${regex}$`).test(branch);
});

const editor_options = {
  selectOnLineNumbers: true,
  seedSearchStringFromSelection: true,
};

const calculateActualRange = (originalRange, insertedText) => {
  // Calculate the actual range occupied by the inserted text
  const lines = insertedText.split('\n');
  const numNewlines = lines.length - 1;
  
  if (numNewlines === 0) {
    // Single line: extend column by text length
    return {
      startLineNumber: originalRange.startLineNumber,
      startColumn: originalRange.startColumn,
      endLineNumber: originalRange.endLineNumber,
      endColumn: originalRange.startColumn + insertedText.length
    };
  } else {
    // Multi-line: calculate end position based on last line
    const lastLine = lines[lines.length - 1];
    return {
      startLineNumber: originalRange.startLineNumber,
      startColumn: originalRange.startColumn,
      endLineNumber: originalRange.startLineNumber + numNewlines,
      endColumn: lastLine.length + 1
    };
  }
};


const default_search_options = { n_iter: 50 };
const no_tests = { tests: [] };

// What tests a batch (a "group") would run
// `batches_files`: files sent with the request, read last: the batches changed for this run, or as an earlier batch ran them
const useGroupInfo = ({ project, selection, commit_id, available_tests_files, is_logged, batches_files }) => {
  const selected_group = selection.join(', ');
  const query = useQuery({
    queryKey: ['tests-group', project, selected_group, commit_id ?? null, available_tests_files, batches_files ?? null],
    queryFn: async ({ signal }) => {
      // several batches: name[]=a&name[]=b
      const params = { project, name: selection.length > 1 ? selection : selection[0], commit: commit_id };
      const body = { groups: Object.values(available_tests_files ?? {}), ...(batches_files ? { batches_files } : {}) };
      // background: no "Sign in" toast, the form already says to sign in
      return (await http.post('/api/v1/tests/group', body, { params, signal, background: true })).data;
    },
    // the server needs to know who asks
    enabled: !!selected_group && is_logged,
    placeholderData: keepPreviousData,
    staleTime: 60 * 1000,
  });
  if (!selected_group)
    return { group_info: no_tests, loading: false, error: null };
  return {
    group_info: query.error ? no_tests : (query.data ?? no_tests),
    loading: query.isFetching,
    error: query.error ? errorMessage(query.error) : (query.data?.error ?? null),
  };
};


// Waits for users to stop typing
const useDebounced = (value, delay) => {
  const [debounced, setDebounced] = useState(value);
  useEffect(() => {
    const timeout = setTimeout(() => setDebounced(value), delay);
    return () => clearTimeout(timeout);
  }, [value, delay]);
  return debounced;
};

// Asks the server what would go wrong if we started now (unknown batch, missing artifacts...)
const no_check = { errors: [], warnings: [] };
const useTuningCheck = ({ project, commit_id, request, is_logged }) => {
  const debounced_request = useDebounced(request, 500);
  const query = useQuery({
    queryKey: ['tuning-check', project, commit_id, debounced_request],
    queryFn: async ({ signal }) => (await http.post(`/api/v1/commit/${commit_id}/batch/check`, debounced_request, { params: { project }, signal, background: true })).data,
    enabled: !!commit_id && is_logged,
    placeholderData: keepPreviousData,
  });
  // we don't block users if the check itself fails: the server checks again when they start
  if (query.error) return { errors: [], warnings: [`Could not check the batch settings: ${errorMessage(query.error)}`] };
  return query.data ? { errors: query.data.errors ?? [], warnings: query.data.warnings ?? [] } : no_check;
};


// Starts tuning experiments. What users enter is remembered per project.
const NewTuningForm = ({ project, git, config, metrics, commit, available_tests_files, repo_file_url, web_url }) => {
  const logged_user = useUser();
  const { docs_root, support_url, email_notifications } = useSiteConfig();
  const form = useTuningForm(project);
  const updateTuning = usePrefsStore(state => state.updateTuning);
  const refresh = useRefreshCommit();
  const [submitted, setSubmitted] = useState(false);
  // Required fields only turn red once users left them empty, not on a pristine form
  const [touched, setTouched] = useState({});
  const touch = name => () => setTouched(touched => touched[name] ? touched : { ...touched, [name]: true });
  const is_missing = (name, value) => !!touched[name] && !value;
  const is_transforming = useRef(false);

  const default_parameter_search_auto = useMemo(() => optimize_template(metrics), [metrics]);
  const default_user = (config.runners || config).lsf?.user;
  const {
    experiment_name = "",
    platform = "linux",
    overwrite = true,
    selected_group = "",
    search_type = "grid",
    parameter_search = templates["no tuning"],
    search_options = default_search_options,
    parameter_search_auto = default_parameter_search_auto,
    optimize_mode = 'guided',
    values_mode = 'table',
    // the batches sent with the request, read after the batch files: changed for this run, or as an earlier batch ran
    // them ("Run again" from its logs, see logs/reproduce.js)
    batches_snapshot = null,
  } = form;
  const batches_files = batches_snapshot?.files ?? null;
  const user = logged_user.user_name || form.user || default_user;

  // the values to try: one model, that the table and the text show (model.js)
  const model = useMemo(() => model_from_text(parameter_search), [parameter_search]);
  const all_combinations = useMemo(() => count_combinations(model), [model]);
  const language = model.reason === 'function' ? 'javascript' : 'yaml'; // Monaco has no JSON mode out of the box, YAML reads it
  const shows_table = values_mode !== 'text' && !!model.sets;
  const known_names = useMemo(() => known_parameters(commit), [commit]);
  const is_logged = logged_user.is_logged;
  // the batches to run, as people would type them: "ci, night-*"
  const selection = useMemo(() => parse_selection(selected_group), [selected_group]);
  const { group_info: selected_group_info, loading: selected_group_info_loading, error } = useGroupInfo({
    project, selection, commit_id: commit?.id, available_tests_files, is_logged, batches_files,
  });
  const changed = useMemo(() => changed_batches(batches_files), [batches_files]);
  const batches_list_files = useMemo(() => Object.values(available_tests_files ?? {}).filter(Boolean), [available_tests_files]);
  const { data: batches_list } = useBatches({ project, commit_id: commit?.id, files: batches_list_files, enabled: is_logged });
  const listed_batches = useMemo(() => Array.isArray(batches_list?.batches) ? batches_list.batches : [], [batches_list]);
  const [editing_batches, setEditingBatches] = useState(false);

  // The batch's settings, as sent to the server
  const request = useMemo(() => {
    // invalid text: the form says so, and doesn't start
    const parameters = search_type === 'optimize' ? parameter_search_auto : (model_to_parameters(model) ?? undefined);
    return {
      project,
      batch_label: experiment_name,
      platform,
      configuration: 'xxxxxxxxx',
      tuning_search: {
        search_type,
        search_options: search_type !== 'grid' ? search_options : {},
        parameter_search: parameters,
      },
      // one name, or several batches that run together
      selected_group: selection.length > 1 ? selection : (selection[0] ?? ''),
      // a function: the server remembers it as written, to start from this batch again
      parameter_search_text: search_type !== 'optimize' && model.reason === 'function' ? parameter_search : undefined,
      groups: Object.values(available_tests_files ?? {}),
      ...(batches_files ? { batches_files } : {}),
      user,
      overwrite,
      // an email when the last run ends (everyone gets them for now, not for automated tuning yet)
      notify: email_notifications && search_type !== 'optimize' ? 'email' : undefined,
    };
  }, [project, experiment_name, platform, search_type, search_options, parameter_search, parameter_search_auto, model, selection, available_tests_files, batches_files, user, overwrite, email_notifications]);
  const check = useTuningCheck({ project, commit_id: commit?.id, request, is_logged });
  // the automated tuning's configuration, checked as users type. The server checks it too, with the same messages.
  const optimize_errors = useMemo(() => {
    if (search_type !== 'optimize') return [];
    const { config: optimize_config, errors } = parse_optimize(parameter_search_auto);
    if (errors.length) return errors;
    const continuing = continuation(commit, experiment_name, optimize_config);
    return [...check_optimize(optimize_config, metrics).map(e => e.message), ...(continuing?.error ? [continuing.error] : [])];
  }, [search_type, parameter_search_auto, metrics, commit, experiment_name]);
  // the answer to the last start, until users change something
  const [submit_check, setSubmitCheck] = useState(null);
  const current_check = submit_check?.request === request ? submit_check : check;
  // the inputs already say when the name or the batch are missing
  // the setup already shows the configuration's errors
  const check_errors = (experiment_name && selection.length > 0) ? current_check.errors.filter(e => !optimize_errors.includes(e)) : [];
  const check_warnings = current_check.warnings;

  const update = name => e => updateTuning(project, { [name]: e?.target ? e.target.value : e });
  const updateSelection = text => updateTuning(project, { selected_group: text });
  const clean_label = text => text.replace(/[^\w_.@:=]/g, "-");
  const updateExperimentName = e => updateTuning(project, { experiment_name: clean_label(e.target.value) });
  const updateParameterSearch = new_parameter_search => updateTuning(project, { parameter_search: new_parameter_search });
  const updateParameterSearchAuto = new_parameter_search => updateTuning(project, { parameter_search_auto: new_parameter_search });
  const updateMode = mode => updateTuning(project, { search_type: mode === 'manual' ? 'grid' : 'optimize' });
  const updateIterations = e => updateTuning(project, { search_options: { n_iter: parseFloat(e.target.value) } });

  // "Start from a previous batch": the form like it was then. The toast undoes it.
  const startFrom = entry => {
    const { form: next, missing } = form_from_request(entry.request, entry.request?.batch_label || entry.batch_label);
    const previous = Object.fromEntries(Object.keys(next).map(key => [key, form[key]]));
    updateTuning(project, next);
    track('tuning_start_from', { kind: next.search_type === 'optimize' ? 'automated_tuning' : 'tuning', same_commit: entry.commit_id === commit?.id }, { project_id: project });
    toaster.show({
      message: `Filled like ${entry.batch_label}${missing ? `, except ${missing}` : ''}. Change what you want, then start.`,
      icon: 'history',
      intent: missing ? Intent.WARNING : Intent.NONE,
      timeout: 8000,
      action: { text: 'Undo', onClick: () => updateTuning(project, previous) },
    }, 'tuning-start-from');
  };

  // Users paste register values in the CDE format, we convert them to QA-Board's format
  const onEditorDidMount = editor => {
    editor.onDidChangeModelContent(e => {
      // Avoid infinite loops from our own transformations
      if (is_transforming.current) return;
      const change = e.changes[0];
      if (!change) return;
      const addedText = change.text;
      // Check if the added text looks like CDE register format and is significant enough to be a paste
      if (addedText.length <= 5 || !addedText.includes('=')) return;
      const transformedText = transformCDERegisters(addedText);
      if (transformedText === addedText) return;
      is_transforming.current = true;
      // Replace the pasted content with the transformed version
      editor.executeEdits('paste-transform', [{
        range: calculateActualRange(change.range, addedText),
        text: transformedText,
      }]);
      setTimeout(() => { is_transforming.current = false; }, 100);
    });
  };

  const onSubmit = () => {
    setSubmitted(true);
    // the same toast says when the experiment started, or why it didn't
    const toast_key = `tuning-${experiment_name}`
    toaster.show({ message: `Starting ${experiment_name}…`, icon: 'time' }, toast_key);
    http.post(`/api/v1/commit/${commit.id}/batch`, request, { params: { project } })
      .then(({ data }) => {
        setSubmitted(false);
        // usage analytics: what people start from QA-Board
        const kind = search_type === 'optimize' ? 'automated_tuning' : typeof combinations === 'number' && combinations > 1 ? 'tuning' : 'run_tests';
        track('batch_start', { kind, commit_id: commit.id, n_runs: typeof data?.submission?.runs === 'number' ? data.submission.runs : undefined }, { project_id: project });
        // e.g. only the source files of the artifacts could be restored
        if (data?.warning) toaster.show({message: data.warning, intent: Intent.WARNING, timeout: 15000});
        if (data?.notification?.error) toaster.show({message: `No email when it ends: ${data.notification.error}`, intent: Intent.WARNING, timeout: 15000});
        toaster.show({
          message: data?.notification?.recipient
            ? `Started ${experiment_name}. QA-Board will email ${data.notification.recipient} when it ends.`
            : `Started ${experiment_name}`,
          intent: Intent.SUCCESS,
          action: { text: 'Show its logs', onClick: () => updateSelected({ selected_batch_new: experiment_name, selected_views: 'logs' }) },
        }, toast_key);
        setTimeout(() => refresh(project, commit.id),  1*1000)
        setTimeout(() => refresh(project, commit.id),  5*1000)
        setTimeout(() => refresh(project, commit.id), 10*1000)
      })
      .catch(error => {
        setSubmitted(false);
        // The batch's page tells users why, and shows the logs
        const submission = error.response?.data?.submission
        const data = error.response?.data
        if (Array.isArray(data?.errors)) setSubmitCheck({ request, errors: data.errors, warnings: data.warnings ?? [] });
        toaster.show({
          message: submission
            ? `${experiment_name} failed to start. Its logs say why.`
            // 409: the artifacts are missing, maybe being recreated by the CI
            : isArtifactsError(error) ? errorMessage(error) : `Could not start ${experiment_name}: ${errorMessage(error)}`,
          intent: isArtifactsError(error) ? Intent.WARNING : Intent.DANGER,
          timeout: 10000,
          action: submission ? { text: 'Show its logs', onClick: () => updateSelected({ selected_batch_new: experiment_name, selected_views: 'logs' }) } : undefined,
        }, toast_key);
        refresh(project, commit.id);
      });
  };

  const { tests } = selected_group_info;
  // the server can't list the tests without running the project's code (its own iter_inputs): `qa batch` will
  const unlisted = !!selected_group_info.unlisted;
  const { combinations, runs: total_runs } = count_runs({ combinations: all_combinations, tests: tests.length, search_type, n_iter: search_options?.n_iter });
  const invalid = combinations === null;


  // we should show different parameters for different runners: queues, user...
  const lsf_runner = (config.runners?.lsf !== undefined || config.lsf !== undefined);
  const any_runner_configured = !!config.lsf || Object.keys(config.runners || {}).filter(t => t !== 'local' && t !== 'default').length > 0;

  // A big grid: an automated tuning would search the same ranges with fewer runs
  const grid_space = useMemo(() => {
    if (search_type === 'optimize' || typeof combinations !== 'number' || combinations < 12) return null;
    const space = grid_to_space(model_to_parameters(model));
    if (!space) return null;
    const evaluations = recommended_evaluations(space.rows);
    return evaluations < combinations ? { ...space, evaluations } : null;
  }, [search_type, combinations, model]);
  const searchGridAutomatically = () => updateTuning(project, {
    search_type: 'optimize',
    optimize_mode: 'guided',
    // the metric the form would have started with
    // optional chaining: the React Compiler reads these in render to memoize the function, also when there is no grid
    parameter_search_auto: from_space(grid_space?.rows, parse_optimize(default_parameter_search_auto).config?.objective ?? {}, grid_space?.preset_params),
  });

  // The batch with this label on the commit, if any: starting adds runs to it
  const existing_batch = experiment_name ? commit?.batches?.[experiment_name] : undefined;
  const existing_runs = existing_batch ? batch_runs(existing_batch) : 0;
  const suggested_label = useMemo(
    () => unused_label(suggest_label({ selection, sets: model.sets, search_type }), commit?.batches),
    [selection, model.sets, search_type, commit],
  );

  const panel_manual = <>
    <div style={{ display: 'flex', flexWrap: 'wrap', gap: 10, alignItems: 'center', marginBottom: 12 }}>
      <Tooltip content={model.sets ? undefined : "The table can't show this text: it's a function, or not valid JSON yet"} disabled={!!model.sets}>
        <SegmentedControl
          aria-label="How to edit the values"
          options={[{ label: 'Table', value: 'table', disabled: !model.sets }, { label: 'Text', value: 'text' }]}
          value={shows_table ? 'table' : 'text'}
          onValueChange={update('values_mode')}
        />
      </Tooltip>
      <HTMLSelect
        id="select-search-type"
        aria-label="Which combinations to run"
        value={search_type}
        onChange={update('search_type')}
      >
        <option key="grid" value="grid">Run every combination</option>
        <option key="sampler" value="sampler">Run a random sample</option>
      </HTMLSelect>
      {(search_type === "sampler") && <>
        <span className={Classes.TEXT_MUTED}>of</span>
        <input
          id="input-iterations"
          value={search_options.n_iter}
          className={Classes.INPUT}
          style={{ width: "80px" }}
          placeholder="50"
          aria-label="Number of samples"
          onChange={updateIterations}
          type="number"
          min={1}
          dir="auto"
        />
        <span className={Classes.TEXT_MUTED}>combinations</span>
      </>}
      {search_type === 'sampler' && !invalid && combinations < all_combinations && <span className={Classes.TEXT_MUTED} data-testid="tuning-combinations">
        of {all_combinations}
      </span>}
    </div>
    {shows_table
      ? <ParametersTable value={parameter_search || ''} onChange={updateParameterSearch} known={known_names}/>
      : <>
        {values_mode !== 'text' && <Callout icon="code" style={{ marginBottom: 10 }} data-testid="tuning-text-only">
          {model.reason === 'function'
            ? <>This is a function: the table shows lists of values. Edit it here, or <Button size="small" variant="minimal" intent={Intent.PRIMARY} text="start over with the table" onClick={() => updateParameterSearch('')}/></>
            : <>The table needs valid JSON: fix it below, or <Button size="small" variant="minimal" intent={Intent.PRIMARY} text="start over with the table" onClick={() => updateParameterSearch('')}/></>}
        </Callout>}
        <MonacoEditor
          height={250}
          language={language || 'json'}
          value={parameter_search || ''}
          options={editor_options}
          name="editor-tuning-set"
          onChange={updateParameterSearch}
          editorDidMount={onEditorDidMount}
        />
        <div style={{ display: 'flex', flexWrap: 'wrap', gap: 4, alignItems: 'center', margin: '8px 0' }}>
          <span className={Classes.TEXT_MUTED} style={{ marginRight: 4 }}>Examples:</span>
          {examples.map(([key, label]) => <Button key={key} size="small" variant="outlined" onClick={() => updateParameterSearch(templates[key])}>{label}</Button>)}
        </div>
      </>}
    {grid_space && <Callout icon="predictive-analysis" style={{ marginTop: 10 }} data-testid="tuning-suggest-auto">
      <strong>{combinations} combinations{tests.length > 0 && <>, {total_runs} runs</>}.</strong> An automated tuning could search the same ranges with
      about {grid_space.evaluations} evaluations{tests.length > 0 && <> ({grid_space.evaluations * tests.length} runs)</>}, and try the values in between: it needs a metric to improve. <Learn topic="search"/>
      <div style={{ marginTop: 6 }}>
        <Button size="small" intent={Intent.PRIMARY} icon="predictive-analysis" text="Search these ranges automatically" onClick={searchGridAutomatically}/>
      </div>
    </Callout>}
  </>


  const panel_auto = <OptimizeSetup
    value={parameter_search_auto || ''}
    onChange={updateParameterSearchAuto}
    metrics={metrics}
    tests={tests.length}
    commit={commit}
    experiment_name={experiment_name}
    mode={optimize_mode}
    onModeChange={mode => updateTuning(project, { optimize_mode: mode })}
    support_url={support_url}
  />

  const available_platforms = config.inputs?.platforms ?? []
  // qaboard.yaml's tuning.branches: the branches whose commits have artifacts, if the CI doesn't save them for all
  const tuning_branches = config.tuning?.branches
  const subproject = git?.path_with_namespace ? project.slice(git.path_with_namespace.length + 1) : ''
  const cannot_tune_on_branch = !!commit?.branch && Array.isArray(tuning_branches) && !branch_matches(commit.branch, tuning_branches, subproject)

  // the one reason Start is disabled, said next to it
  const blocker = !user ? "Sign in first"
    : selection.length === 0 ? "Choose the tests to run, in step 1"
    : (tests.length === 0 && !unlisted) ? "The batch has no tests: step 1 says why"
    : search_type === "optimize" ? (optimize_errors.length > 0 ? "Fix the automated tuning's configuration first: step 2 says what's missing" : undefined)
    : invalid ? "The values to try are not valid JSON, or the function fails: switch to Text to see why"
    : total_runs > MAX_RUNS ? `Too many runs: ${total_runs} (at most ${MAX_RUNS}). Run a random sample, or fewer tests.`
    : undefined;
  const label_blocker = experiment_name.length === 0 ? 'Give the results a label, in step 3' : undefined;
  const hint = blocker ?? label_blocker ?? (check_errors.length > 0 ? 'Fix what step 3 lists first' : undefined) ?? (search_type !== 'optimize' && total_runs >= 1000 ? "Over 1000 runs: check the cluster has room" : undefined);
  const hint_intent = (invalid || total_runs > MAX_RUNS) && search_type !== "optimize" ? Intent.DANGER : undefined;
  const disabled = submitted ||
    !is_logged ||
    check_errors.length > 0 ||
    !user ||
    experiment_name.length === 0 ||
    (!total_runs && search_type !== "optimize" && !unlisted) ||
    (invalid && search_type !== "optimize") ||
    (search_type === "optimize" && optimize_errors.length > 0) ||
    total_runs > MAX_RUNS;
  // the steps' ticks
  const tests_done = selection.length > 0 && (tests.length > 0 || unlisted);
  const parameters_done = search_type === 'optimize' ? optimize_errors.length === 0 : !invalid;
  const chosen = useMemo(() => expand_selection(selection, listed_batches, batches_files), [selection, listed_batches, batches_files]);
  // what to know before starting: where runs execute, and what the server's check found
  const before_start = [
    ...(!any_runner_configured ? [<>Runs start on the QA-Board server: this project has no runner for a cluster or task queue, so runs share the server's CPUs and stop if it restarts. <a href={`${docs_root}docs/runners`}>Choose where runs execute</a>, and see <a href={`${docs_root}docs/backend-admin/runners`}>what the server needs</a>.</>] : []),
    ...check_warnings,
  ];
  const selected_mode = modes.find(mode => (search_type === 'optimize') === (mode.id === 'optimize'));
  const moveMode = e => {
    if (!['ArrowLeft', 'ArrowRight', 'ArrowUp', 'ArrowDown'].includes(e.key)) return;
    e.preventDefault();
    const next = selected_mode.id === 'optimize' ? 'manual' : 'optimize';
    updateMode(next);
    e.currentTarget.parentElement.querySelector(`[data-testid="tuning-mode-${next}"]`)?.focus();
  };

  return <Sections>
    <div className="qa-tuning-top">
      <span className="qa-tuning-top-text">Run this commit's code on the tests you choose, as it is or with other parameters.</span>
      <StartFrom project={project} commit_id={commit?.id} onPick={startFrom} disabled={!is_logged}/>
    </div>
    {!is_logged && <Callout intent={Intent.PRIMARY} icon="log-in" title="Sign in to start runs" style={{ marginBottom: 18 }} data-testid="tuning-login">
      <p>Runs started from QA-Board run as you. Once you are signed in, QA-Board also lists the batches and their tests, and checks your settings.</p>
      <Button icon="log-in" intent={Intent.PRIMARY} text="Sign in" onClick={requestLogin}/>
    </Callout>}
    {cannot_tune_on_branch && <Callout intent={Intent.WARNING} title="Tuning may not work" icon="warning-sign" style={{ marginBottom: 18 }}>
      <p>Tuning needs the commit's artifacts, and the CI only saves them on some branches: {tuning_branches.map((pattern, idx) => <span key={idx}>{idx > 0 && ', '}<code>{expand_subproject(pattern, subproject)}</code></span>)}. Your branch, <code>{commit.branch}</code>, doesn't match.</p>
      <p>A workaround is saving them yourself, from your clone of the repository:</p>
      <pre>
        <div>git checkout {(commit?.id ?? '').slice(0, 8)}</div>
        {subproject && <div>cd {subproject}</div>}
        <div>qa save-artifacts</div>
      </pre>
    </Callout>}

    <section className="qa-step" aria-labelledby="tuning-section-tests">
      <div className="qa-step-head">
        <StepTitle id="tuning-section-tests" number={1} done={tests_done}>Choose the tests</StepTitle>
      </div>
      {!!batches_snapshot && <BatchesForThisRunCallout
        snapshot={batches_snapshot}
        chosen={chosen}
        selection={selection}
        onEdit={() => setEditingBatches(true)}
        onDrop={() => updateTuning(project, { batches_snapshot: null })}
      />}
      <BatchPicker
        project={project}
        commit={commit}
        value={selected_group}
        onChange={updateSelection}
        tests={{ group_info: selected_group_info, loading: selected_group_info_loading, error }}
        available_tests_files={available_tests_files}
        repo_file_url={repo_file_url}
        is_logged={is_logged}
        editor_props={{ git, web_url, config, docs_root }}
        changed={changed.filter(name => chosen.includes(name) || selection.includes(name))}
        onChangeForThisRun={() => setEditingBatches(true)}
      />
      <BatchesForThisRunDialog
        isOpen={editing_batches}
        onClose={() => setEditingBatches(false)}
        onApply={content => {
          updateTuning(project, { batches_snapshot: snapshot_for_this_run(batches_snapshot, content, selection) });
          setEditingBatches(false);
        }}
        project={project}
        commit_id={commit?.id}
        files={batches_list_files}
        selection={selection}
        batches={listed_batches}
        snapshot={batches_snapshot}
      />
      {available_platforms.length > 0 &&
      <RadioGroup onChange={update('platform')} selectedValue={platform} inline label="Platform">
        {available_platforms.map(p => <Radio
          key={p.name}
          labelElement={<span>{p.label || p.name || 'undefined name/label!'}</span>}
          value={p.name}
        />)}
      </RadioGroup>}
    </section>

    <section className="qa-step" aria-labelledby="tuning-mode-label">
      <div className="qa-step-head">
        <StepTitle id="tuning-mode-label" number={2} done={parameters_done && tests_done}>Set the parameters</StepTitle>
        <Modes role="radiogroup" aria-labelledby="tuning-mode-label">
          {modes.map(mode => {
            const checked = mode === selected_mode;
            return <button type="button" key={mode.id} className="qa-tuning-mode" role="radio" aria-checked={checked} tabIndex={checked ? 0 : -1} data-testid={`tuning-mode-${mode.id}`}
              onClick={() => updateMode(mode.id)}
              onKeyDown={moveMode}
            >
              <Icon icon={mode.icon} size={14}/>{mode.title}
            </button>;
          })}
        </Modes>
        <a className="qa-step-side" href={`${docs_root}docs/${search_type !== "optimize" ? 'tuning-from-the-webapp' : 'user-guide/automated-tuning'}`} target="_blank" rel="noopener noreferrer">
          <Icon icon="help" size={12}/> {search_type !== "optimize" ? 'How to tune' : 'How automated tuning works'}
        </a>
      </div>
      <p className="qa-step-lead">{selected_mode.text}</p>
      {search_type !== 'optimize' ? panel_manual : panel_auto}
    </section>

    <section className="qa-step" aria-labelledby="tuning-section-start">
      <div className="qa-step-head">
        <StepTitle id="tuning-section-start" number={3} done={!!experiment_name}>Name the results</StepTitle>
      </div>
      <FormGroup
        helperText={is_missing('experiment_name', experiment_name) ? "Give the batch a label" : "The batch's name on this commit, to find and compare its results."}
        label="Batch label"
        labelFor="batch-label"
        intent={is_missing('experiment_name', experiment_name) ? Intent.DANGER : undefined}
      >
        <div style={{ display: 'flex', flexWrap: 'wrap', gap: 8, alignItems: 'center' }}>
          <InputGroup
            id="batch-label"
            style={{ width: "min(360px, 80vw)" }}
            placeholder="my-tuning-experiment"
            value={experiment_name}
            onChange={updateExperimentName}
            onBlur={touch('experiment_name')}
            intent={is_missing('experiment_name', experiment_name) ? Intent.DANGER : undefined}
            type="text"
            dir="auto"
          />
          {!experiment_name && suggested_label && <Button variant="minimal" intent={Intent.PRIMARY} onClick={() => updateTuning(project, { experiment_name: suggested_label })} data-testid="tuning-suggest-label">
            Use <code>{suggested_label}</code>
          </Button>}
        </div>
      </FormGroup>
      {existing_batch && search_type !== 'optimize' && <Callout intent={overwrite && existing_runs > 0 ? Intent.WARNING : Intent.PRIMARY} icon="layers" style={{ margin: '-4px 0 14px', maxWidth: 760 }} data-testid="tuning-existing-batch">
        <strong>{experiment_name}</strong> already exists on this commit{existing_runs > 0 && <>, with {existing_runs} run{existing_runs === 1 ? '' : 's'}</>}: the new runs are added to it.{' '}
        {overwrite
          ? <>Runs it already has <strong>run again</strong>, and replace their results.</>
          : <>Runs it already has are kept: only the missing ones start.</>}
        <div style={{ marginTop: 6, display: 'flex', flexWrap: 'wrap', gap: 6 }}>
          <Button size="small" text={<>Use <code>{unused_label(experiment_name, commit?.batches)}</code> instead</>} onClick={() => updateTuning(project, { experiment_name: unused_label(experiment_name, commit?.batches) })}/>
          {existing_runs > 0 && (overwrite
            ? <Button size="small" variant="minimal" text="Keep its runs, only start what's missing" onClick={() => updateTuning(project, { overwrite: false })}/>
            : <Button size="small" variant="minimal" text="Run them again too" onClick={() => updateTuning(project, { overwrite: true })} data-testid="tuning-overwrite"/>)}
        </div>
      </Callout>}
      {check_errors.length > 0 && <Callout intent={Intent.DANGER} title="Fix this to start" icon="error" compact style={{ maxWidth: 900, marginBottom: 10 }} data-testid="tuning-check-errors">
        <ul className={Classes.LIST} style={{ margin: 0 }}>{check_errors.map(e => <li key={e}>{e}</li>)}</ul>
      </Callout>}
      {before_start.length > 0 && <Callout intent={Intent.WARNING} title="Before you start" icon="warning-sign" compact style={{ maxWidth: 900 }} data-testid="tuning-check-warnings">
        <ul className={Classes.LIST} style={{ margin: 0 }}>{before_start.map((w, idx) => <li key={idx}>{w}</li>)}</ul>
      </Callout>}
    </section>

    <div className="qa-tuning-bar" data-testid="tuning-bar">
      <div className="qa-tuning-total" data-testid="tuning-total" aria-live="polite">
        {search_type === 'optimize'
          ? <><strong>Automated tuning</strong>{tests.length > 0 && <span className={Classes.TEXT_MUTED}> on {tests.length} test{tests.length === 1 ? '' : 's'}</span>}</>
          : invalid
            ? <strong className="qa-text-danger">The values to try are not valid</strong>
            : unlisted
              ? <><strong>{combinations} combination{combinations === 1 ? '' : 's'}</strong> <span className={Classes.TEXT_MUTED}>on each test: QA-Board can't count the tests here</span></>
              : <>
                <strong>{total_runs} run{total_runs === 1 ? '' : 's'}</strong>{' '}{total_runs > MAX_RUNS && '(' + MAX_RUNS + ' max.) '}
                <span className={Classes.TEXT_MUTED}>
                  {tests.length === 0
                    ? '— no tests chosen yet'
                    : `= ${tests.length} test${tests.length === 1 ? '' : 's'} × ${combinations} combination${combinations === 1 ? '' : 's'}`}
                </span>
              </>}
      </div>
      <div className="qa-tuning-bar-actions">
        {lsf_runner && is_logged && <Tooltip content="Runs start on the cluster as this user, with their shell environment">
          <Tag icon="user" minimal>as <strong>{user}</strong></Tag>
        </Tooltip>}
        <Button
          onClick={onSubmit}
          data-testid="tuning-start"
          disabled={disabled}
          large
          icon="play"
          intent={search_type !== "optimize" ? (total_runs < 1000 ? Intent.PRIMARY : Intent.DANGER) : Intent.PRIMARY}
        >
          {search_type === "optimize" ? "Start automated tuning" : (!unlisted && Number.isFinite(total_runs) && total_runs > 0) ? `Start ${total_runs} run${total_runs === 1 ? '' : 's'}` : "Start runs"}
        </Button>
      </div>
      {(hint || (email_notifications && search_type !== "optimize")) && <div className={`qa-tuning-hint ${Classes.TEXT_MUTED}`}>
        {hint && <span style={hint_intent ? { color: 'var(--qa-text-danger)' } : undefined}>{hint}</span>}
        {hint && email_notifications && search_type !== "optimize" && ' · '}
        {email_notifications && search_type !== "optimize" && <span className={Classes.TEXT_MUTED} data-testid="tuning-email-note">QA-Board will email you when the last run ends.</span>}
      </div>}
    </div>
    <div className={`qa-tuning-footer ${Classes.TEXT_MUTED}`}>
      This form is new. <NewFormActions project={project}/>
    </div>
  </Sections>;
};



// The new form, or the previous one for those who chose it (FormVersion.jsx)
const TuningForm = props => {
  const version = useTuningFormVersion();
  if (version === 'previous') return <>
    <PreviousFormBanner project={props.project}/>
    <LegacyTuningForm {...props}/>
  </>;
  return <NewTuningForm {...props}/>;
};

export { TuningForm };
