import { useEffect, useRef, useState } from "react";
import { useQuery } from "@tanstack/react-query";

import { Classes, Tag } from "@blueprintjs/core";
import MonacoEditor, { MonacoDiffEditor } from "../components/MonacoEditor";

import { http, errorMessage } from "../api/http";
import { output_files_stale_time } from "../api/queries";
import { is_same_data } from "../utils"

// TODO: Implement a way to hide identical lines in the diff viewer
// 1. We could use the diffNavigator
// https://microsoft.github.io/monaco-editor/playground.html#creating-the-diffeditor-navigating-a-diff
// https://github.com/react-monaco-editor/react-monaco-editor/issues/84
// https://github.com/react-monaco-editor/react-monaco-editor#how-to-get-value-of-editor
// 2. Or try to the get the diff and remove everything bu those lines...

const ansi_pattern = [
  '[\\u001B\\u009B][[\\]()#;?]*(?:(?:(?:[a-zA-Z\\d]*(?:;[a-zA-Z\\d]*)*)?\\u0007)',
  '(?:(?:\\d{1,4}(?:;\\d{0,4})*)?[\\dA-PR-TZcf-ntqry=><~]))'
].join('|');
const ansi_regexp = new RegExp(ansi_pattern, 'g');
const strip_ansi = text => text.replace(ansi_regexp, '');

const language = filename => {
  if (filename.endsWith('yaml') || filename.endsWith('yml'))
    return 'yaml';
  if (filename.endsWith('json') || filename.endsWith('tuneset0'))
    return 'json';
  if (filename.endsWith('js'))
    return 'javascript';
  if (filename.endsWith('py'))
    return 'python';
  if (filename.endsWith('cde'))
    return 'python';
  return 'plaintext';
}


const editor_options = {
  selectOnLineNumbers: true,
  seedSearchStringFromSelection: true,
  readOnly: true,
};


// Files are cached: switching between views doesn't fetch them again.
// The key includes the response type, since other viewers may parse the same file as JSON.
export const textFileQuery = (url, { is_running } = {}) => ({
  queryKey: ['file', url, 'text'],
  queryFn: ({ signal }) => http.get(url, { signal, responseType: 'text' }).then(r => r.data),
  select: strip_ansi,
  enabled: !!url,
  staleTime: is_running ? 0 : output_files_stale_time,
});

const count_lines = text => (text?.match(/\r?\n/g)?.length ?? 0) + 1;


function GenericTextViewer({ text_url_new, text_url_ref, filename, renderSideBySide: side_by_side_default, always_show_diff, only_diff, manifests, width, max_lines = 40, language: language_prop, is_running }) {
  const query_new = useQuery(textFileQuery(text_url_new, { is_running }));
  // we don't really care about errors for reference files
  const query_ref = useQuery({ ...textFileQuery(text_url_ref, { is_running }), enabled: !!text_url_new && !!text_url_ref });

  const [shown_left, setShownLeft] = useState("reference");
  const [renderSideBySide, setRenderSideBySide] = useState(side_by_side_default ?? true);
  const editor = useRef(null);

  // Press "t" to switch new and reference
  useEffect(() => {
    const keyboard = ev => {
      if (ev.target.nodeName === 'INPUT' || ev.target.nodeName === 'TEXTAREA' || ev.target.isContentEditable)
        return;
      if (ev.ctrlKey || ev.metaKey || ev.altKey)
        return;
      if ((ev.id || String.fromCharCode(ev.keyCode || ev.charCode)) === "t")
        setShownLeft(shown_left => shown_left === 'reference' ? 'new' : 'reference');
    }
    window.addEventListener("keypress", keyboard, { passive: true });
    return () => window.removeEventListener('keypress', keyboard);
  }, []);

  const is_loaded = !!text_url_new && !query_new.isPending && (!text_url_ref || !query_ref.isPending);
  if (!is_loaded) return <span/>;
  if (query_new.isError && !always_show_diff) return <span>{errorMessage(query_new.error)}</span>

  const data = {
    new: query_new.data ?? '',
    reference: query_ref.data ?? '',
  };
  if (!data.new && !always_show_diff)
    return <span></span>

  if (only_diff && data.new === data.reference)
    return <span></span>

  const has_same_data = is_same_data(filename, manifests?.new?.[filename], manifests?.reference?.[filename])
  const no_reference = !text_url_ref || !data.reference || (!!text_url_new && text_url_new === text_url_ref);
  const show_diff = !no_reference || always_show_diff;

  const height = 18 * Math.min(Math.max(count_lines(data.new), count_lines(data.reference)), max_lines) + 10;
  const editor_language = language_prop || language(filename);
  const editor_element = show_diff
    ? <MonacoDiffEditor
        readonly
        width={width}
        height={height}
        language={editor_language}
        value={shown_left==='reference' ? data.new : data.reference}
        original={shown_left==='reference' ? data.reference : data.new}
        options={{
          ...editor_options,
          renderSideBySide,
        }}
        editorDidMount={instance => { editor.current = instance }}
      />
    : <MonacoEditor
        readonly
        width={width}
        height={height}
        language={editor_language}
        value={data.new}
        options={editor_options}
      />

  return <>
    <h3 className={Classes.HEADING}>
      <span style={{marginRight: "5px"}}>{filename}</span>
      <Tag>{show_diff ? `${shown_left} ➡️ ` : ""}{shown_left==="reference" ? "new" : "reference"}</Tag>
      {!no_reference && <Tag interactive style={{marginLeft: "5px", verticalAlign: "bottom"}} icon={renderSideBySide ? "comparison" : "align-justify"} minimal onClick={() => setRenderSideBySide(!renderSideBySide)}>
        {renderSideBySide ? "Side-by-side" : "Inline diff"}
      </Tag>}
      {!no_reference && !has_same_data && <Tag interactive style={{marginLeft: "5px", verticalAlign: "bottom"}} icon="double-chevron-right" minimal onClick={() => editor.current?.goToDiff('next')}>
        Next Diff
      </Tag>}
      {!no_reference && has_same_data && <Tag style={{marginLeft: "5px", verticalAlign: "bottom"}} icon="duplicate" minimal>
        Same Content
      </Tag>}
    </h3>
    {editor_element}
  </>
}


export default GenericTextViewer;
