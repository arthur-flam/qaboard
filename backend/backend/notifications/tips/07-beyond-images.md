---
key: beyond-images
subject: "Beyond images: profiles, test reports, transcripts, 3D"
preheader: "Every viewer puts the reference's result next to the new one. See what other teams track."
cta:
  label: "Browse the use cases"
  url: "/docs/use-cases"
---
QA-Board compares more than images and videos. Each viewer puts the reference run's result next to the new one, or
over it, and teams use the same review for:

- **Performance**: flame graphs colored by what got slower or faster, latency percentiles plotted over the
  reference's.
- **Test suites**: JUnit reports, each test against the reference, with what started failing, what got fixed and
  what disappeared.
- **LLM agents**: transcripts diffed line by line, task by task.
- **Robotics**: point clouds and 6DoF trajectories in 3D, with the ground truth.
- **Chips and fixed-point models**: `qa check-bit-accuracy` fails the CI when output files change byte for byte,
  and **Output Files** shows which ones did.

The [use cases](/docs/use-cases) walk through one regression of each kind, with screenshots of demo projects.

Something your project writes isn't shown yet? Whoever integrates it chooses the files each card shows in
*qaboard.yaml*: point them to [Viewers and file formats](/docs/viewers).
