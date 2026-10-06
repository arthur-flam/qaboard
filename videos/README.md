# QA-Board videos

Scripted, reproducible videos about QA-Board: every terminal you see really runs, every page of the web app is the
real one with real results. Change the storyboard, run one command, get the new video.

| Chapter | Storyboard | |
|---------|------------|-|
| 1. From `results_final_v3` to a dashboard | [01-getting-started](01-getting-started/storyboard.yaml) | Setting up a project with `qa wizard`, running batches, comparing results in the browser |

## Making a video
```bash
cd videos && npm install                                  # xterm.js and fonts for the stage
videos/stack/stack.sh up                                  # the demo QA-Board server (Docker), see stack/README.md
uv run videos/make.py videos/01-getting-started           # records the terminal scenes, then renders
# → videos/01-getting-started/out/01-getting-started.mp4 and .srt (captions)

uv run videos/make.py videos/01-getting-started render                      # render again, from the recordings
uv run videos/make.py videos/01-getting-started render --scenes outro,chat  # re-render some scenes only
uv run videos/make.py videos/01-getting-started record --scenes batch       # re-record a terminal scene
```
Needs ffmpeg, Node, Docker, and Chromium (Playwright's: `uv run` installs the Python package, the browser in
/opt/pw-browsers is used when present, otherwise run `uv run --with playwright playwright install chromium`).
Recording edits /etc/hosts (for the pretend `*.acme.internal` hosts) and listens on ports 80 and 443: run it as root
in a container or a VM.

## How it works
- **storyboard.yaml**: the scenes, in order, each with its narration lines (the captions, and later the voice).
  - `title`: a title card. `chat`: a team chat where messages appear. `terminal`: a recorded terminal session.
    `browser`: the web app in a browser window, driven by actions (click, scroll, zoom...), see the top of
    [lib/render.py](lib/render.py). `zoom: 1.25` renders the app larger, for legibility.
  - `{name}` placeholders come from `vars:`, and from values captured while recording (e.g. commit hashes, for URLs).
- **lib/terminal.py** runs terminal scenes for real in a pseudo-terminal, types like a person, and saves asciicasts
  (`.cache/<chapter>/casts`). Long waits are shortened. `mark:` steps name moments the narration can sync to.
- **lib/fakellm.py**: the AI assistant of `qa wizard` talks to a scripted, Anthropic-compatible model
  (`conversation.yaml`): the wizard and its tools are real, the conversation is deterministic and free.
- **lib/stage/**: an HTML page that renders every scene at 1920×1080 (xterm.js replays the terminals, the app is in an
  iframe), with a fake cursor, camera zooms and captions. **lib/render.py** records it with Playwright, and joins the
  clips with ffmpeg (crossfades, narration audio, `.srt` captions).

## Adding a voice
Narration lines are written to be said. Set a provider in the storyboard:
```yaml
voice:
  provider: elevenlabs
  voice_id: <voice id>
  model_id: eleven_multilingual_v2
```
and `ELEVENLABS_API_KEY` in the environment (or `QA_VIDEO_VOICE=none` to render without). Each line is synthesized
once (cached in `.cache/voice`), lines follow each other at the voice's pace (or wait for their `at:` moment), a scene
lasts until its last line is said, captions follow the voice, and the audio is mixed in.
Another provider is a class with `audio(line)` and `duration(line)` in [lib/voice.py](lib/voice.py).

## Writing a new chapter
Copy a chapter folder, edit the storyboard. Keep narration lines short (one idea each), show real things, and end
on what viewers can do next.
