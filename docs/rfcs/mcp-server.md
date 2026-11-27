# RFC: an MCP server for QA-Board (`qa mcp`)

Status: a first version is implemented, November 2026, as experimental: see "What ships" at the end. Owner: the
maintainers. Related: the roadmap's "Agents" section (`website/docs/roadmap.mdx`), item F of
`docs/quality-ledger-plan.md` (an evidence pack), the skills in `skills/`, `website/docs/agents.mdx`.

## Why, and why not just the HTTP API
Agents already read QA-Board without MCP: the `qaboard-results` skill teaches `qaboard.runs(url)`, `qa runs --json`,
`qa gate --json` and the compare API, and the OpenAPI document describes the rest. For a coding agent in a terminal
that can run Python and curl, wrapping those endpoints as MCP tools would add little: a token is needed either way,
and agents call HTTP APIs well. **We don't wrap the API.** No CRUD tools, no admin, no tool per endpoint.

MCP earns its place where the HTTP API leaves the agent (or the person) with work it does badly or can't do:

1. **Answers shaped for a task, not for a page.** The compare endpoint returns every test of a batch (thousands of
   rows); an agent that asks "did my change help?" needs the verdict, the metrics that moved, the five worst
   regressions, the new failures and a link. Ranking, grouping and cutting is where agents make mistakes and burn
   context. The tools do it once, the same way for everyone.
2. **Outputs the model can look at.** A tool result can carry images. QA-Board's outputs are often 16-bit TIFFs,
   RAW or DNG files, 50 MP: an agent can't read them, and downloading them to decode them is slow and error-prone.
   The server already has what's needed: the IIIF image servers crop, scale and convert any image the web app shows,
   and `POST /api/v1/output/image/diff` finds where two images differ. A tool returns small PNG crops, new next to
   reference, of the places that changed. Text outputs come back as a diff with the reference's.
3. **People who don't run code.** Managers and algorithm engineers ask "how did the nightly do this week?" in Claude
   Desktop, claude.ai, VS Code's chat or Microsoft 365 Copilot. They have no shell: MCP is their only way in.
4. **Charts and comparisons in the conversation (MCP Apps).** Since January 2026 a tool can declare a `ui://` HTML
   resource that hosts render in a sandboxed iframe next to the answer (Claude, Claude Desktop, VS Code GitHub
   Copilot, Microsoft 365 Copilot, Goose...). The app gets the tool's result, can call the server's tools, open
   links, and post a message to the chat. That's the "ask Claude about the nightly and get a chart" workflow some
   teams build by hand against their database, with QA-Board's numbers (the same medians as the web app's) and links
   back to the pages that show the outputs.
5. **Links for humans.** An agent that wants to convince a person should hand over the page that shows the
   evidence: the Metrics Diff against the reference, the history of a metric, the image viewer on a test. Every tool
   answer carries those links; agents can't guess the web app's URL scheme.
6. **Packaged workflows.** MCP prompts (slash commands in Claude Code) ship the review procedure with the server:
   "did my change help", "review this batch", "nightly report".

What doesn't change: the skills stay (they teach the integration, which no MCP tool does), `qa gate` stays the
verdict CI trusts, and the HTTP API stays the integration surface for scripts.

## Who it's for, and how they'd use it
| Who | Client | Story |
|---|---|---|
| A coding agent changing an algorithm | Claude Code, Codex, Cursor (terminal or IDE) | Pushes, runs `qa batch --commit`, then asks `compare` and `failures`, looks at the worst regressions' outputs, and reports to the person with the Metrics Diff link. Monitors its own progress over its experiments with `metric_history`. |
| A person reviewing an agent's (or a colleague's) change | Claude Desktop, VS Code chat | "Is this commit better than main? Show me where the images changed." Gets the comparison table, the crops and links, in the chat. |
| A team lead | Claude Desktop, claude.ai, M365 Copilot | "How did the nightly do this week, which metrics drifted?" Gets a chart with the step changes and the commits that caused them. |

Terminal clients (Claude Code's CLI) don't render MCP Apps: they get the text answer and the links. Images in tool
results do reach the model in Claude Code.

## Design principles
- **Read-only first.** Every tool is annotated `readOnlyHint`. Starting batches stays with `qa batch --commit`
  (needs the `act` scope, and a person should see what an agent starts). Writes come later, with a confirmation
  (MCP's multi-round-trip requests, 2026-07-28 spec).
- **Same numbers as the web app.** Tools call the server's endpoints (`/compare`, `/commits`, `/output/<id>`...);
  medians, matching of reference runs and metric directions are the server's, as in the Summary and Metrics Diff.
- **Text for the model, structure for the UI.** `content` is compact Markdown the model reads; `structuredContent`
  holds the data the app renders (per the MCP Apps guidance it's not meant for the model's context). Lists are cut
  (top N), and the text says what was left out and how to get it (`runs_table`, the link).
- **Honest evidence.** Say when a comparison is partial (status running/missing, reference mismatch), when runs
  passed only after reruns, and how many tests moved each way (a sign test), so a median that moved by noise isn't
  sold as an improvement. QA-Board has no noise floor per metric yet (roadmap): the tools don't pretend to.
- **The person's credentials, no new ones.** `qa mcp` uses what `qa` uses (`qa auth login`, `QABOARD_TOKEN`). A
  read-only token (web app → Settings → Tokens, scope `read`) is enough and recommended for agents.
- **Untrusted text stays data.** Logs, commit messages, input names and metric names come from code and people the
  reader doesn't control. Tool answers quote them in fenced blocks and never tell the model to act on them; the
  server instructions remind the model of it. This doesn't make prompt injection impossible: it's why the tools are
  read-only.

## Tools (first version)
| Tool | For the task | Built on |
|---|---|---|
| `find_results` | Orient: projects, recent commits with results (a branch, a search), their batches and run counts, milestones. | `/projects`, `/commits`, `/milestones` |
| `compare` | "Is this commit better than the reference?" Per metric: medians, change, direction, tests better/worse (sign test); new failures; worst regressions and best improvements; reruns; links. Takes a web app URL as is. | `/commit/<sha>/compare`, `/commit/<sha>` |
| `failures` | "Why did runs fail?" Failed runs grouped by the error at the end of their logs, with an excerpt, and whether the same test failed in the reference. | `/compare`, `/commit/<sha>`, `log.txt` tails |
| `look_at_output` | "Show me what changed." An image output (any format the image servers read) as a PNG, next to the reference's, or crops of where they differ most; benchmarks, flame graphs, series, JUnit reports, Plotly figures and logged media compared with the reference's by their format (`evidence_outputs.py`, with the parsers the web app's formats share; types recognized like the web app's viewers: qaboard.yaml's visualizations, then file names); a text output as a diff with the reference's; without a path, the run's files and their types. | `/output/<id>`, `/output/<id>/manifest`, `/project`, IIIF, `/output/image/diff` |
| `metric_history` | "How did the nightly do?" / "Am I making progress?" Medians per commit on a branch for a batch label, the biggest steps and the commits that made them. | `/commits?metrics=` (the History page's numbers) |
| `runs_table` | Ad-hoc analysis: one row per run (input, configs, parameters, metrics, status), as CSV, for the agent to compute or plot what the tools don't. | `/commit/<sha>` |
| `check_gate` | The project's gate verdict on a commit, when the server runs in a project checkout. | `qa gate --json` |

Prompts: `did_my_change_help`, `review_results`, `nightly_report`.

App: one `ui://qaboard/results.html` resource, bound to `compare`, `metric_history` and `look_at_output`: a metrics
table with the regressions, a line chart with the step changes, images side by side. Clicking opens the web app page
(`ui/open-link`); "Ask about this" posts a question to the chat (`ui/message`); a click on a test asks the server
for its images (`tools/call`). Self-contained HTML (no CDN: QA-Board runs in air-gapped networks), host theme
variables when given.

## Deployment
1. **Local, over stdio: `qa mcp`** (this RFC). The client starts it; it reaches whatever server `qa` reaches,
   including servers inside a company network, with the person's login. Works with Claude Code, Claude Desktop, VS
   Code, Cursor, Codex. Optional dependency: `pip install "qaboard[mcp]"`.
2. **Remote, Streamable HTTP: `qa mcp --http`**, served at `<QA-Board's URL>/mcp` (`qaboard/mcp_http.py`). For
   clients that only take remote servers (Open WebUI, claude.ai, M365 Copilot) and for zero-install. The same tools
   and prompts (`build_server` in `mcp_server.py`), but `check_gate`, which runs `qa gate` in a checkout.
   - **Where it runs**: its own process, from the backend's image (the backend depends on `qaboard[mcp]`), next to
     the backend: a profile of docker-compose.yml (`COMPOSE_PROFILES=mcp`, service `mcp`) and `mcp.enabled` in the
     Helm chart. Not in the backend's uWSGI workers: the SDK is ASGI (Starlette, uvicorn), and tool calls wait on the
     API for seconds. It calls QA-Board through the proxy (`--server http://proxy`), which serves the API and the
     runs' files and images; `--public-url` is what users and clients see, for links and the OAuth metadata.
   - **Stateless** (`stateless_http`, the 2026-07-28 spec): no sessions, any replica answers any request.
   - **OAuth protected resource** (RFC 9728), QA-Board being the authorization server: `/.well-known/oauth-protected-
     resource/mcp` (and without `/mcp`, which some clients try) says resource `<public URL>/mcp`, authorization server
     `<public URL>`, scope `read`; a request without a good token gets a 401 whose `WWW-Authenticate` points there.
     Clients then read QA-Board's RFC 8414 metadata (`/.well-known/oauth-authorization-server`, the backend) and get a
     token for each person. Tokens are ordinary API tokens (kind `oauth`, scope `read`), not bound to an audience.
   - **Tokens**: the server checks one with `GET /api/v1/user/me/` (it must be authenticated and have `read`) and
     remembers the answer for a minute, by the token's hash. Each tool call builds its `evidence.Api` with the
     caller's token, so QA-Board checks every request it makes, and the answer is what that person may read;
     revoking a token takes effect at once. Never the machine's credentials (`qa auth login`, QABOARD_TOKEN): a call
     without a token is an error, and URLs of other servers are refused instead of using saved logins.
   - **Behind the proxy**: nginx routes `/mcp` and `/.well-known/oauth-protected-resource[/mcp]` to it, without
     buffering (answers stream as Server-Sent Events, with keep-alive pings). In compose, nginx looks the `mcp`
     service up when requests come (Docker's DNS), so the proxy starts without it. The SDK's DNS rebinding checks are
     off: every request needs a token, and the Host is the public one.
   - Still needed for clients in a vendor's cloud (claude.ai, M365 Copilot): the server must be reachable from there,
     which on-premise deployments usually aren't; and Client ID Metadata Documents on the authorization server.

## Gaps found on the way, and what this PR does about them
| Gap | Done here? |
|---|---|
| `POST /api/v1/output/image/diff` computes and caches, changes nothing, but tokens needed the `act` scope: read-only tokens were refused. | Yes: it's a read now. |
| `GET /api/v1/output/<id>` didn't say which project, commit and batch a run belongs to: from a run id alone, nothing could find its reference. | Yes: `project`, `commit_id`, `batch_label`, `batch_id`. |
| No API answer has web app links; agents can't guess them. | In the MCP tools. Server-side links later. |
| No grouping of failures; logs only as files. | In `failures`. |
| No noise floor per metric: nothing says whether a change is real. | No: roadmap ("A noise floor per metric"). The tools give the sign test meanwhile. |
| No record of which runs an agent started, and why. | No: roadmap ("Runs labeled by agents"). Docs suggest `qa --label agent/<topic>`. |
| JSON from every `qa` command. | No: roadmap. `qa runs --json`, `qa gate --json` exist. |
| Starting batches, waiting for them (MCP Tasks extension). | No: later, with a confirmation. |
| A remote endpoint with OAuth. | Later, in its own change: `qa mcp --http` (see "Deployment"). |

## Open questions
- Should `compare` also run the project's gate rules (owners' rules from the web app) without a checkout? The compare
  answer has `project_gate`; evaluating it needs `qaboard/gate.py`'s logic without a qaboard.yaml.
- Image crops: PNG at most 1024 px. Models read 8-bit images; HDR/16-bit outputs are tone-mapped by the image
  server, which can hide differences a person would see with the viewer's levels. Say so in the answer.
- Clients cap tool results (Claude Code warns above ~25k tokens): keep answers under ~8k tokens by default.

## What ships (November 2026)
- `qa mcp` (`qaboard/mcp_server.py`), the tools, prompts and app above, experimental. Tests: `tests/test_mcp_server.py`
  (against `tests/fake_qaboard.py`, whose answers were checked against the backend's handlers), and the app in a host
  that uses the official AppBridge (`tests/mcp_app/`, by hand).
- Backend: the two gaps marked "Yes" above.
- Docs: `website/docs/mcp.mdx`, the roadmap item moved to Experimental, the `qaboard-results` skill mentions it.

Two reviews (the tools as an agent sees them; correctness and security against the real backend) changed, before
merging: the app escapes everything from results and only listens to its host; the token only goes to the QA-Board
server's origin, and URLs of other servers need a `qa auth login` there; `compare` says which failures are new, how
many tests each count covers, the median change per test, and "consistently better/worse, mixed, too few tests"
(sign test corrected for the metrics tested) instead of a verdict on medians; an agent's label is compared with the
reference's CI runs when the reference has none; without a commit, the checkout's or the latest, said in the answer;
logs are readable (they aren't in manifests); crops never upscale; `metric_history` asks for enough commits, shows the
typical step and flags steps where runs changed; unknown metrics are errors; lists in `data` are cut.

## Left for later
- `runs_table` with the reference's values (`with_reference`), so agents don't join two tables.
- `metric_history`: the reference's median as a baseline, and a `url` argument. The History link can't select metrics
  (the page doesn't read `selected_metrics`).
- `compare` and `failures` apply a URL's filter (today they say it isn't applied).
- The gate evaluated on the server, with project and commit (`check_gate` is local only, it needs a checkout): needed
  for the remote server.
- Names: the tools say `label` like `qa gate --label`, URLs and the API say `batch`. Decide before leaving experimental.
- A redone run counts as failed and pending in the batches' counts (`find_results`).
