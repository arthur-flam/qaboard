---
key: find-a-regression
subject: "Find the commit that made a metric worse"
preheader: "History follows one metric across a branch's commits, test by test. Hover until the line jumps."
cta:
  label: "Find a regression"
  url: "/docs/user-guide/history-and-dashboards#find-a-regression"
---
The median latency barely moved, but p99 tripled on one load profile. One commit on the branch did it, and
**History** finds it:

1. On a commit page, hover the name of the run that regressed and click **History**. It opens filtered on that
   input, from 3 days before the commit.
2. Pick the metric at the top left of the chart, e.g. **Latency p99**. Pick earlier dates if the regression may be
   older.
3. Hover along the line. Below the chart, the hovered commit's outputs are compared with the commit before's. Where
   the line jumps, you found the commit.
4. Click that point to keep it as the reference, then hover the later commits to see whether a fix brought the
   metric back. Double-click the chart to let go.

Keep **Breakdown per test** on: a regression on one test can hide in the median over all of them. The metric, the
chart options and the filter are saved in the page's link, so you can send the investigation as it is.
