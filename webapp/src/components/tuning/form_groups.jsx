import { useEffect, useEffectEvent, useState } from "react";
import { useQueries, useQueryClient } from "@tanstack/react-query";

import { CopyToClipboard } from "../CopyToClipboard";

import { http, errorMessage } from "../../api/http";
import MonacoEditor from "../MonacoEditor";

import {
  Classes,
  Callout,
  Intent,
  NonIdealState,
  Button,
  Tag,
  Tab,
  Tabs,
} from "@blueprintjs/core";
import { toaster } from "../../toaster"
import { blob_url } from "../../git"


const editor_options = {
  selectOnLineNumbers: true,
  seedSearchStringFromSelection: true,
  renderWhitespace: "all",
  //renderSideBySide: false
};


const groupsQuery = (project, name) => ({
  queryKey: ['tests-groups', project, name],
  queryFn: async ({ signal }) => (await http.get('/api/v1/tests/groups', { params: { project, name }, signal, responseType: 'text' })).data,
  staleTime: 60 * 1000,
});


// Edit the definitions of batches of tests: shared with everyone, or private
const AddRecordingsForm = ({ project, commit, config, git, available_tests_files, docs_root }) => {
  const queryClient = useQueryClient();
  const files = available_tests_files ?? {};
  const names = Object.values(files);
  const groups_queries = useQueries({ queries: names.map(name => groupsQuery(project, name)) });
  // what users typed, before they save it
  const [edits, setEdits] = useState({});
  const [dirty, setDirty] = useState({});
  const [submitted, setSubmitted] = useState({});
  const [selectedTabId, setSelectedTabId] = useState("usr");

  const group_name = files[selectedTabId];
  const group_index = names.indexOf(group_name);
  const group_value = edits[group_name] ?? groups_queries[group_index]?.data;
  const is_group_dirty = dirty[group_name];
  const is_group_submitted = submitted[group_name];

  const updateGroups = newGroups => {
    setEdits(edits => ({ ...edits, [group_name]: newGroups }));
    setDirty(dirty => ({ ...dirty, [group_name]: true }));
  };

  const onSubmit = (name, e) => {
    e.preventDefault();
    const groups = edits[name] ?? groups_queries[names.indexOf(name)]?.data;
    setDirty(dirty => ({ ...dirty, [name]: false }));
    setSubmitted(submitted => ({ ...submitted, [name]: true }));
    toaster.show({
      message: `The request was sent!`,
    });
    http.post('/api/v1/tests/groups', { project, groups }, { params: { project, name } })
      .then(() => {
        setSubmitted(submitted => ({ ...submitted, [name]: false }));
        queryClient.setQueryData(groupsQuery(project, name).queryKey, groups);
        // tuning experiments will see the new definitions
        queryClient.invalidateQueries({ queryKey: ['tests-group', project] });
        toaster.show({
          message: `Saved`,
          intent: Intent.SUCCESS
        });
      })
      .catch(error => {
        setSubmitted(submitted => ({ ...submitted, [name]: false }));
        setDirty(dirty => ({ ...dirty, [name]: true }));
        toaster.show({
          message: `Something went wrong: ${errorMessage(error)}`,
          intent: Intent.DANGER,
        });
      });
  };

  // CTRL-S (or CMD-S on Mac) saves
  const onKeyDown = useEffectEvent(e => {
    if (!(e.ctrlKey || e.metaKey) || e.key !== 's') return;
    e.preventDefault(); // Prevent browser's save dialog
    // Only submit if we have a valid group name and there are dirty changes
    if (group_name && dirty[group_name])
      onSubmit(group_name, e);
  });
  useEffect(() => {
    document.addEventListener('keydown', onKeyDown);
    return () => document.removeEventListener('keydown', onKeyDown);
  }, []);

  const error = groups_queries.find(q => q.error)?.error;
  if (error)
    return (
      <NonIdealState
        title="An error occurred"
        description={errorMessage(error)}
      />
    );

  const user_form_name = files['usr'] || '';

  const panel_user = <>
    <MonacoEditor
      height={"80vh"}
      language='yaml'
      options={editor_options}
      name="user_groups"
      onChange={updateGroups}
      value={group_value || ""}
    />
  </>

  const panel_shared = <>
    <MonacoEditor
      height={"80vh"}
      language='yaml'
      options={editor_options}
      name="groups"
      onChange={updateGroups}
      value={group_value || ""}
    />
  </>

  let commit_groups_files = config.inputs?.batches ?? config.inputs?.groups ?? []; // .groups for backward compat
  if (!Array.isArray(commit_groups_files))
    commit_groups_files = [commit_groups_files]
  // we allow python-syntax formatting with project/subproject
  // ideally we should something more... complete
  const project_repo = git?.path_with_namespace ?? '';
  const subproject = project.slice(project_repo.length + 1);
  const subproject_parts = subproject.split('/')
  const project_parts = project.split('/')
  commit_groups_files = commit_groups_files.map(f =>
    // FIXME: call utils.fill_template, with a twist to replace "\${key}" with ${key},
    //        but not trivial since those are to be interpreted as python Pathlib...
    f.replace('{project.name}', project_parts[project_parts.length-1])
     .replace('{subproject.parts[0]}', subproject_parts[0])
     .replace('{subproject}', subproject)
  )
  return (
     <form>
      <Callout title="How to define custom batches" icon='info-sign' style={{marginBottom: '10px'}}>
        <div>Tuning experiments will try to use batch definitions from:
          <ol className={Classes.LIST}>
            <li>The <b>current commit,</b> in:
              <ul className={Classes.LIST}>
                {commit_groups_files.map(file => <li key={file}><a href={blob_url({ data: { git } }, commit?.id, file)}>{file}</a></li>)}
              </ul>
            </li>
            <li><b>Shared</b> with all QA-Board users.</li>
            <li><b>Private</b> ({user_form_name}), that only you can view and edit.</li>
          </ol>
        </div>
        <p>To know more about the <b>syntax</b> of this files, <a href={`${docs_root}docs/batches-running-on-multiple-inputs`}>read the docs</a>.</p>
        {(config.inputs?.database !== undefined) && <p>
           <em>By default input paths are relative to</em> <code>{config.inputs?.database?.windows}</code>
          <CopyToClipboard
            text={config?.inputs?.database?.windows}
            style={{margin: '5px'}}
            onCopy={() => {
              toaster.show({
                message: "Copied to clipboard!",
                intent: Intent.SUCCESS
              });
            }}>
            <Tag interactive minimal round icon="duplicate">Copy</Tag>
          </CopyToClipboard>
        </p>}
      </Callout>
      <div className={`${Classes.INLINE} ${Classes.FORM_GROUP}`}>
        <Button
          disabled={!is_group_dirty || is_group_submitted}
          intent={Intent.PRIMARY}
          onClick={e => onSubmit(group_name, e)}
          style={{marginRight: '12px'}}
          icon="floppy-disk"
          >
        <span>Update Batches</span>
        </Button>
      </div>

      <div className={`${Classes.INLINE} ${Classes.FORM_GROUP}`} />

      <Tabs renderActiveTabPanelOnly id="Groups" onChange={setSelectedTabId} selectedTabId={selectedTabId}>
        <Tab id="gr" title="Shared" panel={panel_shared} />
        <Tab id="usr" title={user_form_name} panel={panel_user} />
      </Tabs>
    </form>
  );
};


export { AddRecordingsForm };
