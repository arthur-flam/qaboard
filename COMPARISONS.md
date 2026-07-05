# Design: flexible comparison controls

Status: proposal (no implementation yet). Base: `unified-master`.

## Problems with today's comparison model

1. Users can only pick **2 commits/batches at a time** ("new" vs "reference").
2. The **reference for each run is picked automatically** (nearest-match heuristic) with no user control — not at the batch level, not per run.
3. Output viewers are **hard-wired 1-vs-1** (new vs one reference).
4. Any change must **keep the UX clear** — the 2-way flow is simple and must stay the default.

## How comparison works today (code map)

The whole stack is architecturally *one-new-vs-one-reference*:

- **State**: `selected` (per-project, `webapp/src/defaults.js:104`) holds twin fields
  `new_project/ref_project`, `new_commit_id/ref_commit_id`, `selected_batch_new/_ref`,
  `filter_batch_new/_ref`. URL params: `reference`, `batch`, `batch_ref`, `filter`, `filter_ref`
  (`webapp/src/actions/selected.js:10`).
- **UI**: `AppNavbar` stacks two `CommitNavbar type="new"|"ref"` (`webapp/src/AppNavbar.js:157`).
  `CommitNavbar` is already generic on `type` — every key is derived as `` `${type}_...` ``.
- **Reference resolution**: `batchSelector` (`webapp/src/selectors/projects.js:157`) runs
  `matching_output` (`webapp/src/utils.js:75`) for every filtered output: a 1-NN fuzzy match
  (same input, then Levenshtein on configurations, extra-parameters, platform) that writes a
  single `output.reference_id` + `output.reference_mismatch`. No user override exists.
- **Viewers**: `OutputCardsList` → `OutputCard(output_new, output_ref)` → `OutputViewer` →
  every viewer takes exactly `output_new`/`output_ref`. Image viewer = two synced OpenSeadragon
  instances + pixelmatch diff (`webapp/src/viewers/images/images.js`); plotly overlays 2–3
  traces; text uses a 2-way `ReactDiffViewer`. Tables (`webapp/src/components/tables.js`)
  compute 1-vs-1 metric deltas via `reference_id`.
- **Backend**: no notion of reference at all — comparison is purely a frontend concern.
  The only persisted "saved reference" mechanism is **milestones**
  (`project.data['milestones']`, `backend/backend/api/milestones.py`).

## Proposed UX

Guiding rule: **the 2-way flow stays the default and looks unchanged**. Extra power appears
through progressive disclosure.

### 1. Comparison targets become a list ("comparison tray")

Generalize "the reference" to an ordered list of *comparison targets*, each an independent
`{project, commit, batch, filter}` tuple (exactly what one `CommitNavbar` edits today).

- Default: one target → identical to today's two navbars.
- "+ Compare…" (in the reference navbar / quick-actions menu) adds a target: pick a commit,
  a milestone, "previous commit on branch", or another batch of the same commit.
- With N > 1 targets the reference navbar collapses into a row of **chips**
  (`label · commit · batch · filter`, colored consistently across all views). Clicking a chip
  expands the full `CommitNavbar` to edit it. One chip is starred as the **primary reference**
  — it drives pass/fail deltas and stays what `reference=` means in the URL.
- Milestones become one-click chips: "compare against all milestones" is the killer use of N-way.

### 2. Reference control, at three levels

1. **Batch level (per target)** — each chip already has its own batch + filter (this exists
   today as `batch_ref`/`filter_ref`; it just becomes per-target). The filter is the coarse
   "pick which runs are candidates" control.
2. **Matching-policy level** — a small popover on each chip: *match runs by* `input` /
   `input + configurations` / `input + configurations + platform` (default: today's fuzzy
   nearest-match). This turns the implicit heuristic into an inspectable, tweakable policy.
3. **Run level (pin)** — on each `OutputCard`, the reference row shows the auto-matched run
   with its mismatch tags plus a dropdown of ranked candidates (the sorted list
   `matching_output` already computes, just not exposed). Picking one **pins** the reference
   for that run; pinned refs get a 📌 tag and a "reset to auto" action. Pins live in the URL
   so they're shareable.

### 3. Viewers: N-way where it aggregates, pairwise-with-switcher where it's pixel-level

- **Tables / metrics / plots are truly N-way**: `TableCompare` gains one delta column-group
  per target; `MetricsTags` on cards show per-target deltas; plotly overlays one trace per
  target (color = chip color).
- **Pixel-level viewers (image, text, video) stay pairwise** — a 3-way pixelmatch or text
  diff isn't meaningful — but each card gets a **target switcher** (segmented control
  `B | C | D`, keyboard `1..9`) so flipping between references is instant. The image viewer's
  blink mode (`t`) plus the switcher covers most "which reference regressed?" workflows.
- **Optional image grid mode** (stretch goal): new + all targets side-by-side with synced
  pan/zoom. The `synced_viewers` group mechanism already syncs by input; it needs to accept
  arrays instead of a pair.

This split keeps the UI legible: one mental model (chips = colors = columns), no combinatorial
pixel-diff UI.

## Technical plan

### Phase 0 — internal generalization, zero UX change

- `selected.comparisons: [{id, project, commit_id, batch, filter, match_policy}]`.
  Back-compat: `ref_*` fields alias `comparisons[0]`; URL keeps `reference`/`batch_ref`/
  `filter_ref` for the first target and adds `compare=` (JSON array) for extras, plus
  `pins=` for run-level overrides.
- `batchSelector` produces `ref_batches: Batch[]`; matching writes
  `output.references = {targetId: {id, mismatch}}` and keeps `output.reference_id`
  pointing at the primary target so **every existing consumer keeps working**.
- `matching_output` returns the ranked candidate list (it already computes it) and accepts
  a `match_policy`; pins short-circuit it.
- Perf: matching is O(new × ref) with memoized Levenshtein per target — linear in number of
  targets, acceptable for the 2–4 targets this UX encourages. Guard with a soft cap (e.g. 5).

Touches: `defaults.js`, `actions/selected.js`, `reducers/index.js`, `selectors/projects.js`,
`utils.js`. This phase is mostly mechanical and fully testable against current behavior.

### Phase 1 — comparison tray

- `CommitNavbar` switches from `type: "new"|"ref"` to `target: {index}` (it's already generic;
  the change is key-derivation + dispatch payloads).
- `AppNavbar`: render chip row when `comparisons.length > 1`, expand-to-edit, add/remove/
  reorder/star-primary, milestone → chip shortcut.
- Extend `switchSelection` / `copyToOtherType` to "promote target to new" / "add as target".

### Phase 2 — reference controls

- Chip popover for match policy (stored per target in `selected`).
- `OutputCard`: reference row with ranked-candidate dropdown + pin/unpin; pins in URL.
- Surface `reference_mismatch` more prominently when the policy is strict ("no match" instead
  of silently fuzzy-matching).

### Phase 3 — N-way viewers

- `tables.js`: per-target delta column groups; `metrics.js` tags per target.
- `OutputCard`/`OutputViewer`: pass `outputs_ref` (map) + `active_target` (switcher state,
  synced across cards via the existing `controls` mechanism); viewers keep receiving a single
  resolved `output_ref` = `outputs_ref[active_target]`, so **no viewer internals change**.
- plotly: overlay one trace-set per target.
- Stretch: image grid mode (arrays in `synced_viewers`, layout width `/ (N + diff)`).

### Phase 4 (optional, backend) — persistence & scale

- Named comparison sets saved per project, as a natural extension of milestones
  (`project.data['comparison_sets']`, same CRUD pattern as `api/milestones.py`).
- Re-enable server-side `Batch.aggregated_metrics` (currently hardcoded `{}`,
  `models/Batch.py:89`) so N-way summary tables don't require shipping all outputs
  of every target to the client for large batches.

### Sequencing & risk

Each phase ships independently; Phase 0+1 alone already deliver "compare against several
milestones at once". Main risks: URL-length for many pins (mitigate: keep pins only for the
current page, or move to localStorage), and image-viewer sync complexity (isolated to the
stretch goal). The existing automatic matching, 2-way URLs, and all viewers keep working
unchanged throughout.
