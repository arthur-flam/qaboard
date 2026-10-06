import { useQuery } from "@tanstack/react-query";

import { errorMessage } from "../api/http";
import { fileQuery } from "../api/queries";


// Files are cached: switching between views doesn't fetch them again


const HtmlViewer = ({ path, output_new, output_ref, style }) => {
  const url_new = output_new?.output_dir_url && path ? `${output_new.output_dir_url}/${path}` : undefined;
  const has_reference = !!output_ref?.output_dir_url;
  const url_ref = url_new && has_reference ? `${output_ref.output_dir_url}/${path}` : undefined;
  const query_new = useQuery(fileQuery(url_new, { is_running: output_new?.is_running }));
  // we don't really care about errors for reference outputs
  const query_ref = useQuery(fileQuery(url_ref, { is_running: output_ref?.is_running }));

  if (!url_new || query_new.isPending || (url_ref && query_ref.isPending)) return <span>loading</span>;
  if (query_new.isError) return <span>{errorMessage(query_new.error)}</span>

  // https://developer.mozilla.org/fr/docs/Web/HTML/Element/iframe
  const width = style?.width || '400px';
  return <>
    {has_reference && <h3>New</h3>}
    <div style={{width}} dangerouslySetInnerHTML={{__html: query_new.data ?? ""}} />
    {has_reference && <>
      <h3>Reference</h3>
      <div style={{width}} dangerouslySetInnerHTML={{__html: query_ref.data || ""}} />
    </>}
  </>
}


export default HtmlViewer;
