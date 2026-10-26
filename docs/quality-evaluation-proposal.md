# Proposal: quality evaluation (votes) in QA-Board

Status: proposal v1 (2026-10-26), for discussion. Nothing here is implemented.
Companion of `docs/input-catalog-proposal.md`: same principles, and it reuses collections.

## TL;DR
- **What people vote on is a pair of versions, compared input by input.** That's what QA-Board already shows
  in every comparison: a *new* batch against a *reference* batch, with runs matched by input. A version (a
  **contender**) is a selection of results: a commit's batch, maybe filtered to a configuration. It's what a
  milestone already stores. A vote says "on this input, this version looks better", for one question.
- **Two ways to vote, kept apart:**
  - **Studies** are blind. Someone sets them up with a wizard (question, two versions, a collection of
    inputs, who votes), and people get them in an inbox. Sides are randomized and labels are hidden.
    Study results are evidence you can quote.
  - **Quick opinions** come from the comparison views people already use: "new is better / same / worse" on
    the input they're looking at. These aren't blind, so they're shown as notes, never mixed into study scores.
- **Results are a preference with a confidence interval, not a bare score.** "v2.4 preferred on 68% of
  inputs (58–77%), 312 votes, 8 people", then broken down by input and by the metadata from the input
  catalog (night vs day, light, sensor). The interval comes from resampling **inputs**, since 300 votes on
  5 inputs prove much less than 300 votes on 100.
- **No global Elo.** A rating only means something for one question on one set of inputs. Ranking several
  versions uses the batch equivalent of Elo (Bradley-Terry) inside a study. A long-running ranking, such as
  releases on a fixed night benchmark, is a **benchmark**: a study that stays open and adds new versions over time.
- **Votes are assets, so they keep their evidence.** When a study starts, the exact files people see are
  copied into the study's folder on the shared storage, with their hashes. Deleting run results later
  doesn't affect studies, and retention doesn't have to be blocked. Votes are never edited in place, and
  closing or deleting a study is an audited action.
- **Phases:** 1) blind pairwise studies between two versions (M-L); 2) quick opinions, scores (1-5),
  several versions, attention checks (M); 3) benchmarks, external captures as versions, exporting votes (M).

---

## 1. The core: what a vote is

```
contender   a version being judged: a selection of results (project, commit, batch, filter on configuration
            or inputs), like a milestone. It can also be a set of external files paired with inputs (§6).
question    what people answer: "Which has less noise?", "Which would you ship?". One question per screen.
trial       one input, shown with the matching output of each contender (2 for a pair), on given files
            (output.jpg, crops of a region), with randomized sides.
vote        one person's answer to one trial: left / right / same (or a score), with the time spent.
```

Why this unit:
- **It's what engineers already compare.** Every QA-Board comparison is "new vs reference, matched by input"
  (`backend/backend/compare.py`, `matching_output` in the web app). A study between two milestones, or two
  commits, needs no new mental model.
- **"Based on configurations?"** A configuration is just part of a contender's selection: `commit X,
  batch night, configuration denoise=strong` vs the same commit with `denoise=medium` is a valid study. So is
  the same configuration on two commits. We don't need to choose; a contender is a filter, like the ones
  people already type.
- **Per input, always.** Aggregated preferences hide the failures that matter ("better overall, much worse on
  backlit faces"). Keeping the input in each trial lets us break results down by anything the input catalog
  knows.

## 2. Studies (blind, set up with a wizard)

**The wizard** (four steps, each with a sensible default):
1. **What to compare.** Pick two contenders. The default is the comparison you're looking at: the new batch
   and its reference. Optionally restrict to a collection of inputs (from the input catalog) or to the inputs
   where a metric changed.
2. **What to show.** Which output file (the one the project's image viewer shows by default), full image or
   regions (ROIs from the inputs' metadata), side by side or flicker. Same viewer as the rest of QA-Board,
   with synchronized zoom.
3. **What to ask.** One or more questions from the project's list ("Overall, which is better?", "Less noise?",
   "More natural skin tones?"). Questions are reused across studies, so their results can be compared.
4. **Who votes, and how much.** People or groups, votes per trial, a deadline. The wizard shows what that buys:
   "40 inputs × 5 people = 200 votes. About 20 minutes each. Enough to detect a 60/40 preference."
   (A 60/40 preference needs about 200 independent votes for 80% power at 95% confidence.)

**Voting** is a dedicated full-screen page, reached from an inbox ("3 studies waiting, about 25 minutes"):
- one trial at a time, keyboard first: ← left, → right, ↓ same, with zoom synchronized between the two sides;
- sides randomized per trial and per person, contender names hidden until the study closes;
- trials ordered randomly per person, so the first and last inputs aren't always judged by tired people;
- people can stop and resume; the progress is saved;
- a few **attention checks** (phase 2): identical images, where the answer should be "same", and a few repeats
  to measure each person's consistency.

**Who can create and vote:** anyone who can read the project can vote when invited; creating a study needs
the project's developer role (`backend/backend/models/Access.py`). Studies belong to a project, like milestones.

## 3. Quick opinions (not blind)

In the existing comparison views, next to an output: **Better · Same · Worse**, plus an optional comment.
- They answer the default question of the project ("Overall").
- They are labelled as opinions: the person knew which side was new. They show up as notes on the input
  ("2 people found v2.4 worse here") and in a list on the batch, which is useful for triage.
- They are not added to study results. If enough quick opinions point at the same inputs, "Start a study on
  these inputs" turns them into a blind study.

This keeps voting easy for anyone, without diluting the evidence from studies.

## 4. Results

On the study page, and as a summary on the commit and milestone pages:
- **The headline:** the share of inputs where the new version is preferred, with a 95% interval, the number of
  votes and of people. Ties are shown, not hidden ("same" on 18% of votes).
- **Per input:** sorted from the strongest preference against the new version to the strongest for it, each
  one opening the pair in the viewer. That's the list engineers will actually use.
- **Per slice:** the same headline per metadata value of the inputs (scene, light, sensor), with intervals,
  so "better overall but worse at night" is visible at a glance.
- **Per person:** how often each person agrees with the majority, and their consistency on repeats. It's
  shown to the study owner only, to spot misunderstandings of the question, not to grade people.
- **As metrics:** each run of the new contender gets `votes.preferred` (its share of wins on that input) and
  `votes.count`, so preferences appear in the existing metrics tables and history pages, next to objective
  metrics. Over time this shows which objective metrics agree with people.

**Statistics, kept simple and honest:**
- Two versions: the share of wins, ties counted as half. The interval comes from a bootstrap over inputs
  (and over people), because votes on the same input are not independent.
- Several versions (phase 2): Bradley-Terry scores, the model Elo approximates, fitted on all the votes of
  the study at once rather than updated vote by vote. Shown as a ranking with intervals, and as the
  probability that A beats B.
- We say "not enough votes yet" until the interval excludes 50% or the study closes.

## 5. Why not a global Elo score

Elo answers "who would win" between players playing the same game. Our "game" changes with the question
(noise vs color), the inputs (night vs portraits) and the people voting (experts vs everyone). A single Elo
across studies mixes those up, and the number looks precise when it isn't.

What people actually want from Elo is usually one of two things, and both have a better answer:
- **"Is the new version better than the current one?"** → a two-version study. Most studies are this.
- **"How do our releases rank on night scenes over time?"** → a **benchmark** (phase 3): a fixed collection of
  inputs and one question, kept open. Each new version is compared against a few existing ones, chosen as
  the closest in score (like pairing chess players of similar level), and Bradley-Terry ranks all of them.
  The ranking means something because the inputs and question never change.

### 5.1 Ratings inside a study: many versions, not every pair, four at a time
A rating *is* useful inside a study or a benchmark, when people compare many versions (sets of parameters,
releases) and can't judge every pair on every input:
- **Not every pair is needed.** Bradley-Terry fits incomplete comparisons natively: each vote only links two
  versions, and the model finds the scores that explain all the votes. The study picks the next trial where it
  learns the most: versions whose scores are close, or still uncertain. That's how 10 versions can be ranked
  with far fewer votes than the 45 pairs × all inputs.
- **More than two images at a time.** Showing 3 or 4 versions and asking for the best (or a full ranking) fits
  the same family of models (Plackett-Luce, Bradley-Terry for rankings): a "best of 4" vote counts as 3 wins.
  So 2-up and 4-up screens are two layouts of the same study, not two features.
- **Shown as a rating, scoped.** Scores can be displayed on an Elo-like scale ("1540 ± 30") if people find it
  readable, always with the study or benchmark name next to it, never as a property of a commit.
- **Parameter sets** are contenders like any other (one configuration each). Later, votes can be the objective
  of QA-Board's tuning (`qaboard/optimize.py`): the optimizer proposes parameters, people vote, repeat. That's
  ambitious and comes last.

### 5.2 Quality of life
- **Suggested voters:** people who ran, reviewed or committed on the project recently, and people who voted on
  the same question before. The owner confirms.
- **Random assignment:** each person gets a random subset of trials, balanced so every trial gets its votes.
- **Reminders:** in the inbox, and by notification before the deadline, through the existing notifications.
- **Merging studies:** two studies with the same question and display settings can be pooled for results, with
  each vote still traceable to its study.
- **The viewer already has a blind A/B mode** (`webapp/src/image-viewer/ImageViewer.jsx:187`, key <kbd>h</kbd>):
  the voting page starts from it.

## 6. Versions that aren't QA-Board runs

Application engineers often compare against **another device** or a **reference camera**. Those captures can be
registered in the input catalog as files that belong to an input (a sidecar role, e.g. `competitor-A.jpg`),
or as their own inputs paired through the `id` metadata QA-Board already uses to match inputs
(`compare.py`). A contender can then be "the `competitor-A` files of these inputs". Same studies, same
results page. Phase 3.

## 7. Votes are assets: what we keep

Votes cost people's time, and their value grows (comparing releases, training learned quality metrics). So:
- **Evidence is frozen when the study starts.** The files shown (only those, e.g. a JPEG per output, not run
  folders) are copied into `<storage>/evaluations/<project>/<study>/`, named by content hash, so identical
  outputs are stored once. Each trial records which run, commit, configuration and file each side came from,
  and the hash. Run results can then be deleted by the usual retention: studies are untouched, and nothing
  has to be protected from cleanup. QA-Board keeps deleted runs as rows (`outputs.deleted`), so the links
  stay readable.
- **Votes are append-only.** Changing an answer adds a new vote that replaces the previous one in results;
  the history stays.
- **Studies are closed, not edited.** Closing a study freezes its results. Deleting one is possible for its
  owner or an admin, is recorded in the audit log (`AuditEvent`), and keeps the evidence folder until an admin
  removes it.
- **Everything is exportable,** as files next to the evidence (`votes.csv`, `trials.csv`, `study.yaml`) and
  through the Python API, so the data outlives the tool, like the input catalog.

## 8. Data model (sketch)

```
eval_questions     id, project_id, text, kind (pairwise | score_1_5), help, created_by, archived
eval_studies       id, project_id, title, kind (study | benchmark), status (draft | open | closed | deleted)
                   display (files, rois, layout), settings (votes per trial, deadline, attention checks)
                   evidence_dir, created_by, created_at, closed_at
eval_contenders    id, study_id, label, selector (project, commit, batch, filter, configuration | external files)
                   resolved (input → output id, snapshot of commit and configuration)
eval_trials        id, study_id, question_id, catalog_input_id, test_input_id
                   items: list of (contender_id, output_id, file, content_hash, evidence path)
                   kind (normal | identical_check | repeat_of)
eval_assignments   study_id, user_id or group_id, quota, progress
eval_votes         id, trial_id, user_id, answer (left | right | same | score), shown_order, duration_ms
                   source (study | quick), replaces_vote_id, created_at, client info (screen size)
```
Quick opinions use `eval_votes` with `source = quick` on an implicit study per comparison, so there's one
place for votes, and results filter on `source`.

## 9. Phases

| Phase | Scope | Size | Exit criterion |
|---|---|---|---|
| 1. Blind pairwise studies | Wizard for two contenders, inbox, voting page with the existing image viewer, evidence frozen at start, results per input and per slice with intervals, export | M-L | One team replaces a spreadsheet or tool-based evaluation with a study |
| 2. Everyday use | Quick opinions in comparison views, 1-5 scores, several contenders with Bradley-Terry, 4-up screens, adaptive pairing, attention checks and repeats, reminders, votes as run metrics | M | Quick opinions are used for triage on a release |
| 3. Benchmarks and beyond | Benchmarks that stay open, versions from external captures, Python API for votes (to train or validate quality metrics) | M | A benchmark tracks releases for a quarter |

## 10. Open questions
1. **What do the current evaluation tools do** beyond pairwise votes? Scores against a scale, defect marking
   on a region, standard protocols (ITU-T P.910-style sessions in controlled viewing conditions)? Phase 1
   should cover what they're used for most, and nothing else.
2. **Who votes in practice:** a few experts, or many people? With a few experts, per-person consistency matters
   more than the number of votes, and the wizard's defaults should change.
3. **Viewing conditions:** are evaluations done on calibrated screens? If so, record the screen with each vote,
   and maybe restrict studies to some workstations.
4. **Videos:** the same model works with a video player instead of the image viewer, but synchronized playback
   needs its own work.
