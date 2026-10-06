import qs from "qs";
import { history as router_history } from "../router";
import { getSyncPreferences } from "../utils/dynamicOptions";

const parse_query_controls = search => {
  const query = qs.parse(search.replace(/^\?/, ''));
  if (!query.controls) return {};
  try {
    return JSON.parse(query.controls);
  } catch {
    return {};
  }
}

// The visual controls of the outputs: defaults from the project's configuration, overridden by ?controls= in the URL
const controls_defaults = (qatools_config, search = window.location.search) => {
  const outputs = qatools_config?.outputs;
  const show = {};
  const from_config = {};
  for (const control of outputs?.controls ?? [])
    from_config[control.name] = control.default;
  for (const view of outputs?.visualizations ?? outputs?.detailed_views ?? []) {
    if (view.default_hidden)
      show[view.name] = false;
  }
  return {
    show,
    dynamic_options: {},
    dynamic_options_sync: getSyncPreferences(),
    ...from_config,
    ...parse_query_controls(search),
  };
}


// Saves the controls in the URL. Toggling visual controls replaces the history entry:
// users don't expect the back button to undo each toggle.
const updateQueryUrl = (history, controls) => {
  if (controls === undefined)
    return
  const query = qs.parse(window.location.search.substring(1));
  (history ?? router_history).replace({
    pathname: window.location.pathname,
    search: qs.stringify({
      ...query,
      controls: JSON.stringify(controls),
    })
  });
}

export { controls_defaults, updateQueryUrl }
