// What code a local run used: its uncommitted changes (and unpushed commits), the patch, and how to get the code back.
// Loaded lazily (LocalChangesTag.jsx): it shows the patch in Monaco.
import { useRef, useState } from "react";
import { useQuery } from "@tanstack/react-query";
import {
  AnchorButton, Button, Callout, Classes, Dialog, DialogBody, DialogFooter, Intent, NonIdealState, SegmentedControl, Spinner, Tag,
} from "@blueprintjs/core";

import MonacoEditor, { MonacoDiffEditor } from "../MonacoEditor";
import { useSiteConfig } from "../../hooks";
import { copyWithToast } from "../../utils/clipboard";
import {
  changes_sentence, git_commands, index_patch, is_dirty, local_changes_of, max_compared, max_shown, omitted_message, patchQuery,
  patch_url, restore_command, saved_file, short_sha, status_names, unpushed_text,
} from "./localChanges";


const editor_options = {
  readOnly: true,
  minimap: { enabled: false },
  scrollBeyondLastLine: false,
  renderWhitespace: 'boundary',
  folding: true,
};
const diff_options = { ...editor_options, renderSideBySide: true, useInlineViewWhenSpaceIsLimited: false };

// Outside of the component: the React Compiler fails on Math.min there
const max_lines = 30;
const editor_height = lines => 18 * Math.min(Math.max(lines, 3), max_lines) + 10;

const status_intent = { A: Intent.SUCCESS, '?': Intent.SUCCESS, D: Intent.DANGER };
const status_letter = status => typeof status === 'string' && status ? status.slice(0, 1) : '·';

const count = value => typeof value === 'number' && Number.isFinite(value) ? value : null;

// The files, each scrolls the patch to its changes
const FileList = ({ files, total, onReveal, revealable }) => {
  if (files.length === 0) return null;
  return <div className="local-changes-files" style={{ maxHeight: 180, overflow: 'auto', marginBottom: 10 }}>
    <ul className={Classes.LIST_UNSTYLED} aria-label="Changed files">
      {files.map(file => {
        const path = String(file.path ?? '');
        const letter = status_letter(file.status);
        const added = count(file.added);
        const deleted = count(file.deleted);
        return <li key={path} style={{ display: 'flex', alignItems: 'center', gap: 6, minHeight: 22 }}>
          <Tag minimal intent={status_intent[letter]} title={status_names[letter] ?? 'changed'} style={{ fontFamily: 'monospace', minWidth: 20, textAlign: 'center' }}>{letter}</Tag>
          {revealable(path)
            ? <Button size="small" variant="minimal" className={Classes.MONOSPACE_TEXT} style={{ minWidth: 0 }} ellipsizeText text={path} title={`Show the changes of ${path}`} onClick={() => onReveal(path)}/>
            : <span className={Classes.MONOSPACE_TEXT} style={{ fontSize: 12, overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap', padding: '0 7px' }}>{path}</span>}
          <span style={{ marginLeft: 'auto', fontVariantNumeric: 'tabular-nums', fontSize: 12, whiteSpace: 'nowrap' }}>
            {file.binary
              ? <span className={Classes.TEXT_MUTED}>binary</span>
              : <>
                {added !== null && <span style={{ color: 'var(--qa-success, #238551)' }}>+{added}</span>}{' '}
                {deleted !== null && <span style={{ color: 'var(--qa-danger, #cd4246)' }}>−{deleted}</span>}
              </>}
          </span>
        </li>;
      })}
    </ul>
    {total > files.length && <p className={Classes.TEXT_MUTED} style={{ margin: '4px 0 0' }}>Only the first {files.length} of the {total} files are listed.</p>}
  </div>;
};

const Notes = ({ changes, which }) => {
  const file = changes[which];
  const omitted = omitted_message(file, { what: which === 'unpushed' ? 'patch of the unpushed commits' : 'patch' });
  const excluded = Array.isArray(changes.excluded) ? changes.excluded.map(String) : [];
  const notes = [];
  if (is_dirty(changes) === null)
    notes.push(<Callout key="unknown" compact intent={Intent.WARNING} icon="help">
      git couldn't tell whether this run had uncommitted changes{changes.timed_out ? ': it took too long' : ''}.
    </Callout>);
  if (omitted)
    notes.push(<Callout key="omitted" compact intent={file.omitted === 'level' ? Intent.NONE : Intent.WARNING} icon="info-sign">{omitted}</Callout>);
  if (changes.error && typeof changes.error === 'string')
    notes.push(<Callout key="error" compact intent={Intent.WARNING} icon="error">git said: <code>{changes.error.slice(0, 300)}</code></Callout>);
  if (which === 'patch' && excluded.length > 0)
    notes.push(<Callout key="excluded" compact icon="lock">
      Left out: {excluded.slice(0, 5).join(', ')}{excluded.length > 5 ? `, and ${excluded.length - 5} more` : ''}. Untracked files that look like secrets aren't saved.
    </Callout>);
  if (which === 'patch' && changes.untracked_contents === 'too_many')
    notes.push(<Callout key="untracked" compact icon="info-sign">
      There were too many untracked files: they are listed, but their contents aren't in the patch.
    </Callout>);
  if (notes.length === 0) return null;
  return <div style={{ display: 'flex', flexDirection: 'column', gap: 6, marginBottom: 10 }}>{notes}</div>;
};

const Reproduce = ({ output, changes }) => {
  const commands = git_commands(changes);
  const restore = restore_command(output);
  if (commands.length === 0 && !restore) return null;
  return <div style={{ marginTop: 10 }}>
    {commands.length > 0 && <details>
      <summary style={{ cursor: 'pointer' }}>With git</summary>
      <p className={Classes.TEXT_MUTED} style={{ margin: '6px 0' }}>
        In a clean checkout, after downloading the patch{commands.length > 1 ? 'es' : ''} next to it:
      </p>
      {commands.map(command => <div key={command} style={{ display: 'flex', alignItems: 'center', gap: 6 }}>
        <code className={Classes.CODE_BLOCK} style={{ flex: 1, margin: '2px 0', whiteSpace: 'pre-wrap', wordBreak: 'break-all' }}>{command}</code>
        <Button size="small" variant="minimal" icon="duplicate" aria-label={`Copy: ${command}`} onClick={() => copyWithToast(command, { what: 'the command' })}/>
      </div>)}
    </details>}
    {restore && <span className={Classes.TEXT_MUTED} style={{ fontSize: 12 }}>
      <code>{restore}</code> makes a branch with the run's code.
    </span>}
  </div>;
};

const files_of = patch => [...patch.files.keys()].map(path => ({ path }));

// The files, then the patch: clicking a file scrolls the patch to its changes
const mb = bytes => `${(bytes / 1024 / 1024).toFixed(1)} MB`;

const TooLarge = ({ bytes, url, what = 'show here' }) => <Callout compact icon="info-sign" style={{ marginBottom: 10 }}>
  The patch is {bytes ? `over ${mb(bytes)}` : 'large'}: too large to {what}.{' '}
  {url && <AnchorButton size="small" variant="minimal" icon="download" text="Download" href={url} download/>}
</Callout>;

const PatchText = ({ url, files, total }) => {
  const editor = useRef(null);
  const query = useQuery(patchQuery(url, max_shown));
  const patch = index_patch(query.data?.text);
  const reveal = path => {
    const line = patch.files.get(path);
    if (!editor.current || !line) return;
    editor.current.revealLineNearTop(line);
    editor.current.setPosition({ lineNumber: line, column: 1 });
    editor.current.focus();
  };
  const revealable = path => patch.files.has(path);
  let text = null;
  if (query.isPending)
    text = <div style={{ height: editor_height(10), display: 'flex', alignItems: 'center', justifyContent: 'center' }}><Spinner size={20}/></div>;
  else if (query.isError)
    text = <Callout compact intent={Intent.WARNING} icon="error">Could not load the patch: {String(query.error?.message ?? query.error)}</Callout>;
  else if (query.data.too_large)
    text = <TooLarge bytes={query.data.bytes} url={url}/>;
  else
    text = <MonacoEditor value={query.data.text} language="diff" height={editor_height(patch.lines)} options={editor_options} editorDidMount={instance => { editor.current = instance; }}/>;
  return <>
    {/* the unpushed commits' files are those of their patch */}
    <FileList files={files.length > 0 ? files : files_of(patch)} total={total} onReveal={reveal} revealable={revealable}/>
    {text}
  </>;
};

const ComparePatches = ({ url, url_ref }) => {
  const query = useQuery(patchQuery(url, max_compared));
  const query_ref = useQuery(patchQuery(url_ref, max_compared));
  if (query.isPending || query_ref.isPending) return <Spinner size={20}/>;
  if (query.isError || query_ref.isError) return <Callout compact intent={Intent.WARNING} icon="error">Could not load the patches.</Callout>;
  if (query.data.too_large || query_ref.data.too_large)
    return <TooLarge bytes={query.data.too_large ? query.data.bytes : query_ref.data.bytes} what="compare here"/>;
  return <>
    <p className={Classes.TEXT_MUTED} style={{ margin: '0 0 6px' }}>Left: the reference's changes. Right: this run's.</p>
    <MonacoDiffEditor original={query_ref.data.text} value={query.data.text} language="diff" height="55vh" options={diff_options}/>
  </>;
};

const LocalChangesDialog = ({ isOpen, onClose, output, output_ref }) => {
  const { docs_root } = useSiteConfig();
  const [tab, setTab] = useState('patch');
  const [comparing, setComparing] = useState(false);
  const changes = local_changes_of(output);
  if (!changes) return null;

  const has_unpushed_tab = !!changes.unpushed && changes.pushed === false;
  const which = has_unpushed_tab ? tab : 'patch';
  const url = patch_url(output, changes, which);
  const ref_changes = local_changes_of(output_ref);
  const url_ref = patch_url(output_ref, ref_changes, 'patch');
  const can_compare = which === 'patch' && !!url && !!url_ref && !!changes.sha256 && changes.sha256 !== ref_changes?.sha256;
  const files = which === 'patch' && Array.isArray(changes.files) ? changes.files : [];
  const total = which === 'patch' ? (count(changes.files_total) ?? files.length) : 0;
  const dirty = is_dirty(changes);
  const unpushed = unpushed_text(changes);

  return <Dialog
    isOpen={isOpen}
    onClose={onClose}
    title="Local changes"
    icon="git-new-branch"
    style={{ width: 'min(1100px, 94vw)' }}
  >
    <DialogBody>
      <p style={{ marginTop: 0 }}>
        On <code>{short_sha(changes.head) || '?'}</code>
        {changes.branch && <> on <code>{String(changes.branch)}</code></>}
        {' · '}
        {dirty === null ? 'uncommitted changes unknown' : dirty ? <>uncommitted changes: {changes_sentence(changes)}</> : 'no uncommitted changes'}
        {unpushed && <> · {unpushed}{changes.upstream ? <> (vs <code>{String(changes.upstream)}</code>)</> : null}</>}
        {' · '}
        <a href={`${docs_root}docs/local-changes`} target="_blank" rel="noopener noreferrer">Docs</a>
      </p>

      {(has_unpushed_tab || can_compare) && <div style={{ display: 'flex', gap: 10, flexWrap: 'wrap', alignItems: 'center', marginBottom: 10 }}>
        {has_unpushed_tab && <SegmentedControl
          size="small"
          aria-label="Which changes"
          value={which}
          onValueChange={value => { setTab(value); setComparing(false); }}
          options={[
            { value: 'patch', label: 'Uncommitted changes' },
            { value: 'unpushed', label: `Unpushed commits${unpushed_text(changes) ? ` (${changes.unpushed_commits ?? '?'})` : ''}` },
          ]}
        />}
        {can_compare && <Button
          size="small"
          icon="comparison"
          active={comparing}
          text={comparing ? "Back to this run's changes" : "Compare with the reference's changes"}
          onClick={() => setComparing(!comparing)}
        />}
      </div>}

      <Notes changes={changes} which={which}/>
      {comparing
        ? <ComparePatches url={url} url_ref={url_ref}/>
        : url
          ? <PatchText key={url} url={url} files={files} total={total}/>
          : <>
            <FileList files={files} total={total} revealable={() => false}/>
            {dirty && !omitted_message(changes[which]) && <NonIdealState icon="document" title="The patch wasn't saved"/>}
          </>}

      <Reproduce output={output} changes={changes}/>
    </DialogBody>
    <DialogFooter actions={<>
      {url && <AnchorButton icon="download" text="Download" href={url} download={saved_file(changes, which)?.split('/').pop() ?? 'local-changes.patch'}/>}
      {restore_command(output) && <Button icon="duplicate" text="Copy reproduce command" title={restore_command(output)} onClick={() => copyWithToast(restore_command(output), { what: 'the command' })}/>}
      <Button text="Close" onClick={onClose}/>
    </>}/>
  </Dialog>;
};

export default LocalChangesDialog;
