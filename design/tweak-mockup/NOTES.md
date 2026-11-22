# Tweak sessions: design notes

Status: exploration. Mock-up: `index.html` in this folder (one file, fake data, open it in a browser). Nothing in the app
changes.

## What the code already has, and what it doesn't

| Idea | Fits | Fights |
|---|---|---|
| Overrides as a flat patch | `outputs.extra_parameters` *is* this: the tuning parameters, set with `qa --tuning '{"nr.strength": 0.5}'`, part of the run's identity and of its folder's name. SIRC already uses `"block.register"` keys (the tuning form converts pasted CDE registers, `transformCDERegisters` in `components/tuning/forms.jsx`). | `context.params` merges configurations with `merge()`, which nests dicts. Dotted keys only work if they stay flat and the project's `run()` passes them to the binary. |
| A session | A batch: `(commit, label)` is unique, so `tweak/s14` on the commit holds the session's runs, and the commit page, Tuning and the API see them for free. | A batch belongs to one commit. A session that spans rebuilds or worktrees doesn't fit. |
| Lineage | `outputs.data` is free-form JSON: `data.tweak = {parent, own}` needs no migration. | Nothing models a parent today. Run identity is `(batch, input, configurations, extra_parameters, platform)`: two branches that end up with the same patch are **the same row**, so the next run of it becomes attempt 2 (`docs/rfcs/rerun-directories.md`). The tree is really a DAG. |
| Launch from the web app | `POST /api/v1/commit/<sha>/batch` (`api/tuning.py`) runs `qa batch` on the commit's artifacts with the server's runner. | It's batch-sized: artifacts check, a script, a runner job, `qa batch`, then `qa run`. Fine for minutes, too slow for "live". It also needs `QABOARD_TUNING_RUNNER`, which is opt-in. |
| Parameter schema | The closest thing is `qa optimize`'s `search_space` (Integer/Real/Categorical with bounds). | No per-binary schema anywhere. Nothing runs project code on demand on the server, except through a runner job. |
| Per-block cache and timings | Nothing. `run()` is a black box to QA-Board. | The binary is the only thing that can tell what it reused. |
| Intermediate streams | Viewers pick files by path patterns (`outputs.visualizations`). The image viewer already does split views and N-way tiles. | Every stream of every block of every run is stored. See Storage below. |
| Compare to a milestone | A milestone is `(commit, batch, filter)`, and runs are matched by input and platform as on the commit page. | The milestone is on another commit, so its parameter diff and its code diff are different things. The mock-up shows them separately. |

One detail: the existing button is labelled **WebCDE**, and it only shows up when a run's files include a `cde.sh`. The
mock-up shows **Tweak** only when the run has a schema.

## The three contracts

**1. Parameter schema.** This is JSON that describes the binary the run used:
`groups[] = {id, name, streams[], params[] = {k, type, min, max, step, default, options, shape, unit, doc, widget?}}`.
The order of the groups is the order of the chain. Add an optional `needs: [group ids]` later, for pipelines that
aren't chains.

- *Recommendation: the run writes it* (e.g. `qaboard/params.schema.json` in its folder, from an entrypoint hook
  `def parameters(context)` that `qa run` calls). Don't use a server-side hook that runs the binary. A schema written
  by the run always matches the binary that made the run. It's stored, and it works offline. A server hook would
  need a runner job for every page view, plus a new trust decision.
- Core: the types, validation, search across groups, a widget per type, and generic hints (`curve`, `matrix`, `lut`).
  Site plugin: the register naming (`sim|`/`hal|` prefixes), import and export of CDE register dumps (move
  `transformCDERegisters` there), and any custom widgets.

**2. Override patch.** `{ "group.param": value }`, validated against the schema.

- A run stores its **full** patch in `extra_parameters`, as today. Its **own** step goes in `data.tweak.own`. The
  configuration files are never edited.
- "Write back" generates a diff against one configuration file, plus `qa apply-tweak <run> --to <file>` to apply it
  in a working copy. The server never commits.

**3. Rerun with lineage.** `POST start-run {parent_run_id, patch, session}`: one input, one configuration, one
platform, the same commit as the parent.

- Core records the parent and gives the run its parent's folder (`context.parent_output_dir`). The run reports
  `blocks.json = [{id, state: reused|computed, secs}]`, and core shows it.
- The project's binary decides what it can reuse: a key per block, made from the upstream parameters, the input
  and the binary's hash. QA-Board can't know which parameters are upstream of what, beyond the order in the schema.
- If the patch matches an existing run of the session, QA-Board opens that run instead of starting it again. That's
  the cheapest cache, and it makes the DAG problem visible.

## Layout decisions I'm least sure about

1. **Tweak is a page, not a side panel on the run card.** The chain, a form of 3–7 rows and two to four viewers
   don't fit in a drawer. I rejected the drawer. The cost is one more navigation, which the breadcrumb and the
   "Tweak from #n" buttons try to soften.
2. **The chain is a vertical list on the left, not a horizontal strip or a node graph.** A strip of 10–12 blocks has
   no room for the edit badges and the timings. A graph is only worth it for real DAGs. If pipelines branch, the
   list needs indentation, and that's where it could break down.
3. **No preview of pending edits.** The viewers only show results that ran. A pending bar shows the patch and an
   estimate ("reuses 7 blocks, ≈ 13 s"), and nothing runs until you click Run. I rejected auto-running on every edit
   (CDE's feel): each run costs a runner slot and storage. This can be revisited once a warm worker exists.

## Pushback

- **Live feel (#4) is the weakest idea as written.** QA-Board can show reuse and timings, but it can't make runs fast.
  The start overhead (runner, queue, loading the binary) dominates late-block tweaks. The mock-up's estimate shows it
  even with a 6 s start, and on LSF it takes minutes. Real "live" needs a warm process per session that keeps the
  binary loaded and its intermediates in memory. That's rebuilding CDE's service. Ship the display and the
  estimate, and don't promise live.
- **`qa connect` is a product of its own** (a local agent, auth, a tunnel to a laptop, local changes). For v1, use
  `qa run --parent <id>` from a working copy, which uploads its run like any local run.
- **Custom widgets from a site plugin**: the web app loads no code at runtime. Site widgets would be compiled in per
  site (like `QABOARD_SITE` docs). Start with the generic widgets.
- **CDE's tabs (several chains or binaries at once)**: don't copy them. Use one session per browser tab, and compare
  across sessions with the existing run comparison.

## Open questions and risks

- **Storage of short-lived runs.** With every intermediate stream stored, 10 blocks × 1–2 streams × 12 MP comes to
  hundreds of MB per run, and 30 runs per session. Proposal: streams are opt-in per block, or kept only for the
  last N runs. Unpinned session runs expire after N days (pinned and milestone runs stay). The parents' intermediates
  are kept while their children could still branch. Retention today goes by the commit's age, and protects
  milestone batches (`backend/clean.py`). This needs per-run rules.
- **Several binaries or worktrees in one session.** v1: a session is one commit, one binary and one input. Branching
  "on another commit" starts a new root that carries the patch, flags keys the new schema doesn't have, and reuses
  no cache. Allowing parents across commits would make the parameter diff and the cache lie.
- **Dirty git state.** Runs launched from the web app use the commit's artifacts, so they're clean. Local runs record
  their changes already (`qaboard/local_changes.py`). The cache key must include that fingerprint, and the tree should
  mark "code differs from parent".
- **Minutes instead of seconds.** Show the estimate before Run. Allow queuing several children. Use the existing
  batch notifications. Make the session page work as "come back later" (compare, pin, write back), not as a spinner.
  This is likely the common case at SIRC.
- **Same patch reached twice** (see Lineage above): decide whether a run can have several parents, or whether the
  second path only links to the first run.
- **Audit**: launching runs code, so it needs an audit event (`tweak.run`) and the same permission as tuning from
  the web app.

**To confirm with you:** does every SIRC binary already accept arbitrary `block.register` overrides through
`run()`, as the tuning-form conversion suggests? And can it write and reuse per-block intermediates given a
parent's folder? If it can't, ideas 4 and 5 depend on work in the simulator, not in QA-Board.
