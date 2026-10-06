import { useQuery } from "@tanstack/react-query";

import { http, errorMessage } from "../api/http";
import { output_files_stale_time } from "../api/queries";


// Files are cached: switching between views doesn't fetch them again
const fileQuery = (url, output) => ({
  queryKey: ['file', url],
  queryFn: ({ signal }) => http.get(url, { signal }).then(r => r.data),
  enabled: !!url,
  staleTime: output?.is_running ? 0 : output_files_stale_time,
});


const HtmlViewer = ({ path, output_new, output_ref, style }) => {
  const url_new = output_new?.output_dir_url && path ? `${output_new.output_dir_url}/${path}` : undefined;
  const has_reference = !!output_ref?.output_dir_url;
  const url_ref = url_new && has_reference ? `${output_ref.output_dir_url}/${path}` : undefined;
  const query_new = useQuery(fileQuery(url_new, output_new));
  // we don't really care about errors for reference outputs
  const query_ref = useQuery(fileQuery(url_ref, output_ref));

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
