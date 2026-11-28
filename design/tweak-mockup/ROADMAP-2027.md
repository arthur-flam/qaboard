# Update for the CDE roadmap discussion: tuning in QA-Board, 2027

This is an update to give to the session where the CDE roadmap is discussed. It is self-contained. It describes
what we want to build and in what order. Staffing and dates are not decided.

## The proposal in one paragraph

Stop investing in a new desktop app for CDE. Move the "change parameters, rerun, look, compare, save" loop into
QA-Board, where most tuning already happens, and where every result is recorded and comparable with any commit
or milestone. Build it as a generic QA-Board feature (tuning sessions for any tool that has parameters and
steps), with our simulator as the first project that uses it. Step-by-step debugging of the simulator's
internals is out of scope: the people who do it are few, work mostly on the front-end ISP, and are not the
people who look at images.

## Why now

- The desktop app redesign has run for years without enough adoption. Tuning from QA-Board is used daily.
- Simulations are slow, so people already launch runs and come back. A web app fits that better than a desktop one.
- More code is written by agents and more research happens in Python. A recorded history of what was tried, by a
  person or an agent, is worth more than an interactive debugger.
- QA-Board already has what a tuning tool needs around the edges: image comparison, metrics, milestones, the
  record of local code changes, storage management, permissions.

## What we build, in order

Each step is useful if we stop after it. Each has a condition to pass before the next starts.

| Step | What users get | What it needs | Go on when |
|---|---|---|---|
| **1. Better tuning form** (in progress) | Choose tests from a list, values in a table, start from a previous batch | Finish and merge the current branch | IQV feedback is positive on the form |
| **2. Parameters the form knows** | A searchable list of parameters per block, with ranges, defaults and descriptions, instead of typing names. One table for "one value", "several values" and "search" | The simulator lists its parameters. One hook in the project. | The list is right for our main sensors, and people stop pasting register names |
| **3. Sessions as a record** | Any run can say which run it came from. QA-Board shows a session's history and what changed at each step. Works from the command line, scripts and agents | A small addition to how runs are reported. No new screen for editing yet | We have real sessions (from engineers and agents) to design the screen with |
| **4. Tune one run** | From a run: the chain of blocks, a form per block, input and output of each block, the log per block, a Run button, the history, and Save | Runs on the cluster as today. The simulator starts at a given block from the previous run's streams | A late-block change comes back fast enough that IQV users choose it over the desktop app for real tasks |
| **5. Save that people trust** | Save as a new settings file or into the existing files, sent for review, with the full tests run on it | A project-side function for writing our config files. Permission rules | Saved settings reach the main branch without an engineer redoing the work |
| **6. Faster** | Shorter start-up: machines that stay ready, then a simulator that stays open | Measurements from step 4. A cache with rules we can explain | Only if step 4's measurements say start-up is what holds adoption back |
| **7. Connect a local simulator** | Tune a working copy on your own machine, with the same screen and record | A small agent started by `qa connect` | Demand from engineers, or from another site |

Steps 1 to 3 need no new infrastructure. Step 4 is the first that depends on the simulator team. Steps 6 and 7 can
swap or be dropped.

## The decisions behind it

- **A session is a set of ordinary runs.** Nothing lives only in a live process. If the fast path fails, the same
  request runs the slow way and gives the same result.
- **Speed is optional, correctness is not.** Reusing earlier blocks may make a run faster, never different.
  Anything that gets saved is validated by a clean full run of the tests. So a caching bug can only waste
  time during exploration.
- **QA-Board stays generic.** It knows parameters, steps, streams and runs. It does not know image processing or
  CDE. Everything specific to us is project code (three functions: list the parameters, check a set of values,
  write them to files) or site code built into our deployment.
- **It works without any of the hooks.** A project with none gets today's form. Each hook adds something: the
  parameter list gives the rich form, per-step reports give the timeline and logs, start-at-step gives fast
  reruns, a connected simulator gives live use. This is also how we would document it for other companies.
- **The word is "tuning" at both sizes**: tuning batches (the existing tab) and tuning one run (the new screen).

## Risks, and what we do about them

| Risk | What we do |
|---|---|
| Start-up time makes single-run tuning feel slow | Measure start-up, input loading and per-block time on real cases before step 4 is designed in detail. It decides how much of step 6 is needed |
| The long-lived simulator has undiscovered bugs | It is never the only way to run, and never the source of saved results. It can come last, or not at all |
| Caching across the organisation is a project of its own (hashing each block's inputs, settings, source files and the files it reads) | Start with reuse from the previous run of the same session only. A shared cache is a separate decision after step 4, with its own owner |
| Storage: every block's streams for every version are large | Decide the rule before step 4 ships: block streams of versions nobody kept expire in days; final outputs follow the session; saved versions are ordinary CI results |
| IQV users find versions, history and saving too technical | Test the mock-up with a few of them on a real task before step 4. Plain words in the screen, no git vocabulary |
| Requests for custom behaviour grow without end | Customisation goes through data first (the parameter list), then project checks, then site-built widgets. A custom screen talks to the same record as the built-in one (see below) |
| QA-Board turns into our in-house tool | Every step ships with an example on a public sample project that is not an image pipeline, and docs written for an outside reader |

## What we are not building

- A debugger: breakpoints, stepping inside a block, reading registers of a running simulation.
- A replacement for the simulator's own caching, or a build system for it.
- A general plugin system loaded at run time. Site-specific pieces are compiled into the site's deployment.

## What we need from others

- **Simulator team:** list parameters with type, range, default and description; start at a block from given
  streams; print a marker per block in the log; report per block whether it was reused and how long it took.
- **IQV leads:** three to five people for a one-hour session with the mock-up, and later as first users of step 4.
- **Infrastructure:** a runner the server can start runs with at our site, and later machines that stay ready.

## Open questions for the roadmap discussion

1. Who owns the project-side code (parameter list, checks, saving): the simulator team or the platform team?
2. What is the target for "fast enough" in step 4, in seconds, for a late-block change on a typical image?
3. When does the desktop app stop getting features: at step 4, or only once step 5 is trusted?
4. Is a shared cache across the organisation wanted for its own sake (CI time), separately from this roadmap?

Mock-up (fake data): https://claude.ai/artifact/3PJaahHyfoLu9DCvVN6vse. Design notes: `design/tweak-mockup/NOTES.md`
in the QA-Board repository.
