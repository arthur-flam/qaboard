import { useState } from "react";

import { parse_query, updateSelected } from "../selection";
import { getSyncPreferences } from "../utils/dynamicOptions";

const parse_query_controls = search => {
  const query = parse_query(search);
  if (!query.controls) return {};
  try {
    return JSON.parse(query.controls);
  } catch {
    return {};
  }
}

// The visual controls of the outputs: defaults from the project's configuration, overriden by ?controls= in the URL
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
const save_controls = controls => updateSelected({ controls: JSON.stringify(controls) }, { replace: true });


// Controls of the output viewers (e.g. image diff on/off), which visualizations are shown,
// and the values of dynamic options: [controls, setControls]
const useViewerControls = config => {
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
    save_controls(controls);
  };
  return [controls, update];
}

export { controls_defaults, save_controls, useViewerControls }
