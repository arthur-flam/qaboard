# Plan: LLM evals, trials, and blind reviews by several people

Status: working plan, November 2026. Nothing here is on the public roadmap (`website/docs/roadmap.mdx`) yet: move
items there when they're ready to be announced. Neighbors: `docs/quality-ledger-plan.md` (noise, the gate, the
decision record), `docs/rfcs/python-sdk-runs.md` (runs from code, steps), `docs/design/logged-media.md`,
`docs/dag-pipelines-proposal.md`.

Two needs met in the same discussion, and they share most of their parts:

- **Teams building on LLMs** (products with prompts, tools and agents; teams that fine-tune, distill or quantize
  models) want their evals reviewed like the rest of the product. `examples/agent-evals` shows the start: tasks as
  inputs, transcripts diffed as text, pass rates gated.
- **Subjective validation** of image, video or audio quality between versions: people compare outputs without
  knowing which version made them, several people judge the same items, and a release needs enough of them to agree.

Both need *trials*, *scores aggregated over inputs with their uncertainty*, *blind comparison* and *several
reviewers*. An LLM judge is one more reviewer. We build these once.

## What the domain asks that QA-Board doesn't have
1. **Outputs are conversations.** Messages, tool calls and their results, reasoning, tokens and time per turn. A
   line diff of a text file misses the structure: which turn diverged first, which tool call changed.
2. **Outputs are stochastic.** One run per input says little. Eval harnesses run each sample several times (Inspect's
   *epochs*) and reduce: mean, at least k of n, all of n.
3. **The version is a tuple.** Model or checkpoint, system prompt, tool definitions, scaffold, sampling parameters,
   judge, dataset. The commit is one coordinate. Configurations and tuning parameters carry the others today; what's
   missing is comparing along one of them as easily as against a reference commit (roadmap: N-way comparisons).
4. **Graders are code under test.** Judges have prompts, versions and noise. People re-grade stored outputs without
   running the model again, and check judges against people.
5. **People are part of the measurement.** Blind, with several reviewers per item, their agreement measured, their
   decision recorded.

## Principles
- **Don't reinvent the harness.** Projects keep Inspect, promptfoo or their own code inside `run()`. QA-Board reads
  what they write, like it reads JUnit and benchmark files, and uses Inspect's words where it needs words: sample,
  epoch, scorer, score, messages.
- **A trial is declared, a rerun is not a sample.** `docs/quality-ledger-plan.md` says we compute nothing over
  reruns: people rerun after they changed something. That stays. A trial is a repeat the batch asked for, with the
  same code, configuration and input, and its index is part of the run's identity. Statistics are over declared
  trials only. This amends the ledger plan's "we don't run things N times and aggregate on top": for stochastic
  outputs we do, when asked to.
- **Blind means blind everywhere.** Names, colors, commit ids, parameters, file URLs, metrics: a reviewer who can
  find the side out will. Viewers already get the sides' names and colors as `labels`; a blind review gives them
  "A" and "B", swapped at random per item and per reviewer.
- **One reviewer model.** A person and an LLM judge both produce verdicts on items. Automatic ones are labeled as
  such and never count as a person's sign-off.
- **Not ours**: tracing production traffic, prompt playgrounds, a library of judges (`alternatives.mdx`).

## The shared parts

### Trials
- Declared per batch (`trials: 5` in a batch's definition, `qa batch --trials 5`), each run gets `trial: 0..n-1` in
  its identity next to input, configuration, parameters and platform. Runs that write several samples or epochs in
  one file (an Inspect log) keep them inside the file: the viewer picks.
- The Summary and Metrics Diff pair runs by input and configuration, then reduce over trials: mean, and for 0/1
  metrics "any" (pass@k) and "all". Names follow Inspect's reducers (`mean`, `max`, `at_least_k`, `pass_at_k`).
- Differences against the reference come with an interval: paired over inputs, clustered by input when there are
  trials. The gate's `max_regression` passes what's inside the interval and says so ("within noise", ledger step 3).
- Flaky inputs: those whose trials disagree, listed, like flaky tests in the JUnit viewer.
- Open: where trials live in the database (a column of `outputs`, or `data.trial` and an index); how `qa batch`
  resumes a batch with some trials done; whether runners run trials of one input in one job.

### Reviews
A review is a set of items (an input, and the outputs of 2 or more versions: commits, labels, milestones or tuning
configurations), a protocol, and reviewers.

| Protocol | The reviewer gives | Aggregated as |
|---|---|---|
| Pairwise preference | A, B, or no difference; optional strength and reason | Win rate with an interval (sign test); Bradley-Terry scores for more than 2 versions |
| Rating | A score per side on a scale or a rubric (sharpness, noise, color... or correctness, tone) | Mean opinion score and its interval, per criterion, per slice of inputs |
| Pass/fail with tags | A verdict and failure tags from a taxonomy the project grows | Rates per tag, new failures against the reference |

- **Blinding**: sides shown as A/B in an order drawn per item and reviewer, kept on the server, revealed when the
  review closes. The review page shows only the viewer and the form: no run card, badges, parameters or metrics.
  Files are served through a URL that doesn't name the commit or the output directory.
- **Several reviewers**: each item goes to k reviewers; the page shows progress per reviewer. Agreement per item and
  overall (Cohen's or Fleiss' kappa, Krippendorff's alpha for ratings); items where reviewers disagree are listed
  first when the review closes: they are what the team should discuss.
- **Sign-off**: a review can require "k of n validators approve". The result is the decision of the ledger
  (`quality-ledger-plan.md`, D): who, when, what they saw.
- **Judges**: `qa` posts an LLM judge's verdicts as a reviewer named after the judge and its version. The review's
  report shows the judge's agreement with people on the items both did.
- Built on: the image viewer's tiles and synced zoom, the audio viewer's A/B with matched loudness, the transcript
  viewer below, regions of interest for "where is the problem".

### Re-scoring
A scoring step over outputs that exist: new metrics for a batch without running it again, with the scorer's version
recorded. It is the first concrete use of `docs/dag-pipelines-proposal.md` (`needs:`). Until then, projects re-score
in a second batch that reads the first one's outputs.

## Roadmap
Each step is useful without the next, and small steps are one pull request.

### 1. Conversations read well
- **Transcript viewer** (this change): `webapp/src/viewers/transcript/`. Reads Inspect's JSON logs and samples, and
  chat messages in Inspect's, OpenAI's and Anthropic's shapes. Turns aligned with the reference's, first difference,
  word diff, tool calls with their results, reasoning and system prompt folded, scores with their explanations,
  tokens and time, search, an outline, keyboard. Standalone, with a playground.
- **Inspect's `.eval` files**: zip archives compressed with zstd, which browsers don't decompress. Either a WASM
  decoder loaded by the viewer, or the server extracts a sample on request. Until then: `inspect eval --log-format
  json` or `inspect log convert --to json`.
- **Metrics from an Inspect log** in Python (`qaboard/transcripts.py`, like `junit_metrics` and
  `benchmark_metrics`): each scorer's metric, tokens, time, turns, tool errors. Then `qa upload` reads them.
- **`examples/agent-evals` writes `transcript.json`** next to `transcript.txt`, and the use case page shows the
  viewer (needs a reseed of the demo, `docs/TODO.md`).
- **Prompt and tool definitions next to the conversation**: the system prompt's diff against the reference's is in
  the viewer already when it's a message; tool definitions come with Inspect's events (`events[].tools`).
- **A `qb.Transcript` kind for logged media**: sampled conversations at each step of a training or an RL run,
  on the timeline with the reward curve.

### 2. Trials
In the order of "Trials" above: the identity and `--trials`; reducing in the Summary and Metrics Diff; intervals and
the gate; flaky inputs. Design the storage with what SDK runs carry at steps (`python-sdk-runs.md`).

### 3. Reviews
1. Pairwise, blind, one reviewer, images: the smallest thing validation teams can try. A review's page, verdicts
   stored, the result as a table per input.
2. Several reviewers, assignment, agreement, the list of disagreements.
3. Ratings with rubrics; other viewers (audio, video, transcripts).
4. Sign-off rules, and the review's result as a check of the gate.
5. Judges as reviewers, and their agreement with people.

### 4. Many configurations at once
A matrix of inputs × versions (pass/fail or a metric as color), the inputs that flipped between two versions first.
Depends on N-way comparisons (roadmap). For prompt and model sweeps, quantization levels, checkpoints over steps.

### 5. Re-scoring, then analysis at scale
Re-scoring as above. Then: failure tags from reviews as filters everywhere; clustering failed transcripts; "what
changed in behavior" summaries through `qa mcp`'s evidence (`docs/rfcs/mcp-server.md`).

## Open questions
- Who the first users of reviews are, and their protocol today (spreadsheets? which scales?): write the first
  version for them.
- Do validators have QA-Board accounts, or do reviews need guest links?
- How many trials people can afford: it sizes the intervals, and whether the gate can use them in CI or nightly only.
- Inspect logs hold a whole eval in one file. One run per sample (QA-Board's shape, needs splitting the log) or one
  run per eval (the viewer picks the sample, metrics are the eval's)? The viewer handles both; the Python side
  should make the first easy.
