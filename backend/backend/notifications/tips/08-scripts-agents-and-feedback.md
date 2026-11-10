---
key: scripts-agents-and-feedback
subject: "Last tip: use your results in scripts and agents"
preheader: "Paste a page's link into qaboard.runs(). Then: issues, contributions, and where to follow changes."
cta:
  label: "Use results in scripts"
  url: "/docs/apis#runs-in-scripts"
---
The runs a page shows are a few lines of Python away. Filter them in the web app, copy the page's address, and:

```python
import qaboard as qb
for run in qb.runs("<page link>"):
    print(run.input, run.params, run.metrics.get("psnr"), run.output_dir)
```

`qa runs <page link>` lists their folders from the shell. In CI, use a token from
[Settings › Access tokens](app:/settings/tokens), with only the scopes it needs. Coding agents read results the same
way: the [skills](/docs/agents) teach them to compare with the reference before calling a change better.

That's the last email of this series. QA-Board is open source (Apache 2.0), and it grows from what its users ask.
Found a bug, or a use case it doesn't cover? [Open an issue](https://github.com/Samsung/qaboard/issues). Pull
requests to `master` are welcome: start with
[CONTRIBUTING.md](https://github.com/Samsung/qaboard/blob/master/CONTRIBUTING.md). Access to a project on your
server is a question for your QA-Board admins.

New features land every month. They're in [What's new](app:/#whats-new), the sparkles button in the sidebar, which
also shows a month's highlights once it's over.
