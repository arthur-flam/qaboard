---
key: compare-with-a-release
subject: "Compare your commit with the last release"
preheader: "The default reference is the latest main. Pick a release instead, stack several, name both sides."
cta:
  label: "Choose a reference"
  url: "/docs/user-guide/comparing-results#picking-a-reference"
---
By default, a commit is compared with the latest results of the project's reference branch, often `main`. That
answers "what does my change do to everyone's code?". To know whether you beat what shipped, compare with the
release.

On a commit page, click **Change** next to the commit field of the **ref** bar, and pick a milestone, like the last
release. To save one yourself, click the star next to a commit's hash. It's then one click away, for you or for the
whole project, and the cleanup of old results keeps it.

The reference bar does two more things:

- **Stack a reference**, under the **ref** bar, adds a second reference. Press `r` to switch between the release and
  the latest `main` on the same page.
- Click **new** or **ref** at the start of a bar to rename that side, e.g. `ISP v2` and `ISP v1`. Image labels, plot
  legends and the metrics tables use the names, and so does the link you share.
