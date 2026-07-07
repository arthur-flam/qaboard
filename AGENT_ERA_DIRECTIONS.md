# QA-Board in the Age of Agents — Strategic Directions

Status: thinking memo, companion to `DOCS_OVERHAUL_OUTLINE.md`. Written July 2026.

## The reframe

QA-Board was built as a system of record for what *humans* tried: runs, metrics,
visualizations, tied to commits. Agents invert the economics that design assumed:

- **Runs become abundant and cheap.** An agent will happily produce 500 candidate
  changes overnight. The bottleneck moves from *doing experiments* to *evaluating and
  believing them*.
- **Trust becomes scarce.** Agents optimize whatever signal you give them, including
  by accident (Goodhart). The valuable artifact is no longer the run — it's the
  *verified claim*: "this change improves SNR +0.4dB on the held-out set, no texture
  regression, p<0.01, evidence attached."
- **The reader changes.** Agents don't need dashboards; they need APIs, schemas, and
  queryable history. Humans need dashboards *more* than before — but different ones:
  review queues and evidence, not run tables.

So the thesis: **stop thinking "experiment tracker," start thinking "verifier layer."**
Define what *better* means; let anything — a human, a Claude session, a ShinkaEvolve
loop — try to achieve it; verify the claims; keep the evidence forever. QA-Board is
unusually close to this already: it is commit-centric, batch-over-inputs,
metrics-vs-reference, with milestones as promotion gates. That's reward-function
infrastructure wearing a dashboard costume.

## Why the market moment is odd — and favorable

The neutral experiment-tracking market has effectively been liquidated:

- **wandb → CoreWeave** (May 2025, ~$1.4B): now a compute-vendor's productivity suite.
- **Neptune → OpenAI** (late 2025): not integrated — *shut down*; hosted users lost
  access March 2026. A whole user population got orphaned.
- **MLflow → Databricks' enterprise funnel**, pivoting hard to GenAI tracing.
- LLM-app eval/observability (Braintrust, LangSmith, W&B Weave, Arize) is crowded and
  well-funded — for *prompt/LLM-pipeline* evals.

Two spaces are conspicuously open:

1. **Neutral, self-hosted, you-own-your-eval-data.** Exactly the position Neptune's
   shutdown proves is both scarce and (as a standalone business) hard. As OSS
   infrastructure rather than a venture bet, it's viable — that's what QA-Board is.
2. **Quantitative verification of agent-written *code*** — perf, numerics, image
   quality, bit-exactness. The LLM-eval crowd measures *model outputs*; nobody owns
   "an agent changed your C++ ISP pipeline; should you merge it?" That is literally
   the SIRC use-case, about to go mainstream.

The lesson from ex-SIRC users who "never found something as nice": the moat was never
charts (wandb wins charts). It's the workflow — commit ⇄ batch of real inputs ⇄
per-input outputs and viewers ⇄ comparison vs reference. That workflow is what agent
verification needs.

## What agents actually need (vs what QA-Board has)

| Need | Why | QA-Board today |
|---|---|---|
| Machine-readable "better": metrics vs references over input sets | Agents are only as good as their reward signal | ✅ core model (batches, metrics, reference branch, milestones) |
| Clean programmatic surface: submit run → get scores; schemas; OpenAPI/MCP | Agents can't click | ⚠️ `qa` CLI is decent; HTTP API is undocumented/inconsistent (see outline §4) |
| Caching / content-addressed results | Agents re-run near-identical evals constantly; latency is the iteration bottleneck | ⚠️ bit-accuracy manifests are content-addressed hashing — generalize into a memoization layer |
| Multi-fidelity evals: smoke → discriminative-subset → full → holdout | Lets agents climb cheaply, spend on finalists | ❌ (batches exist; no staging/promotion policy) |
| Queryable history incl. **negative results** | "Has anything like this been tried?" is the highest-value question; agents can finally record failures diligently | ⚠️ data exists in Postgres; no search/summarization surface |
| Provenance: run ↔ commit ↔ agent session/transcript ↔ human sponsor | Tomorrow's "who ran this?" is "which agent, under whose authority, reasoning attached" | ❌ user field only |
| Budgets, quotas, audit, sandboxing | Agents burn compute; someone is accountable | ❌ |
| Anti-Goodhart: hidden holdouts, canary inputs, significance testing, metric rotation | Agents *will* overfit your metric | ❌ — and nobody else does this either |
| Approval gates: agent proposes, human promotes | The human layer needs leverage points, not surveillance | ⚠️ milestones are exactly this primitive, unmarketed |

## Directions, with pros and cons

### A. Agent-native interface: MCP server + clean API (do first, smallest lift)

`qaboard-mcp`: expose `run_batch`, `get_metrics`, `compare_to_reference`,
`list_experiments`, `search_history`, `wait_for_output`, `get_output_files` as MCP
tools over the existing CLI/API. Every coding agent (Claude Code, Cursor, Devin,
in-house harnesses) can then use a QA-Board instance as its eval oracle.

- **Pro:** cheap (days-weeks); rides the whole agent ecosystem's growth; forces the
  API cleanup already listed in the docs outline (OpenAPI, auth, error envelope —
  now prerequisites, not chores). Immediate dogfood story.
- **Con:** an interface, not a differentiator by itself; security work (auth,
  the SSRF proxies, unauthenticated DELETEs) becomes urgent because you're inviting
  automated clients.
- **Verdict:** unconditional. Everything else builds on it.

### B. The merge gate for agent PRs ("CI for agent-written code")

Positioning + a thin product layer: every agent PR gets a QA-Board check — perf
(built-in hyperfine-style repeated timing with confidence intervals), quality metrics
vs reference, bit-accuracy, image diffs — posted as a commit status with a link to
evidence. GitHub-side gaps are already scoped in `docs/github-support-roadmap.md`
(commit status, Actions dispatch, webhook signatures).

- **Pro:** QA-Board's commit-centricity — the thing that made it *unlike* wandb — is
  exactly the right shape here; nobody owns this; the marketing story writes itself
  ("you wouldn't merge an agent's PR on green unit tests alone").
- **Con:** requires finishing GitHub support; overlaps with CI vendors if they wake up
  to it; "perf gate" alone felt niche for humans — but agents multiply PR volume by
  10–100×, which is what turns a niche gate into default infrastructure.
- **Verdict:** the strongest *positioning*. The perf instinct wasn't wrong, it was early.

### C. Evolution host: be the backend for ShinkaEvolve / OpenEvolve / AlphaEvolve-likes

Evolutionary code optimization needs exactly: a fitness function (batch + metrics), a
program database with lineage, populations/islands, and budget control. QA-Board has
the first and half the second. Add: parent-run pointers (lineage), a population/Pareto
view, `qa evolve` wrapping ShinkaEvolve/OpenEvolve with QA-Board as eval + tracking.
ShinkaEvolve (ICLR 2026, active, now supports headless CLI-backed mutation via
subscription agents) needs a serious tracking/eval substrate — currently everyone
rolls their own JSON files.

- **Pro:** perfect technical fit; `qa optimize` (scikit-optimize) is the primitive
  ancestor of this; dogfoodable on QA-Board's own backend perf; a genuinely new
  category ("evolution runs" as first-class objects) rather than catch-up.
- **Con:** research-adjacent market today; frameworks churn; requires eval-latency
  work (caching, multi-fidelity) to be pleasant.
- **Verdict:** the most *creative* bet, medium cost. Do after A, alongside B.

### D. wandb-style SDK + MLflow compat shim

The underrated argument isn't human familiarity — it's **training-data compatibility**:
every coding model has `wandb.log()` and `mlflow.log_metric()` burned into its weights.
An agent dropped into a repo will emit those calls zero-shot. A `qaboard.wandb` shim
and an MLflow-tracking-REST-subset endpoint make QA-Board a drop-in sink for code
agents write *without being told about QA-Board*.

- **Pro:** cheap adoption wedge; Neptune's shutdown created displaced self-hosters
  looking for a landing spot; complements the WIP wandb-style API thread.
- **Con:** compat layers are leaky and a treadmill; risks re-framing QA-Board as
  "a worse wandb" — the exact comparison to avoid; MLflow's surface is large.
- **Verdict:** yes, but strictly *ingestion-only* (log runs/metrics/artifacts), never
  chase dashboard parity. Convergence plots and multi-run comparison charts are
  table stakes worth adding once, not a war worth fighting.

### E. The trust layer: eval integrity as a product (most differentiated)

Features nobody ships today, all natural extensions of the batch/milestone model:

- **Hidden holdout batches** — inputs the agent can query aggregate scores on but
  never enumerate or overfit deliberately; server-side split enforcement.
- **Canary inputs & metric rotation** — detect reward hacking automatically.
- **Statistical guards** — repeated runs, confidence intervals, multiple-comparison
  correction before a "regression"/"improvement" label is granted (hyperfine-thinking,
  generalized beyond timing).
- **Claim objects** — a first-class record: hypothesis, evidence runs, significance,
  reviewer, decision. The unit of record shifts from *run* to *claim*.
- **Human spot-check queue** — sampled agent results routed for review; milestones
  become "promoted claims."

- **Pro:** this is where the puck is going — when agents run the experiments, the
  humans' job *is* auditing; deeply dogfoodable; hard for LLM-observability vendors
  to reach because it requires owning the eval substrate.
- **Con:** novel = education cost; needs A + parts of C to matter; no one is asking
  for it by name yet.
- **Verdict:** the long-term identity. Start with one feature (holdout batches or
  statistical guards) and let it name the category.

### F. The human-above-agents UX: campaign view

If humans supervise N agents, the UI unit isn't a run — it's a **campaign**: goal,
budget, hypotheses tried (agent-written summaries), Pareto front of candidates,
decisions pending. Runs get generated narratives; the dashboard becomes a *review*
tool. "Manager view for AI-led research."

- **Pro:** directly answers "how will researchers keep track when agents run
  everything"; reuses batch/commit plumbing; the demo that makes the whole strategy
  legible.
- **Con:** frontend-heavy; design risk; premature until A–C generate real agent
  traffic to summarize.
- **Verdict:** phase 2. Prototype on one internal campaign first.

### G. Rich traces (robotics/lidar/rerun): adopt, don't build

The instinct is right that these platforms are cool and that dogfooding is destiny —
which is the argument *against* building it without a resident use-case. rerun is
open-source and embeddable: add `.rrd` as a visualization type next to IIIF images
and Plotly, and QA-Board gets robotics-grade trace viewing for a plugin's cost.

- **Verdict:** integration, not direction.

## What NOT to do

- **Don't compete with wandb on training telemetry.** CoreWeave-subsidized, mature,
  and the ex-SIRC nostalgia isn't about loss curves anyway.
- **Don't pivot to LLM-app observability.** Braintrust/LangSmith/Weave/Arize own it;
  it's the one eval market that is *over*-served.
- **Don't build hosted SaaS first.** The graveyard (Neptune included) says standalone
  neutral trackers don't survive as companies. As OSS infrastructure with a hard niche
  (hardware-adjacent verification) QA-Board doesn't need to be a company to matter —
  and self-hosted is the feature, given who now owns the alternatives.

## On "humans a layer above — for how long?"

Wrong question to bet on, and the bet doesn't require answering it. Whether the
reviewer is a human, a stronger model, or an audit agent, *someone* needs a durable,
trusted, queryable record of what was claimed, what evidence supports it, and who
authorized it. Dashboards are for whoever is accountable; APIs are for whoever works.
Bet on the substrate — verification, provenance, evidence retention — which appreciates
under every scenario, including full autonomy (an agent-only lab needs the trust anchor
*more*, not less). The layer that gets automated away is run-*launching*, and that was
never the moat.

## Sequencing

1. **Now:** API security + OpenAPI (outline §4) → `qaboard-mcp` (A). Dogfood: agents
   fix QA-Board's own quick-wins list, gated by QA-Board runs.
2. **Next:** GitHub commit-status/checks + repeated-run statistical timing → the agent
   merge gate (B). Ship the wandb/MLflow ingestion shim (D-lite) with the WIP SDK.
3. **Then:** lineage + `qa evolve` with ShinkaEvolve/OpenEvolve (C); first trust
   feature (holdout batches or significance guards) (E).
4. **Later:** campaign view (F); rerun embed when a real trace use-case shows up (G).

The one-line pitch at the end of it: *QA-Board — define what better means, let anyone
or anything try to achieve it, and believe the results.*
