import { useEffect, useMemo, useState } from "react";
import { useRouter } from "./router";

import {
  Classes,
  Button,
  MenuItem,
  Card,
  NonIdealState,
} from "@blueprintjs/core";
import { MultiSelect } from "@blueprintjs/select";
import { noMetrics } from "./components/metricSelect";

import { Container, Section } from "./components/layout";
import { MetricsSummary, MetricHeader } from "./components/metrics";
import { CommitWarningMessages, BatchStatusMessages } from "./components/messages";

import { TableCompare, TableKpi } from "./components/tables";
import { BatchLogs } from "./components/BatchLogs";
import { CommitParameters } from "./components/Parameters";
import { OutputCardsList } from "./viewers/OutputCardsList";
import { useComparison, useCommitIdsInUrl, useSiteConfig, useUser, updateSelected } from "./hooks";
import { useDynamicOptions } from "./useDynamicOptions";


import { TuningForm } from "./components/tuning/forms";
import { AddRecordingsForm } from "./components/tuning/form_groups";
import TuningExploration from "./components/tuning/TuningExploration";
import { controls_defaults, updateQueryUrl } from "./viewers/controls";
import { ExportPlugin } from "./plugins/ExportPlugin";
import { match_query } from "./utils";
import { humanFileSize } from "./viewers/bit_accuracy/utils";
import { setSyncPreferences } from "./utils/dynamicOptions";

import PrivateContent from "./components/authentication/PrivateContent"
import FloatingControlsPanel from "./components/FloatingControlsPanel";



const visualization_stats = {
  total_visualizations: 0,
  disabled_visualizations: 0,
  missing_files_count: 0,
};

const filterMetric = (query, metric) => match_query(query)(`${metric.key} ${metric.label} ${metric.short_label}`);


// Controls of the output viewers (e.g. image diff on/off), which visualizations are shown,
// and the values of dynamic options. Saved in the URL.
function useViewerControls(config) {
  const { history } = useRouter();
  // we initialize optionnal controls with their defaults
  const [controls, setControls] = useState(() => controls_defaults(config));
  // When the configuration changes, we keep users' choices
  const [controls_outputs, setControlsOutputs] = useState(config?.outputs);
  if (controls_outputs !== config?.outputs) {
    setControlsOutputs(config?.outputs);
    const defaults = controls_defaults(config);
    setControls({
      ...defaults,
      show: { ...defaults.show, ...controls.show },
      dynamic_options: controls.dynamic_options || {},
      dynamic_options_sync: controls.dynamic_options_sync || {},
    });
  }
  const update = controls => {
    setControls(controls);
    updateQueryUrl(history, controls);
  };
  return [controls, update];
}


const CiCommitResults = () => {
  const comparison = useComparison();
  const {
    project, selected, project_data, git, config, metrics, selected_metrics: selected_metrics_keys,
    new_commit, ref_commit, new_batch, ref_batch, selected_views, selected_batch_new, selected_batch_ref,
  } = comparison;
  const { new_project, ref_project, new_commit_id, ref_commit_id, filter_batch_new, filter_batch_ref } = selected;
  const { available_metrics } = metrics;
  const { docs_root } = useSiteConfig();
  const user = useUser();
  useCommitIdsInUrl({ new_commit, ref_commit });

  useEffect(() => {
    const name = project.split('/').slice(-1)[0];
    if (!!new_commit_id)
      document.title = `${new_commit_id.slice(0, 4)} - ${name}`;
  }, [project, new_commit_id]);

  const [controls, setControls] = useViewerControls(config);
  // Registrations are reset when the commit or batch changes, or when the visualizations change
  const visualizations_key = JSON.stringify(config.outputs?.visualizations || []);
  const dynamic = useDynamicOptions({ config, reset_key: `${new_commit_id}|${selected_batch_new}|${visualizations_key}` });
  // Reset file tracking when filter changes (but keep registrations for performance)
  const { forgetFiles } = dynamic;
  useEffect(() => forgetFiles(), [filter_batch_new]); // oxlint-disable-line react/exhaustive-deps
  // New dynamic options start with their default value, synced across outputs
  const effective_controls = useMemo(() => {
    const dynamic_options = { ...controls.dynamic_options };
    const dynamic_options_sync = { ...controls.dynamic_options_sync };
    Object.entries(dynamic.dynamic_options).forEach(([name, option]) => {
      if (dynamic_options[name] === undefined) dynamic_options[name] = [option.defaultValue];
      if (dynamic_options_sync[name] === undefined) dynamic_options_sync[name] = true;
    });
    return { ...controls, dynamic_options, dynamic_options_sync };
  }, [controls, dynamic.dynamic_options]);

  const [expand_floating_panel, setExpandFloatingPanel] = useState(false);
  useEffect(() => {
    if (!expand_floating_panel) return;
    // a short pulse: the panel expands, then can be collapsed
    const timer = setTimeout(() => setExpandFloatingPanel(false), 100);
    return () => clearTimeout(timer);
  }, [expand_floating_panel]);

  const toggle = name => () => setControls({ ...effective_controls, [name]: !effective_controls[name] });
  // Three-state toggle: undefined -> true -> false -> true -> ...
  const toggle_show = name => () => setControls({
    ...effective_controls,
    show: { ...effective_controls.show, [name]: effective_controls.show?.[name] !== true },
  });
  const updateDynamicOption = (name, value) => setControls({
    ...effective_controls,
    dynamic_options: { ...effective_controls.dynamic_options, [name]: [value] },
  });
  const toggleDynamicOptionSync = name => {
    const sync = !effective_controls.dynamic_options_sync[name];
    const dynamic_options_sync = { ...effective_controls.dynamic_options_sync, [name]: sync };
    setSyncPreferences(dynamic_options_sync);
    setControls({ ...effective_controls, dynamic_options_sync });
    // If we're syncing (not unsyncing), expand the floating panel
    if (sync) setExpandFloatingPanel(true);
  };

  const update = attribute => e => {
    const value = (e?.target && e.target.value !== undefined) ? e.target.value : e;
    updateSelected(project, { [attribute]: value }, { replace: attribute.startsWith('filter') })
  }

  // The metrics shown in tables
  const selected_metrics = useMemo(
    () => selected_metrics_keys.map(m => available_metrics[m]).filter(Boolean),
    [selected_metrics_keys, available_metrics],
  );
  const setSelectedMetrics = metrics => updateSelected(project, { selected_metrics: metrics.map(m => m.key) });
  const isMetricSelected = metric => selected_metrics.some(m => m.key === metric.key);
  const handleMetricSelect = metric => setSelectedMetrics(
    isMetricSelected(metric) ? selected_metrics.filter(m => m.key !== metric.key) : [...selected_metrics, metric]
  );
  const renderMetric = (metric, { handleClick, modifiers }) => {
    if (!modifiers.matchesPredicate)
      return null;
    return (
      <MenuItem
        active={modifiers.active}
        icon={isMetricSelected(metric) ? "tick" : "blank"}
        key={metric.key}
        label={metric.key}
        text={<MetricHeader {...metric} show_suffix />}
        onClick={handleClick}
        shouldDismissPopover={false}
      />
    );
  };
  const shown_selected_metrics = selected_metrics.filter(m => new_batch.used_metrics.has(m.key));
  const metricTableSelect = (
    <MultiSelect
      items={Object.values(available_metrics).filter(m => new_batch.used_metrics.has(m.key))}
      itemPredicate={filterMetric}
      itemRenderer={renderMetric}
      onItemSelect={handleMetricSelect}
      tagRenderer={m => <MetricHeader {...m}/>}
      tagInputProps={{
        onRemove: (_tag, index) => setSelectedMetrics(shown_selected_metrics.filter((_, i) => i !== index)),
        rightElement: selected_metrics.length > 0 ? <Button icon="cross" aria-label="Clear" minimal={true} onClick={() => setSelectedMetrics([])} /> : null,
      }}
      noResults={noMetrics}
      selectedItems={shown_selected_metrics}
      popoverProps={Classes.MINIMAL}
    />
  );

  const warning_messages = <CommitWarningMessages project={new_project} commit={new_commit} />;
  const config_outputs = config.outputs || {};
  const controls_extra = config_outputs.controls || []
  // we allow both for some leeway with half updated projects
  const visualizations = [...(config_outputs.visualizations || []), ...(config_outputs.detailed_views || [])];
  const tuned_params = new_batch.sorted_extra_parameters.filter(p => new_batch.extra_parameters[p].size > 1)
  const has_tuning = tuned_params.length > 0
  const show_ref_navbar = !(selected_views.includes('logs') || selected_views.includes('tuning') || selected_views.includes('groups'))
  // TODO: migrate the availble-tests-files to DB
  const available_tests_files = { gr: "extra-batches", usr: user.user_name ?? null };
  const export_plugin = <ExportPlugin
    project={new_project}
    ref_project={ref_project}
    config={config}
    new_commit_id={new_commit?.id ?? new_commit_id}
    ref_commit_id={ref_commit?.id ?? ref_commit_id}
    selected_batch_new={selected_batch_new}
    selected_batch_ref={selected_batch_ref}
    filter_batch_new={filter_batch_new}
    filter_batch_ref={filter_batch_ref}
    batch_dir_url={new_batch.batch_dir_url}
  />;
  const output_cards_props = {
    project: new_project,
    config,
    metrics,
    new_commit,
    new_batch,
    ref_batch,
    controls: effective_controls,
    onRegisterOutputOptions: dynamic.register,
    onToggleDynamicOptionSync: toggleDynamicOptionSync,
  };
  const docs_link = <a target="_blank" rel="noopener noreferrer" href={`${docs_root}docs/visualizations`}>Read the docs</a>;
  return (
    <Container style={{paddingTop: show_ref_navbar ? '150px' : '75px'}}>

      {(!new_commit || !ref_commit) && show_ref_navbar && <Section>
        {warning_messages}
      </Section>}

      {(!!new_commit) && (
          <>
            <Section key="filters">
              {warning_messages}
              <BatchStatusMessages project={new_project} commit={new_commit} batch={new_batch} />
            </Section>

            {selected_views.includes('summary') && <Section>
              <Card elevation={2}>
                <h2 className={Classes.HEADING}>Summary</h2>
                <MetricsSummary
                  project={new_project}
                  project_data={project_data}
                  metrics={metrics}
                  available_metrics={available_metrics}
                  new_batch={new_batch}
                  ref_batch={ref_batch}
                />
              </Card>
             </Section>}

            {selected_views.includes('parameters') && <Section>
              <Card>
                <h2 className={Classes.HEADING}>Artifacts & Configurations</h2>
                <CommitParameters
                  project={new_project}
                  config={config}
                  new_commit={new_commit}
                  ref_commit={ref_commit}
                />
              </Card>
             </Section>}

            {selected_views.includes('groups') && <Section style={{width: "1000px"}}>
              <Card>
                <h2 className={Classes.HEADING}>Groups of tests</h2>
                <PrivateContent enabled={true}>
                  <AddRecordingsForm
                  project={project}
                  git={git}
                  commit={new_commit}
                  config={config}
                  available_tests_files={available_tests_files}
                  docs_root={docs_root}
                  />
                </PrivateContent>
              </Card>
             </Section>}

            {selected_views.includes('tuning') && (Object.keys(config.artifacts || {}).length === 0
              ? <NonIdealState
                  icon="heatmap"
                  title={<p>Tuning requires you to define build <strong>artifacts.</strong></p>}
                  description={<p>{docs_link} to learn how to declare visualizations.</p>}
                />
              : <Section>
                <h2 className={Classes.HEADING}>Tuning Experiments</h2>
                <Card>
                  <PrivateContent enabled={true}>
                    <TuningForm
                    project={project}
                    config={config}
                    metrics={metrics}
                    commit={new_commit}
                    available_tests_files={available_tests_files}
                    />
                  </PrivateContent>
                </Card>
            </Section>)}

            {selected_views.includes('table-compare') && <Section>
              <Card>
                  <h2 className={Classes.HEADING}>Improvement report</h2>
                  <TableCompare
                    new_batch={new_batch}
                    ref_batch={ref_batch}
                    metrics={selected_metrics_keys}
                    available_metrics={available_metrics}
                    input={metricTableSelect}
                  />
              </Card>
             </Section>}

            {selected_views.includes('table-kpi') && <Section>
              <Card>
                  <h2 className={Classes.HEADING}>Quality report</h2>
                  <TableKpi
                    new_batch={new_batch}
                    ref_batch={ref_batch}
                    metrics={selected_metrics_keys}
                    available_metrics={available_metrics}
                    input={metricTableSelect}
                  />
              </Card>
             </Section>}

            {/* as wide as the page, not as its widest log line or run configuration */}
            {selected_views.includes('logs') && <Section style={{ width: 'auto', minWidth: 0 }}>
                <h2 className={Classes.HEADING}>Logs</h2>
                <BatchLogs
                  project={new_project}
                  commit={new_commit}
                  batch={new_batch}
                  batch_label={new_batch.label}
                />
             </Section>}

            {selected_views.includes('output-list') && (visualizations.length === 0
               ? <NonIdealState
                   icon="heatmap"
                   title="Visualizations are not configured yet."
                   description={<p>{docs_link} to learn how to declare visualizations.</p>}
                 />
               : <Section>
                <h2 className={Classes.HEADING}>Visualizations</h2>
                {export_plugin}
                <OutputCardsList {...output_cards_props}/>
            </Section>)}

            {selected_views.includes('bit-accuracy') && <Section>
                <h2 className={Classes.HEADING}>Output Files</h2>
                <p className={Classes.TEXT_MUTED}>Total Storage: {humanFileSize(
                  new_batch.filtered.outputs
                  .map(id => new_batch.outputs[id]?.data?.storage ?? 0)
                  .reduce((running_total, storage) => running_total + storage, 0)
                , true)}</p>
                {export_plugin}
                <OutputCardsList type='bit_accuracy' {...output_cards_props}/>
             </Section>}

            {selected_views.includes('optimization') && <Section>
              <Card>
                <h2 className={Classes.HEADING}>Auto-Tuning Analysis</h2>
                <TuningExploration
                  project={new_project}
                  metrics={metrics}
                  available_metrics={available_metrics}
                  selected_metrics={selected_metrics_keys}
                  batch={new_batch}
                  input={metricTableSelect}
                  />
              </Card>
             </Section>}

          </>
        )}

      {(!!new_commit) && (
        <FloatingControlsPanel
          controls={effective_controls}
          visualizations={visualizations}
          controls_extra={controls_extra}
          selected_views={selected_views}
          selected_metrics={selected_metrics}
          new_batch={new_batch}
          available_metrics={available_metrics}
          metricTableSelect={metricTableSelect}
          sort_by={selected.sort_by}
          sort_order={selected.sort_order}
          onToggle={toggle}
          onToggleShow={toggle_show}
          onUpdate={update}
          has_tuning={has_tuning}
          tuned_params={tuned_params}
          dynamic_options={dynamic.dynamic_options}
          onUpdateDynamicOption={updateDynamicOption}
          onToggleDynamicOptionSync={toggleDynamicOptionSync}
          visualization_stats={visualization_stats}
          visualizations_with_files={dynamic.visualizations_with_files}
          expandPanel={expand_floating_panel}
          registration_info={{
            total_outputs: new_batch.filtered.outputs.length,
            registered_outputs: dynamic.registered_outputs,
            is_throttled: false,
            last_recompute_at: dynamic.registered_outputs,
          }}
          onForceReregisterAllOptions={dynamic.recompute}
        />
      )}
    </Container>
  );
}

export default CiCommitResults;
