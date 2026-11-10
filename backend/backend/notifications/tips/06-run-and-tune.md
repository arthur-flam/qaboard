---
key: run-and-tune
subject: "Run more tests or tune parameters from the browser"
preheader: "Start a batch on any commit, try a grid of values or an automated tuning, get an email when it ends."
cta:
  label: "Run tests or tune"
  url: "/docs/user-guide/running-from-the-webapp#running-tests-and-tuning"
---
When the CI's tests don't answer your question, run the commit on other tests or settings, without a terminal. On a
commit page, open **Run Tests / Tuning**, under **Tuning** in the sidebar. Name the batch, e.g. `int8-nms`, so its
runs stay apart from the CI's, type the batch of tests to run, and list the values to try under **Manual tuning**:

```json
{"score_threshold": [0.2, 0.3], "nms_iou": [0.5, 0.7]}
```

The form counts the runs before you start them: 4 combinations on each test. **Tuning Analysis** then shows which
values win.

Too many parameters to try every combination? The **Automated tuning** tab searches them for you: say what to tune,
the metrics to improve and whether higher or lower is better.

When the last run of a batch you started ends, QA-Board emails you, if your server sends emails: what failed, the
main metrics against their targets, the best combinations.

You need to be signed in, with the developer role on the project. If the form says the project has no task runner,
ask whoever integrated it.
