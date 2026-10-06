import { useEffect, useMemo, useState } from "react";

import {
  Classes,
  Card,
  Spinner,
  NonIdealState,
  Tabs,
  TabsExpander,
  Tab,
  Button,
  MenuItem,
  HTMLSelect,
} from "@blueprintjs/core";
import { MultiSelect } from "@blueprintjs/select";

import CommitsEvolution from "./CommitsEvolution";
import { Container, Section } from "./components/layout";
import { noMetrics } from "./components/metricSelect";
import { MetricsSummary } from "./components/metrics";
import { TableCompare, TableKpi } from "./components/tables";
import { match_query } from "./utils";

import { useCommitsList, useComparison, updateSelected } from "./hooks";
import { empty_batch } from "./defaults";


const filterMetric = (query, metric) => match_query(query)(`${metric.key} ${metric.label} ${metric.short_label}`);


const Dashboard = () => {
  const { project, project_data, selected, new_commit, ref_commit, new_batch, ref_batch, selected_batch_new } = useComparison();
  const { commits, error, isPending, isFetching } = useCommitsList();

  useEffect(() => {
    let name = project.split('/').slice(-1)[0];
    document.title = `History - ${name}`;
  }, [project]);

  // On branches, the metrics configuration comes from the latest commit
  const metrics = commits[0]?.data?.qatools_metrics ?? project_data.data?.qatools_metrics ?? {};
  const { available_metrics = {}, main_metrics, dashboard_metrics, dashboard_evolution_metrics } = metrics;
  const evolution_metrics = dashboard_evolution_metrics || main_metrics || [];

  // null: the project's default metrics
  const [user_selected_metrics, setSelectedMetrics] = useState(null);
  const selected_metrics = user_selected_metrics ?? (dashboard_metrics || main_metrics || []).map(k => available_metrics[k]).filter(Boolean);
  const [selected_tab, setSelectedTab] = useState(undefined);

  const used_metrics = useMemo(
    () => Object.values(available_metrics).filter(m => new_batch.used_metrics.has(m.key)),
    [available_metrics, new_batch.used_metrics],
  );

  if (isPending || (isFetching && commits.length === 0))
    return (
      <Container style={{paddingTop: '50px'}}>
        <NonIdealState title={`Loading ${selected.branch ?? ''}`} icon={<Spinner />} />
      </Container>
    );
  if (!!error) return <Container style={{paddingTop: '50px'}}>
    <NonIdealState title="Error" icon='error' description={error.message}/>
  </Container>
  if (commits.length === 0) return <Container style={{paddingTop: '50px'}}>
    <NonIdealState title="No commits found" description="Try expanding the date range." icon='search' />
  </Container>

  const has_reference = Object.keys(ref_batch?.filtered?.outputs ?? {}).length > 0

  const isMetricSelected = metric => selected_metrics.includes(metric);
  const handleMetricSelect = metric => setSelectedMetrics(
    isMetricSelected(metric) ? selected_metrics.filter(m => m !== metric) : [...selected_metrics, metric]
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
        text={`${metric.label} [${metric.suffix}]`}
        onClick={handleClick}
        shouldDismissPopover={false}
      />
    );
  };
  const clearButton = selected_metrics.length > 0 ? <Button icon="cross" aria-label="Clear" minimal={true} onClick={() => setSelectedMetrics([])} /> : null;
  const metricTableSelect = (
    <MultiSelect
      items={used_metrics}
      itemPredicate={filterMetric}
      itemRenderer={renderMetric}
      onItemSelect={handleMetricSelect}
      tagRenderer={m => m.label}
      tagInputProps={{
        onRemove: (_tag, index) => setSelectedMetrics(selected_metrics.filter((_, i) => i !== index)),
        rightElement: clearButton
      }}
      noResults={noMetrics}
      selectedItems={selected_metrics}
      popoverProps={Classes.MINIMAL}
    />
  );
  const selected_metrics_keys = selected_metrics.map(m => m.key);

  const outputs_nb = new_batch.filtered.outputs.length
  const count = outputs_nb > 0 ? <p className={Classes.TEXT_MUTED}>{outputs_nb} output{outputs_nb > 1 ? 's' : ''}</p> : <span/>
  return (
    <Container style={{paddingTop: '50px'}}>
      <Section>
        {isFetching && <Spinner />}
        <Card elevation={1} style={{ breakInside: "avoid" }}>
          <h2 className={Classes.HEADING}>Performance over time</h2>
          <CommitsEvolution
            project={project}
            project_data={project_data}
            commits={commits}
            shown_batches={[selected_batch_new]}
            new_commit={new_commit}
            ref_commit={ref_commit}
            select_metrics={evolution_metrics}
            output_filter={selected.filter_batch_new}
            per_output_granularity
            default_breakdown_per_test={true}
            onSelect={commits => updateSelected(project, commits, { replace: true })}
            style={{ marginTop: "20px" }}
          />
        </Card>
      </Section>

      {has_reference && (
        <Section style={{ breakAfter: "always", breakInside: "avoid" }}>
          <Card elevation={0}>
            <h2 className={Classes.HEADING}>Metrics distribution</h2>
            {count}
            <MetricsSummary
              selected_metrics={selected_metrics}
              project={project}
              project_data={project_data}
              metrics={metrics}
              new_batch={new_batch}
              ref_batch={ref_batch}
            />
          </Card>
        </Section>
      )}

      {project === 'dvs/psp_swip' && <Section>
        <Card elevation={1}>
          <h2 className={Classes.HEADING}>Algorithmic bottlenecks</h2>
          {count}
          <MetricsSummary
            breakdown_by_tag
            selected_metrics={selected_metrics}
            project={project}
            project_data={project_data}
            metrics={metrics}
            new_batch={new_batch}
            ref_batch={empty_batch}
          />
        </Card>
      </Section>}

      <Section>
        <Card>
          <h2 className={Classes.HEADING}>Metrics per-test</h2>
          <Tabs
            renderActiveTabPanelOnly
            id="tabs-outputs"
            onChange={setSelectedTab}
            selectedTabId={selected_tab}
          >
            <Tab
              id="table-kpi"
              title="vs KPI"
              panel={
                <TableKpi
                  new_batch={new_batch}
                  ref_batch={ref_batch}
                  metrics={selected_metrics_keys}
                  available_metrics={available_metrics}
                  input={metricTableSelect}
                />
              }
            />
            {has_reference && (
              <Tab
                id="table-compare"
                title="vs reference"
                panel={
                  <TableCompare
                    new_batch={new_batch}
                    ref_batch={ref_batch}
                    metrics={selected_metrics_keys}
                    available_metrics={available_metrics}
                    input={metricTableSelect}
                  />
                }
              />
            )}
            <TabsExpander />
            <HTMLSelect
              value={selected.sort_by ?? metrics.default_metric ?? 'test_input_path'}
              onChange={e => updateSelected(project, { sort_by: e.target.value }, { replace: true })}
            >
              <option value="test_input_path">Sort by Name</option>
              <option value="id">Sort by ID</option>
              {used_metrics.map(m => (
                <option key={m.key} value={m.key}>
                  Sort by {m.label}
                </option>
              ))}
            </HTMLSelect>
            <HTMLSelect value={selected.sort_order} onChange={e => updateSelected(project, { sort_order: e.target.value }, { replace: true })}>
              <option value={-1}>descending</option>
              <option value={1}>ascending</option>
            </HTMLSelect>
          </Tabs>
        </Card>
      </Section>
    </Container>
  );
}

export default Dashboard;
