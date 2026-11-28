# Tweak sessions and a parameter-aware Tuning tab: design notes

Status: exploration, second pass. Mock-up: `index.html` in this folder (one file, fake data). Nothing in the app
changes. The next step is an RFC for phase 1 (see "What to build first").

## The main idea: one model, two screens

Tweaking and tuning are the same thing at two sizes. Both are *tests × parameter rows*, where each row is one of:

| A row is | Meaning | Today |
|---|---|---|
| **One value** | set it | a tweak, or a list with one value in the tuning form |
| **Several values** | try each combination | the tuning form's grid |
| **Search** | QA-Board picks values in a range | automated tuning (`qa optimize`) |

So both screens share the parameter list, the picker, the validation and the stored format (`extra_parameters`).
They differ in what they show:

- **Tuning tab: wide.** Whole batches, many values, and the results go to Tuning Analysis. No block view.
- **Tweak screen: deep.** One image (a handful later), one value per parameter, every block's input, output, log
  and timing, and a history of versions.

They hand over to each other. A version has **Run on more tests…**, which opens the Tuning tab filled with its
values. The Tuning tab offers **Tweak instead** when the request is "one value each on a few images", and can
start from a tweak version.

I'd keep ranges out of the tweak screen and whole batches out of the session. Each addition is reasonable alone,
but together they rebuild the Tuning tab inside a screen that is already dense.

## Decided in the discussion

- A session is a batch (`tweak/<id>` on the commit). A version's full set of changes is its `extra_parameters`.
- The tweak view is its own screen, not a panel.
- No working copy per session. Runs use the commit's build and the changes only; files matter when saving.
- No git words on the main path ("version", "previous", "original", "keep", "save", "send for review"). The git
  terms appear once, in muted text, for those who know them.
- Site widgets are compiled into each site's web app. Loading them at run time is a later question.
- Parameters the user sets and flags that control execution are separate (`params` and `execution`, below).
- The simulator can write intermediates and start at a given block with injected streams.

## Recommendations on what is still open

**1. The live service: allow it, but never depend on it.** One rule makes it safe to build step by step:
*a cache miss may be slower, never different*. QA-Board describes every run the same way (commit, input,
changes, execution hints), whoever executes it.

- Three executors answer the same request: a cluster job (today), a warm pool (Dask workers that stay up), and a
  live simulator (the CDE service, or `qa connect`). If the live one dies or loses its cache, the run falls back
  to the next and gives the same result.
- A cache bug can't reach anything that matters, because **saving goes through a clean full run**: "Send for
  review" makes CI run the whole test suite on the saved settings. Tweak results are exploration.
- A **Recompute from scratch** action on a version checks a doubtful result. A run that reused blocks says so.
- So "degrade to losing the cache" is the right bar for your engineer's service, and it's much lower than "the
  cache is always right".

**2. How much stays on the user's side: three hooks, and QA-Board owns shapes, not meanings.**

- `parameters(configurations) -> schema`: required for the rich form. Without it the form keeps free-text names.
- `validate(changes) -> errors, warnings`: optional. This is the escape hatch that keeps the schema from growing
  into a language: dependencies between parameters, per-sensor rules and "ignored unless…" are checks the project
  writes in Python, and the form shows their messages.
- `save(changes, how) -> file changes`: optional. Without it, QA-Board writes a new file with only the changes
  (generic, since it's the same flat dict). Saving into the files where values are defined is entirely this hook.
- **Schema v1 fields:** key, group (block) and its order, type (bool, int, float, enum, text, list, matrix),
  default, min/max/step, unit, description, optional widget name, and the group's streams.
- **Kept out on purpose:** conditional visibility, computed defaults, constraints between parameters,
  per-input defaults. If `validate` proves too weak in practice, add fields then, with real cases.

**3. What to build first, and for whom.**

| Phase | What | For whom | Needs |
|---|---|---|---|
| 1 | `parameters` hook and the parameter-aware Tuning tab (picker, ranges, checks) | everyone who tunes today | no new infrastructure; builds on the tuning-form redesign |
| 2 | Tweak screen on cluster runs: versions, start-at-block, logs, Save | IQV and algorithm engineers | simulator flags for start-at-block; a server runner |
| 3 | Warm pool, then the live service, behind the same request | the same people, faster | measurements from phase 2 |
| later | `qa connect` for a local working copy | engineers, other sites | local changes already recorded by `qa` |

Phase 1 is useful alone, and it tests the schema on real binaries before any new screen depends on it. Phase 2
is only as good as its start-up time, so measure before promising anything (see the questions).

## Third pass: naming, where the line is, and staying generic

**Naming.** The team prefers "tuning" for both sizes: the Tuning tab tunes batches, **Tune this run** opens a
tuning session on one run. The mock-up now says so. This folder keeps its name.

**Where the line is between a generic screen and custom behaviour.** There are not two worlds to choose from.
There are two layers, and the lower one comes first:

- **The record** is QA-Board's product: a session, its runs, which run each came from, what changed, the outputs,
  the comparisons. Anything can write to it through the same API: the built-in screen, a script, an agent, the
  CDE service, a custom screen.
- **The built-in screen** is one client of the record. It covers what a parameter list can describe.

So the rule for any request: *whatever happens must end as a run or an output in the record*. How it happened is
free. Customisation then climbs a ladder, and each rung is tried before the next:

1. **Data**: the parameter list (what to show and edit).
2. **Project checks**: `validate` and `save` (what is allowed, where it is written).
3. **Named actions**: the project lists actions with their own small parameter list ("dump the statistics of
   this block", "run in fast mode"). QA-Board shows a button and passes the request through. This is the remote
   call, and it is one generic call, not an API per feature. The answer comes back as outputs or a new run.
4. **Site widgets and site screens**, built into the site's web app, talking to the same record.

A request that needs state QA-Board can't see (modes of a live process, a multi-step dialogue) is rung 3 or 4,
never a new core concept. If rung 4 fills up for one project, that project has its own front end on QA-Board's
record, which is a fine outcome.

**Order.** Add lineage to the record (a run names the run it came from, through `qa` and the SDK) *before* the
single-run screen. It is small, generic, and it gives real sessions to design with, including sessions driven by
agents. The full order is in `ROADMAP-2027.md`.

**Cache and storage.**
- Start with reuse from the previous version of the same session. A shared cache across the organisation needs
  per-block hashing of inputs, settings, sources and files read: a separate project, not on this path.
- The long-lived simulator is one optional way to run. Nothing saved depends on it. The mock-up has
  **Recompute from scratch** to check a result that reused blocks.
- Block streams of versions nobody kept expire in days. Final outputs follow the session. Saved versions are
  ordinary CI results. Decide the numbers before the single-run screen ships.

**Staying a generic product.**
- Core words: parameters, steps (a project may call them blocks), streams, versions. Nothing about images.
- It works with no hook, and each hook adds something: parameter list → rich form; per-step report → timeline
  and logs; start-at-step → fast reruns; a connected tool → live use.
- Each step ships with a public example that is not an image pipeline, and docs for an outside reader.
- Out of scope: a debugger (breakpoints, stepping inside a step).

## The contracts

**Schema**: see recommendation 2. One hook, two callers: the Tuning tab calls it for the chosen tests'
configurations (the server already runs `qa batch --list` on the commit's build for that check), and `qa run`
stores it with each run, so the tweak screen shows the schema of the binary that made the run.

**Changes**: `{ "group.param": value }`. A run stores every change since the original in `extra_parameters`, and
its own step plus the version it came from in `data.tweak`. Config files are never edited by the server.

**Execution**: `execution = {start_at, inputs: {stream: path}, save_streams}`. Not part of the run's identity.
QA-Board decides when reuse is valid: same commit, and no change in a block before `start_at` (the schema gives
the order). The run reports per block whether it was reused or computed, how long it took, and where its log
section starts. External files the algorithms load are invisible to this check: a build fingerprint in the run's
metadata would at least show when they differ.

**Saving**: where (a new file, or in place through the `save` hook) and then what (send for review, or copy).
"Send for review" creates a branch from the session's commit with one commit, and a merge request.

## What the code has today

- Fits: `extra_parameters` and `qa --tuning` (flat keys, already used with `block.register` names); batches
  unique per commit and label; `outputs.data` for lineage; the tuning endpoint for launching; the batch check for
  calling a hook; local changes recorded for local runs; `search_space` of `qa optimize` for the Search rows.
- Fights: two paths to the same changes are the same run (identity includes `extra_parameters`), so a rerun is
  "attempt 2" and the history is not strictly a tree; launching is batch-sized and slow to start; retention goes
  by commit age, not per run; nothing knows about blocks.

## Layout decisions I'm least sure about

1. **Three ways to try a parameter, as a per-row choice.** The alternative is today's two separate forms (choose
   values / search). A per-row choice is simpler to explain, but "Search" on one row changes what the whole
   request does (a budget and an objective appear). It may need to be a page-level switch after all.
2. **History as a list that becomes a tree.** The mock-up has both views. The list hides structure, and the tree
   may confuse people who never start again from an older version. Worth watching in a user test.
3. **Save with two questions (where, then what).** The desktop app has one button. Defaults make it two clicks,
   but "the files where each value is set" may be more than IQV users want to see at all.

## Questions to ask

**IQV users (with the mock-up, one realistic task each):**
- When you tweak in CDE, how long does one change take to show? What would feel too slow?
- How many images do you look at while tweaking: one, or always the same few?
- What do you do after you find good values? Save a file, send values to someone, write them down?
- Do you ever go back to an earlier try and continue from there?
- Which block's log do you open, and when?

**The simulator's engineer:**
- Can the binary print every parameter with its type, range, default and description? Does the list change with
  the sensor or the configuration?
- How long are start-up and input loading, compared with one late block? (This decides whether a warm pool is
  enough or only a live process will do.)
- How common are parameters that depend on each other? Give five real examples: they test whether `validate` is
  enough.
- What would the live service do when its cache is lost or wrong? Can it always fall back to a cold run?
- Which external files do algorithms load, and can a fingerprint cover them?

**For you:**
- Is the tuning-form redesign (values as a table, start from a previous batch) going to merge? Phase 1 should be
  designed on top of it. That branch is not in this repository's remote, so I haven't read its code.
- Who may "send for review": anyone who can tune from the web app, or a smaller group?
- How long should session runs live before clean-up, and may QA-Board delete intermediates earlier than outputs?
