import { useEffect, useMemo, useState } from "react";
import qs from "qs";
import { useInView } from "react-intersection-observer";

import { history as router_history } from "../router";
import { BitAccuracyForm } from "./bit_accuracy/utils";
import { OutputCard } from "./OutputCard";


// Batches can have thousands of outputs: we render the cards incrementally, as users scroll down
const page_size = 30;

// bit-accuracy controls, saved in the URL
const form_from_url = search => {
  const params = new URLSearchParams(search);
  return {
    show_all_files: params.get("show_all_files") === 'true',
    hide_runs_without_files: params.get("hide_runs_without_files") === 'true',
    expand_all: params.get("expand_all") === 'true',
    files_filter: params.get("files_filter") || '',
    color_blind_friendly: params.get("color_blind_friendly") === 'true',
  };
}


function OutputCardsList({ type, project, config, metrics, new_commit, new_batch, ref_batch, controls, history = router_history, onRegisterOutputOptions, onToggleDynamicOptionSync }) {
  const [form, setForm] = useState(() => form_from_url(window.location.search));
  const { show_all_files, hide_runs_without_files, expand_all, files_filter, color_blind_friendly } = form;

  // The form changes as users type: we don't add browser history entries
  const save = (name, value) => {
    setForm(form => ({ ...form, [name]: value }));
    const query = qs.parse(window.location.search.substring(1));
    history.replace({
      pathname: window.location.pathname,
      search: qs.stringify({ ...query, [name]: value }),
    });
  }
  const update = (attribute, attribute_url) => e => save(attribute_url || attribute, e?.target?.value !== undefined ? e.target.value : e);
  const toggle = name => () => save(name, !form[name]);

  const outputs = useMemo(() => (new_batch?.filtered?.outputs ?? [])
    .map(id => [id, new_batch.outputs[id]])
    .filter(([, output]) => output && output.output_type !== "optim_iteration"),
  [new_batch]);

  const [nb_shown, setNbShown] = useState(page_size);
  const { ref: more_ref, inView: want_more } = useInView({ rootMargin: '2000px 0px', fallbackInView: true });
  useEffect(() => {
    // oxlint-disable-next-line react/set-state-in-effect
    if (want_more && nb_shown < outputs.length) setNbShown(nb_shown => nb_shown + page_size);
  }, [want_more, nb_shown, outputs.length]);

  return (
    <>
      {type === 'bit_accuracy' && <BitAccuracyForm
                                   show_all_files={show_all_files}
                                   hide_runs_without_files={hide_runs_without_files}
                                   expand_all={expand_all}
                                   files_filter={files_filter}
                                   color_blind_friendly={color_blind_friendly}
                                   toggle={toggle}
                                   update={update}
                                  />
      }
      <div
        style={{
          display: "flex",
          justifyContent: "space-between",
          flexFlow: "row wrap",
        }}
      >
        {outputs.slice(0, nb_shown).map(([id, output]) => <OutputCard
          key={id}
          output_type={output.output_type}
          output_new={output}
          output_ref={ref_batch?.outputs?.[output.reference_id]}
          project={project}
          config={config}
          metrics={metrics}
          commit={new_commit}
          controls={controls}
          type={type}
          show_all_files={show_all_files}
          hide_runs_without_files={hide_runs_without_files}
          files_filter={files_filter}
          expand_all={expand_all}
          color_blind_friendly={color_blind_friendly}
          onRegisterOutputOptions={onRegisterOutputOptions}
          onToggleDynamicOptionSync={onToggleDynamicOptionSync}
        />)}
      </div>
      {nb_shown < outputs.length && <div ref={more_ref} style={{ height: '1px' }} />}
    </>
  );
}


export { OutputCardsList }
