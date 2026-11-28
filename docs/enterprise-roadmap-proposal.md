# Proposal: being ready for other sites, and the roadmap around it

Status: proposal, 2026-11-28. Written from the code, not from the public roadmap: each "built" below was checked in
`backend/backend/`. Neighbors: `docs/design/user-management.md` (UM), `docs/design/http-upload.md`,
`docs/design/inputs-and-collections.md`, `docs/evals-and-reviews-plan.md`, `docs/quality-ledger-plan.md`, PR #276
(tuning sessions).

## The short version

Sign-in and access are already in good shape for another company: LDAP, SAML and OIDC side by side, groups from
claims, three roles inherited down project paths, scoped tokens that expire, service accounts, CI OIDC trust, and an
exportable audit log. **What another site would hit first is somewhere else:**

1. **Projects have no lifecycle.** A project is created by the first `qa` ingest or the first git webhook, by anyone
   who can ingest. With the defaults (`public`, `signed_in_role: developer`), that's anyone signed in. There's no
   create API, no delete, no owner by construction, and no namespaces or orgs (UM M4, not started).
2. **There are no quotas at all.** We measure storage per batch and per user (`storage_usage.py`, nightly) but never
   refuse anything. The only limits are 1 GiB per uploaded file and a 1 GB free-space floor.
3. **Site config lives in this repo.** Python site hooks are already entry points, and the Helm chart takes any
   values file. Compose, the backend image build and `.gitlab-ci.yml` still assume `deployments/<site>/`.
   `services/` also has SIRC data (passwd files, LDAP config, SAML settings, HW_ALG rules in `clean.py`).
4. **Operators can't watch it.** We have Sentry only: no Prometheus `/metrics`, no OpenTelemetry. Redis and RabbitMQ
   run as single pods. PostgreSQL 12 and RabbitMQ 3 are end-of-life. A platform team at another company will ask
   about all three in the first meeting.

Everything else on the enterprise list (SCIM, MFA, audit streaming, per-user git tokens) is real, but it comes
after these four.

## Week plan: next week

Five working days with agents doing most of the typing. Ordered so that each item ships alone. Items A-C are the
week. D is a stretch goal. E is decisions for you.

### A. Site config from outside the repo (1-1.5 days)
Smallest item, and it unblocks hiding SIRC/DSK specifics. The plan is already in `docs/known-issues.md`.
- `deploy.py`: `--site-dir` / `QABOARD_SITE_DIR`. It's exported so overlays can use `${QABOARD_SITE_DIR}/...`.
  `.deploy-state.*.json` and `.env` are resolved next to the env file, not the repo root.
- `backend/Dockerfile`: a named build context `deployments` (`additional_contexts`), defaulting to `./deployments`
  so nothing changes for whoever doesn't set it.
- Website build: `site-docs` from the same directory.
- Tests that read SIRC files skip when they're absent. `deployments/example.yml` becomes a full template site,
  tested in CI (the `quickstart` job), so we notice when the template drifts.
- Move SIRC data out of `services/` (passwd, LDAP, SAML settings) into the overlay. HW_ALG cleanup rules become
  site config or a `qaboard.hooks` entry point.
- **Not this week:** actually moving `deployments/sirc` to a private repo. Do that once the mechanism has deployed
  once at SIRC from an external path.

### B. Projects you create, with owners (2-2.5 days)
A first slice of UM M4, **without namespaces**. Namespaces are a bigger migration, and prefix grants on the
instance already stand in for orgs today.
- **`projects.create` setting**: `anywhere` (today, the default on upgrade), `owners-of-prefix` (you can create
  `a/b/c` if you're an owner of `a/` or `a/b`, through a prefix grant), `admins`. Ingest to an unknown project
  under a stricter policy is refused with a new `Reason` code and a message that says how to get it created.
- **Create explicitly**: `POST /api/v1/projects` (id, visibility, initial owners and groups, optional repository
  link), a "New project" dialog in the web app, `qa project create`. The creator becomes owner. Audit `project.create`.
- **Repository links** (`project_repos`): store which repo(s) a project gets results from. Webhooks look up
  projects through it instead of the id, so a project's id no longer has to equal its repo path. Existing projects
  get a row from their current id.
- **Archive, then delete**: archive hides a project and refuses ingest. Delete is admin-only, after archiving, and
  goes through the existing hard-delete job. Both are audited.
- **Defaults for new installs**: `default_visibility: internal`, `signed_in_role: viewer`,
  `projects.create: owners-of-prefix`. SIRC keeps today's values explicitly in its settings.
- Docs: `website/docs/backend-admin/project-access.mdx`, a user-guide page, release notes, and `skills/` (a new `qa`
  command).

### C. Storage quotas, and refusing runs (1.5-2 days)
The honest constraint is that QA-Board can't stop someone from writing to an NFS share. What it can do is refuse to
*start* or *register* a run. Combined with the nightly measurement, that's enough for a budget that people respect.
- **Budgets per project prefix**: soft and hard, in bytes, set by admins (Admin → Storage), stored in a table, not
  in qaboard.yaml (users would raise their own). Optionally per user within a project later.
- **Usage**: what `storage_usage` already computes, plus bytes uploaded since the last rebuild, so a burst within a
  day still counts.
- **Enforcement, at three points, one function** (`quotas.check(project, user, expected_bytes=None)`):
  1. `POST /api/v1/output/` (every run, whether it writes over HTTP or to shared storage): over the hard budget,
     answer 507 with a `quota_exceeded` `Reason`. Over the soft budget, accept with a warning that `qa` prints.
  2. `qa run`/`qa batch` ask before running, so a 500-job batch isn't refused job by job after the cluster has
     already scheduled it.
  3. HTTP uploads: the same check per file, replacing the bare free-space floor.
- **The escape hatch**: owners can always delete runs to get back under budget (Storage usage already shows the
  biggest folders). Admins can grant a temporary raise, which is audited. Milestones and the reference branch don't
  count against the budget. Otherwise people end up deleting the references.
- **Where a site has filesystem quotas** (XFS project quotas, NetApp qtrees): a `qaboard.hooks` entry point that
  reports a folder's usage and limit, so the check uses the real number. It replaces the hardcoded URL in
  `scripts/user_storage_server.py`.
- **Not this week:** retention policies edited in the web app (they're still in qaboard.yaml), rate limits per
  token, inode limits.

### D. Stretch: Prometheus metrics (0.5-1 day)
`/metrics` on the backend (requests, latency per endpoint, jobs queue depth and failures, ingest bytes, runs per
status), a `ServiceMonitor` in the chart behind a value, and one example Grafana dashboard in `deployments/`.
Cheap, and it changes how a platform team reads the project.

### E. Decisions for you before Monday
1. **Namespaces now or later?** I recommend later (above). The cost is that "orgs" stay prefix grants, with no
   org page and no "create an organization" request.
2. **Should the quota cover shared-storage runs or only uploads?** I recommend both, enforced at run creation as
   above. Refusing shared-storage runs changes daily life at SIRC, so ship it with `soft` only there first.
3. **Should new installs get stricter defaults** (internal, viewer, owners-of-prefix)? I recommend yes. Another
   company's security review will reject `public` + `developer` for everyone.

## What enterprise teams like in W&B (and similar tools) that we don't have

A comparison with W&B, MLflow/Databricks, Comet and Neptune, as they are usually deployed in companies. Some of
these we should skip on purpose.

| Feature | Them | Us | Worth it? |
|---|---|---|---|
| Teams/orgs with a page, members, and projects created inside them | Core | Prefix grants, no page | **Yes**: M4 namespaces, after B |
| Explicit project creation, archive, delete | Core | Implicit only | **Yes**: B |
| Storage quotas and usage per team, with alerts | Yes (SaaS and dedicated) | Usage only | **Yes**: C |
| TTL / retention policies on artifacts, set in the UI | W&B artifact TTL | qaboard.yaml + admin schedules | Yes: after C |
| Bring your own bucket (S3/GCS/Azure), presigned uploads | W&B BYOB, MLflow | Shared FS or mounted object storage | **Yes for other sites**. Many won't have a big NFS. Today's design says "not planned": reconsider |
| SCIM, group → role mapping | Yes | Groups from claims, no SCIM | Medium: claims cover most cases |
| Service accounts per team | Yes | Per instance or project | Small, comes with namespaces |
| Audit log streamed to SIEM | Yes | Export only, manual purge | Small: a scheduled purge job and a JSONL webhook/syslog sink |
| Reports: a narrative page with live panels, shared and commented | W&B Reports | Shared URLs, milestones with notes | **Yes**. It's how reviews get written down. It fits the ledger's "decision record" |
| Saved, shared workspaces/views | Yes | On our roadmap | Yes: "Saved comparison views" |
| Artifact lineage graph (dataset → model → eval) | W&B Artifacts | Collections versioned. Lineage only in PR #276 | Yes, as part of data management |
| Model registry with stages and approvals | W&B, MLflow | Milestones | Partly: "approve as reference" covers our use. Don't build a registry |
| Alerts to Slack/Teams | Yes | Email when a batch ends | Yes: already ledger step 2 |
| Human annotation queues and scoring | W&B Weave, Langfuse | Planned (reviews) | **Yes**: see below. It's our domain advantage on images |
| Status page, Prometheus, SLOs for the server | Dedicated cloud | Sentry | Yes: D |
| Custom roles | Enterprise tier | Rejected on purpose (UM principle 6) | No. Three roles are a feature |
| Hosted SaaS, billing, plans | Yes | Out of scope | No |

## The roadmap, by theme

The order within each theme is the order I'd build. Each line is roughly one PR.

### 1. Run it at another company
1. Site config outside the repo (A).
2. Projects lifecycle (B), then namespaces and orgs (rest of M4), then SCIM (M6).
3. Quotas (C), then retention in the web app, then per-token rate limits (http-upload §6.3).
4. Observability (D). Then Redis/RabbitMQ HA in the chart, and the PostgreSQL 16 / RabbitMQ 4 upgrades. Those
   are the warts a new site sees in its first review.
5. Files served from their own host name (Security warts). It's the one security item an outside pen test will
   find.
6. Object storage as a first-class backend: presigned PUT/GET, BYOB. It's the biggest of these. Do it only once a
   second site confirms it has no shared filesystem. Ask before designing.
7. An "installing at your company" guide: a sizing table, the security checklist (Admin → Overview has one), the
   upgrade policy, and backups of the *results* (today only the database is covered).

### 2. Data management
Rollout 1 of inputs and collections shipped. Next, in the design's order:
1. Annotations and crops (rollouts 2-3). Reviews also need them for "where is the problem".
2. Kept outputs, labels and drafts (rollout 4): outputs that outlive retention because someone marked them.
3. Lineage: a run names the run it came from, and the collection version it read. It's the same small addition
   as tuning sessions' step 3 (PR #276). Build it once for both.
4. Datasets by content, not path (hashes per input). It isn't planned anywhere. Say so explicitly, or decide it's
   needed: it's what lets two sites share a collection.
5. Imports (rollout 5) from what teams use today: folders, CSV manifests, COCO/FiftyOne.

### 3. Subjective evaluation: trials, votes and scores
The plan is `docs/evals-and-reviews-plan.md`. It's generic enough to sell in many domains (camera IQ, audio, video,
LLM outputs). My one push: **start with step 3.1 (blind pairwise review of images, one reviewer) before trials.**
It's the smallest thing a validation team can use. It needs no new run identity, and it answers the plan's
open question about who the first users are.
1. Blind pairwise on images, a review page, verdicts stored, a table per input.
2. Several reviewers, assignment, agreement, and disagreements first.
3. Guest reviewers (links with a scope and an expiry, on top of the existing tokens). Validators and outside
   panels often won't have accounts. This is also an enterprise question: decide it with B.
4. Ratings and rubrics (MOS), then audio and video, then transcripts.
5. Trials and intervals in the Summary and the gate.
6. Sign-off rules as a gate check. LLM judges as reviewers.

### 4. Interactive tuning (PR #276)
The PR's 7 steps are sound, and its "a cache miss may be slower, never different" rule is the right one. Comments:
- **Steps 1-3 are generic and cheap.** Merge the tuning form, add the `parameters` hook, add lineage. They help
  every project and don't depend on the simulator team.
- **Step 4 is gated on a number nobody has measured yet**: start-up plus input loading versus a late block. Get
  that measurement in the next two weeks, before any design work on the single-run screen. If it's 2 minutes,
  steps 4-6 are a different project.
- It needs a **server-side runner** at SIRC (the PR lists it under "what we need from others"). It's the same
  runner that "Redo runs in a background worker" (PR #24) and reviews' re-scoring want. Treat it as platform work
  with its own owner, not as part of tuning.
- Storage: tuning sessions are where quotas (C) start to matter. Block streams per version add up fast. Decide the
  expiry rule together with C's budgets.

### 5. Already planned, keep going
The quality ledger (the gate's verdict kept by the server, then alerts and noise floors), N-way comparisons, and
the open PRs that are close (#230 runs across commits, #226 pipelines).

## Open PRs to merge or close first

There are 12 open PRs, some from early October (#20 Typer CLI, #22 qa wizard, #24 redo in a worker, #153 tuning,
#155 benchmarks). Stale branches cost more every week: they conflict with A-C. I suggest one half-day at the start
of the week to merge, rebase or close each, so the week's work lands on a clean master.
