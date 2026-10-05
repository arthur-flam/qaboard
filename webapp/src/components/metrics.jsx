import { memo, useMemo, useState } from "react";
import Plot from "./Plot";
import styled from "styled-components";

import {
  Classes,
  Tag,
  Button,
  Icon,
  Intent,
  Callout,
  MenuItem,
  Colors,
  CompoundTag,
  Tooltip,
} from "@blueprintjs/core";
import { MultiSelect } from "@blueprintjs/select";

import { noMetrics } from "./metricSelect";
import { RunBadge } from "./tags";
import { format, median, plotly_palette, match_query } from "../utils";



// todo: we should use the colors defined by @blueprint, and JS helpers to alpha-ize, darken, etc.
const color = "rgba(255, 157, 0, 1)";
const color_ref = "rgba(55, 126, 184, 1)";
const colors = [color, color_ref];

const color_a = "rgba(255, 157, 0, .4)";
const color_ref_a = "rgba(55, 126, 184, .4)";
const colors_a = [color_a, color_ref_a];

const metric_formatter = (value, metric) => {
  if (isNaN(value)) {
    return value
  }
  // 3 significant digits by default
  // https://mathjs.org/docs/reference/functions/format.html
  return format(value, {precision: metric?.precision ?? 3})
  // This doesn't support scientific notation.. but above there
  // are not thousant separators... ^^
  // return value.toLocaleString({
  //   minimumSignificantDigits: metric?.precision ?? 3,
  //   maximumSignificantDigits: metric?.precision ?? 3,
  // })
}
const percent_formatter = new Intl.NumberFormat("en-US", {
  style: "decimal",
  minimumFractionDigits: 0,
  maximumFractionDigits: 0
});

const MetricHeader = ({short_label, label, description, condensed=false, suffix, show_suffix=false}) => {
    return <Tooltip content={<span><strong>{label}</strong> {description}</span>}>
          <>
            {condensed ? short_label: label}
            {show_suffix && `${label}${!!suffix ? ` [${suffix}]` : ''}`}
          </>
    </Tooltip>
}

const MetricTag = ({ metrics_new, metrics_ref, metric_info }) => {
  if (metric_info.key === 'is_failed')
    return <span/>;
  const value = metrics_new[metric_info.key]
  const value_tooltip = <span>{!isNaN(value) ? `${metric_info.scale * value}${metric_info.suffix}` : JSON.stringify(value)}</span>
  const value_component = isNaN(value) ? <RunBadge badge={value}/> : <Tooltip content={value_tooltip}>
    <>{metric_formatter(metric_info.scale * value, metric_info)}{metric_info.suffix}</>
  </Tooltip>

  // compare tag
  let compare_tag = <span/>;
  const value_ref = metrics_ref?.[metric_info.key];
  if (value_ref && value_ref !== value) {
    const delta_relative = (value - value_ref) / value_ref;
    const neutral_threshold = metric_info.target_passfail ? 0 : 0.01
    let intent_compare;
    if (delta_relative > neutral_threshold)
      intent_compare = metric_info.smaller_is_better ? Intent.DANGER : Intent.SUCCESS;
    else if (delta_relative < -neutral_threshold)
      intent_compare = metric_info.smaller_is_better ? Intent.SUCCESS : Intent.DANGER;
    else intent_compare = Intent.DEFAULT;
    compare_tag = <Tag round minimal intent={intent_compare}>{delta_relative >= 0 ? '+' : ''}{percent_formatter.format(100 * delta_relative)}%</Tag>;
  }

  const intent =
    (value > metric_info.target && metric_info.smaller_is_better) ||
    (value < metric_info.target && !metric_info.smaller_is_better)
      ? Intent.DANGER
      : Intent.SUCCESS;
  const { key: _key, ...metric_info_rest } = metric_info;
  return <CompoundTag
    style={{margin: '3px', paddingTop: "0px", paddingBottom: "0px"}}
    minimal
    intent={metric_info.target ? intent : null}
    leftContent={<MetricHeader condensed {...metric_info_rest}/>}
  >
    {value_component}
    {compare_tag}
  </CompoundTag>;
};


const MetricsTags = memo(({ metrics_new, metrics_ref, available_metrics = {}, selected_metrics = [] }) =>
  selected_metrics
    .filter(key => metrics_new[key] !== undefined && available_metrics[key] !== undefined)
    .map(key =>
      <MetricTag key={key}
        metrics_new={metrics_new}
        metrics_ref={metrics_ref}
        metric_info={available_metrics[key]}
      />
    )
);




const MetricRow = styled.div`
  display: flex;
  align-items: center;
  &:first-child {
    margin-top: 10px;
  }
  // justify-content: space-between;
`;
const MetricTile = styled.div`
  flex: 0.1 0.1 auto;
  text-align: center;
  align-self: start;
  padding: 10px;
  min-width: 355px; // manuall adjusted with the largest title..
`;

const HistogramComparaison = ({ series, metric, xaxis_labels, layout, use_plotly_default_colors }) => {
  const xdata = xaxis_labels || ["New", "Reference"];
  const base_layout = {
    bargap: 0,
    bargroupgap: 0,
    barmode: "overlay",
    yaxis: {
      type: metric.plot_scale || "log",
      autorange: true,
      color: "rgba(0,0,0,0.8)",
      tickcolor: "rgba(0,0,0,0.8)",
      showgrid: false,
      zeroline: false,
      gridcolor: "rgb(255, 255, 255)",
      gridwidth: 1
    },
    xaxis: { color: "rgba(0,0,0,0.8)", fixedrange: true, title: "" },
    showlegend: false,
    margin: {
      // will eat into the drawing area
      l: 40,
      r: 30,
      b: 25,
      t: 0,
      pad: 0
    },
    width: 300,
    height: 100,
    autosize: false,
    plot_bgcolor: "rgba(0,0,0,0)",
    paper_bgcolor: "rgba(0,0,0,0)",
  };

  const all_values = series.flat().filter(
    x => x !== null && x !== undefined && !isNaN(x)
  );

  const shapes = [];
  if (metric.target) {
    const min_y = Math.min(...all_values) * metric.scale;
    const max_y = Math.max(...all_values) * metric.scale;
    const threshold = metric.target * metric.scale;
    const all_success = metric.smaller_is_better
      ? max_y <= threshold
      : min_y <= threshold;
    const all_failed = metric.smaller_is_better
      ? min_y >= threshold
      : max_y >= threshold;
    if (!all_success)
      shapes.push({
        type: "rect",
        layer: "below",
        xref: "paper",
        x0: 0,
        x1: 1,
        yref: "y",
        y0: metric.smaller_is_better ? max_y : threshold,
        y1: metric.smaller_is_better ? threshold : min_y,
        opacity: 0.2,
        fillcolor: Colors.RED5,
        line: {
          color: Colors.RED5
        }
      });
    if (!all_failed)
      shapes.push({
        type: "rect",
        layer: "below",
        xref: "paper",
        x0: 0,
        x1: 1,
        yref: "y",
        y0: metric.smaller_is_better ? min_y : threshold,
        y1: metric.smaller_is_better ? threshold : max_y,
        opacity: 0.15,
        fillcolor: Colors.GREEN2,
        line: {
          color: Colors.GREEN2
        }
      });
  }
  const layout_ = { ...base_layout, ...layout, shapes };

  const data = xdata.map((name, i) => ({
    type: "box",
    y: series[i]?.map(x => metric.scale * x),
    name,
    namelength: -1,
    boxpoints: "all",
    jitter: 0.5,
    whiskerwidth: 0.3,
    boxmean: true,
    fillcolor: use_plotly_default_colors ? undefined : colors_a[i],
    marker: {
      size: 8,
      color: use_plotly_default_colors ? undefined : colors_a[i],
    },
    line: {
      width: 2,
      color: use_plotly_default_colors ? undefined : colors[i],
    }
  }));
  return (
    <Plot
      data={data}
      layout={layout_}
      config={{ displayModeBar: false }}
      useResizeHandler
      fit
      style={{
        marginLeft: "auto",
        flex: "0 1 auto",
        position: "relative",
        display: "inline-block"
      }}
    />
  );
};

const pc_under_threshold = (array, threshold) => {
  if (threshold === null || threshold === undefined)
    return 0
  return array.filter(x => x <= threshold).length / array.length;
};
const pc_over_threshold = (array, threshold) => {
  if (threshold === null || threshold === undefined)
    return 0
  return array.filter(x => x >= threshold).length / array.length;
};

const disable_axe = {
  fixedrange: true,
  zeroline: false,
  showgrid: false,
  showline: false,
  showticklabel: false,
  ticks: "",
  autotick: true
};

const layout_tiles = {
  barmode: "stack",
  font: {
    color: "#fff"
  },
  yaxis: {
    ...disable_axe,
    title: "",
    color: "#fff",
    tickcolor: "#fff"
  },
  xaxis: {
    ...disable_axe,
    title: "",
    color: "#fff",
    tickcolor: "#fff"
  },
  showlegend: false,
  margin: {
    l: 0,
    r: 0,
    b: 0,
    t: 0,
    pad: 0
  },
  width: 150,
  height: 25,
  autosize: false,
  plot_bgcolor: "rgba(0,0,0,0)",
  paper_bgcolor: "rgba(0,0,0,0)"
};

const SuccessBar = ({ success_frac }) => (
  <Plot
    data={[
      {
        type: "bar",
        orientation: "h",
        name: "Success",
        x: [100 * success_frac],
        textposition: "auto",
        hoverinfo: "none",
        text:
          success_frac > 0.4
            ? `${percent_formatter.format(100 * success_frac)}% success`
            : "",
        opacity: 1,
        marker: {
          color: Colors.GREEN3
        }
      },
      {
        type: "bar",
        orientation: "h",
        name: "Failure",
        x: [100 * (1 - success_frac)],
        textposition: "auto",
        hoverinfo: "none",
        text:
          success_frac < 0.6
            ? `${percent_formatter.format(100 * (1 - success_frac))}% failed`
            : "",
        opacity: 0.8,
        marker: {
          color: Colors.RED5
        }
      }
    ]}
    layout={layout_tiles}
    config={{ displayModeBar: false }}
  />
);

const no_metrics_selected = [];

const tags_breakdown = outputs => {
  const tags = {};
  const outputs_by_tag = {};
  outputs.forEach(output => {
    (output.test_input_metadata?.tags ?? []).forEach(tag => {
      tags[tag] = (tags[tag] ?? 0) + 1;
      (outputs_by_tag[tag] ??= []).push(output);
    });
  });
  return { tags, outputs_by_tag };
};

const MetricsSummary = ({ new_batch, ref_batch, breakdown_by_tag, xaxis_labels: xaxis_labels_, metrics, selected_metrics: selected_metrics_ }) => {
  const { available_metrics = {}, summary_metrics = [] } = metrics ?? {};
  // By default we show the metrics we were given, or the project's summary metrics.
  // Users can change the selection, until the default changes.
  const default_keys = (selected_metrics_ ?? summary_metrics.filter(k => !!available_metrics[k]).map(k => available_metrics[k]))
    .map(m => m.key);
  const default_signature = default_keys.join('\n');
  const [user_selection, setUserSelection] = useState(null);
  const selected_keys = user_selection?.default_signature === default_signature ? user_selection.keys : default_keys;
  const selected_metrics = selected_keys.map(k => available_metrics[k]).filter(Boolean);
  const setSelectedKeys = keys => setUserSelection({ default_signature, keys });

  const outputs_new = useMemo(() => (new_batch?.filtered?.outputs ?? [])
    .map(id => new_batch.outputs[id])
    .filter(o => !o.is_pending && o.output_type !== "optim_iteration"),
  [new_batch]);
  // we only show ref outputs with a matching input
  // it's debatable, maybe we should show all, or filter also on tuning params...
  const outputs_ref = useMemo(() => {
    const inputs_new = new Set(outputs_new.map(o => o.test_input_path));
    return (ref_batch?.filtered?.outputs ?? [])
      .map(id => ref_batch.outputs[id])
      .filter(o => inputs_new.has(o.test_input_path) && !o.is_pending);
  }, [outputs_new, ref_batch]);
  const { tags, outputs_by_tag } = useMemo(
    () => breakdown_by_tag ? tags_breakdown(outputs_new) : { tags: {}, outputs_by_tag: {} },
    [breakdown_by_tag, outputs_new],
  );

  if (new_batch === null) return <span />;
  const xaxis_labels = xaxis_labels_ || ["New", "Reference"];

  const isMetricSelected = metric => selected_keys.includes(metric.key);
  const handleMetricSelect = metric => setSelectedKeys(
    isMetricSelected(metric) ? selected_keys.filter(k => k !== metric.key) : [...selected_keys, metric.key]
  );
  const shown_metrics = selected_metrics.filter(m => new_batch.used_metrics.has(m.key));
  const handleRemoveMetric = (_tag, index) => {
    const removed = shown_metrics[index];
    setSelectedKeys(selected_keys.filter(k => k !== removed?.key));
  };
  const clearButton =
    selected_metrics.length > 0 ? (
      <Button icon="cross" aria-label="Clear" minimal={true} onClick={() => setSelectedKeys(no_metrics_selected)} />
    ) : null;

  const renderMetric = (metric, { handleClick, modifiers }) => {
    if (!modifiers.matchesPredicate) {
      return null;
    }
    const { key, ...rest } = metric;
    return (
      <MenuItem
        active={modifiers.active}
        icon={isMetricSelected(metric) ? "tick" : "blank"}
        key={key}
        label={key}
        text={<MetricHeader {...rest} show_suffix/>}
        onClick={handleClick}
        shouldDismissPopover={false}
      />
    );
  };

  const batch_data = new_batch.data || {};
  return (
    <div>
      {(!batch_data.optimization && new_batch.sorted_extra_parameters.length > 0) && (
        <Callout intent={Intent.WARNING}>
          The aggregation below contains all runs, possibly with tuning <strong>parameters mixed together.</strong>
        </Callout>
      )}
      <MultiSelect
        items={Object.values(available_metrics).filter(m => new_batch.used_metrics.has(m.key))}
        itemPredicate={filterMetric}
        itemRenderer={renderMetric}
        onItemSelect={handleMetricSelect}
        tagRenderer={m => m.label}
        tagInputProps={{
          onRemove: handleRemoveMetric,
          rightElement: clearButton
        }}
        noResults={noMetrics}
        selectedItems={shown_metrics}
        popoverProps={Classes.MINIMAL}
      />
      <br/>
      {breakdown_by_tag &&
        Object.entries(tags).map(([tag, count], idx) =>
          <Tag style={{ margin: '5px', background: plotly_palette(idx) }} key={tag}>
            {count} @{tag}
          </Tag>
        )}
      {selected_metrics.map(m =>
        <MetricSummary
          key={m.key}
          metric={m}
          outputs_new={outputs_new}
          outputs_ref={outputs_ref}
          outputs_by_tag={breakdown_by_tag ? outputs_by_tag : undefined}
          xaxis_labels={xaxis_labels}
        />
      )}
    </div>
  );
};

const filterMetric = (query, metric) => match_query(query)(`${metric.key} ${metric.label} ${metric.short_label}`);

const delta_intent = (delta_relative, smaller_is_better) => {
  if (delta_relative > 0.01) return smaller_is_better ? Intent.DANGER : Intent.SUCCESS;
  if (delta_relative < -0.01) return smaller_is_better ? Intent.SUCCESS : Intent.DANGER;
  return Intent.DEFAULT;
};

const breakdown_layout = {
  width: 850,
  xaxis: { fixedrange: true, title: "", tickfont: { size: 8 }},
};

const MetricSummary = ({ metric: m, outputs_new, outputs_ref, outputs_by_tag, xaxis_labels }) => {
  const breakdown_by_tag = outputs_by_tag !== undefined;
  const new_values = outputs_new
    .map(o => o.metrics[m.key])
    .filter(x => x !== undefined && typeof x !== 'string')
    .map(o => 1 * o);
  if (new_values.length === 0) return null;
  const ref_values = outputs_ref.map(o => o.metrics[m.key]).filter(v => v !== undefined && v !== null && typeof v !== 'string');
  const new_med = median(new_values);
  const ref_med = median(ref_values);
  const new_pc_good = m.smaller_is_better
    ? pc_under_threshold(new_values, m.target)
    : pc_over_threshold(new_values, m.target);
  const ref_pc_good = m.smaller_is_better
    ? pc_under_threshold(ref_values, m.target)
    : pc_over_threshold(ref_values, m.target);
  const delta_relative = (new_med - ref_med) / ref_med;
  const intent = delta_intent(delta_relative, m.smaller_is_better);

  return (
    <MetricRow>
      <MetricTile>
        <Tooltip content={<span>{m.scale * new_med}{m.suffix}</span>}>
          <h3 className={Classes.HEADING}>
            {metric_formatter(m.scale * new_med, m)}{m.suffix}
            <span style={{ color: "#ccc" }}> median</span>
          </h3>
        </Tooltip>
        <br/>
        <Tooltip content={<span>{m.label}</span>}>
          <h5 className={Classes.HEADING}>{m.short_label}</h5>
        </Tooltip>
        <br/>
        {m.target !== undefined && <SuccessBar success_frac={new_pc_good} />}
      </MetricTile>

      {!breakdown_by_tag && (
        <>
          {ref_values.length > 0 && <MetricTile>
            <Tooltip content={<span>{m.scale * ref_med}{m.suffix}</span>}>
              <h3 className={Classes.HEADING} style={{ color: color_ref }}>
                <Icon style={{verticalAlign: 'middle'}} icon="swap-horizontal" color="#ccc" size={16}/> {metric_formatter(m.scale * ref_med, m)}{m.suffix}
              </h3>
            </Tooltip>
            <h5 className={Classes.HEADING}>
              <Tooltip content={<span>{100 * delta_relative}</span>}>
              <Tag intent={intent}>
                {delta_relative > 0 ? "+" : ""}
                {percent_formatter.format(100 * delta_relative)}%
              </Tag>
              </Tooltip>
            </h5>
            {(m.target !== undefined && !!ref_pc_good) && <SuccessBar success_frac={ref_pc_good} />}
          </MetricTile>}
          {new_values.length > 1 && <HistogramComparaison
            series={ref_values.length > 0 ? [new_values, ref_values] : [new_values]}
            metric={m}
            xaxis_labels={xaxis_labels}
          />}
        </>
      )}
      {breakdown_by_tag && (
        <HistogramComparaison
          series={Object.values(outputs_by_tag).map(outputs =>
            outputs
              .map(o => o.metrics[m.key])
              .filter(x => x !== undefined)
              .map(o => 1 * o)
          )}
          metric={m}
          xaxis_labels={Object.keys(outputs_by_tag)}
          use_plotly_default_colors
          layout={breakdown_layout}
        />
      )}
    </MetricRow>
  );
};

export {
  HistogramComparaison,
  MetricsSummary,
  MetricTag,
  MetricsTags,
  MetricHeader,
  metric_formatter,
  percent_formatter,
};
