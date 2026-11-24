# Prompt: finish the tuning form branch

Paste the text below into the session that works on the tuning form branch. It is self-contained: that session
doesn't need this folder. Fill in the two `<...>` placeholders first.

---

You are finishing the tuning form redesign on branch `<branch name>` so it can merge. The redesign has four parts:
choosing the tests from a list of batches, the values to try as a table (one row per parameter, values as tags, a
range filler, the JSON text one click away), starting from a previous batch, and a warning before adding to an
existing batch.

## Goal

Merge a form that is polished for today's users, and whose code is easy to extend in the three directions listed
under "What comes later". Do not build those directions now. I prefer clean code that I can refactor over
abstractions made ahead of time: when in doubt, write the simple version and keep it in one place.

User feedback on the redesign is still coming in (`<link to the survey results, or "not yet">`), in particular on
how people want to choose batches. Don't add polish to the batch selection beyond fixing bugs until I share it.

## Start by reading, then tell me what you found

Before changing anything, read the branch's diff against `master` and report, briefly:

1. What is finished, what is half-done, and what is broken or untested.
2. Where the form's state lives, and how many representations of "the parameters to try" exist (the table, the
   JSON text, the automated-tuning setup, what is sent to the server).
3. Anything in the diff that is not needed for the four parts above.

Then propose a short list of what you will do, and wait for my go-ahead if the list contains a refactor larger
than a file or two.

## What to leave in good shape

These are the only preparations for the future that I want. Each should make today's code simpler, not add a layer.

1. **One model for the parameter rows.** A plain list such as `{key, mode, values}` that the table edits, with
   pure functions to and from the text form and the payload the server already accepts (`tuning_search`). The
   text view and the table view are two views of that one model. Put the functions in one module, with tests for
   the round trips, including values the table can't show (keep them, and fall back to text).
2. **Parsing and checking values in one place.** One function turns what a person typed into a value and says
   what is wrong with it. Today it knows nothing about a parameter. Give it an optional description argument
   (`{type, min, max, default, description, group}`), unused for now: later a project will describe its
   parameters and the same function will check ranges. Don't add an endpoint, a hook or a setting for it.
3. **The row's "how to try it" is explicit.** "Choose the values" and "Search automatically" are two forms today,
   each with its own list of parameters. Don't merge them now. Do make sure a parameter's name and values can
   move from one to the other without retyping (the code already converts a grid to a search space: check that it
   works from the new table).
4. **Pre-filling takes plain data.** "Start from a previous batch" should fill the form through one function that
   takes the model from point 1. Later, "run these parameters on more tests" from a single run will call the same
   function with that run's tuning parameters. If it costs less than an hour, add that entry point now: a link on
   a run with tuning parameters that opens the form filled with them. Otherwise leave it.
5. **Counting runs in one pure function** (tests × combinations), used by the summary and the checks.
6. **The register-paste conversion stays separate.** The conversion of pasted register lists is specific to one
   site's tools. Keep it as its own module with one entry point (text in, model out), so it can move to site code
   later. Don't move it now.

## What comes later (do not build)

So you know which way the code will be pushed. None of this is in scope.

- Projects describe their parameters (name, group, type, range, default, description), and the form offers a
  searchable picker and typed inputs instead of free-text names.
- A row can be "one value", "several values" or "search", in one table.
- A screen for tuning a single run step by step, with a history of versions. It will store the same flat
  `{"name": value}` parameters as the tuning form, and hand over to it ("run on more tests").

## Do not

- Change the database, the tuning endpoints' payloads, or `qa`'s options. If you think one must change, stop and
  ask.
- Add a plugin system, a schema format, a settings key, or a generic "form builder".
- Rewrite the automated tuning setup beyond what points 1 to 3 need.
- Use git vocabulary in new labels (branch, commit, diff) where a plain word works. Existing labels stay.

## Done means

- The four parts work in the browser on a real project, in light and dark themes, with the keyboard, and at a
  narrow width. Say what you tried and what you could not try.
- `npm test -- --run` and `npm run lint` pass in `webapp/`. New logic has unit tests; the form has at least one
  test that fills the table and checks the payload sent.
- The form still accepts everything the old text form accepted. List any input that behaves differently.
- The user guide page for tuning describes the new form, and the current month's release note has one bullet for
  it (follow the repository's `CLAUDE.md` for both, and run `website/release-notes/release_notes.py check`).
- A short summary for me: what changed since you started, what you left out on purpose, and the questions you
  would ask users.

## If something is unclear

Ask me before guessing, in particular about: what a label should say, whether a behaviour change is acceptable,
and anything that looks like it needs a server change.
