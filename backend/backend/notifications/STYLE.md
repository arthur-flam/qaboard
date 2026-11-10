# QA-Board's emails: house style

Every email QA-Board sends looks and reads like the web app: Blueprint's colors, a dark header like the sidebar,
technical and to the point. They must also work in **Outlook 2016/2019 on Windows**, which renders HTML with Word: that
decides most of how they're built.

## The pieces
- `templates/_layout.html`: the frame. A 600px white card on a gray page, the dark header (**QA-Board**, linking to
  the app, and a short context on the right: the project, "Tip 3 of 7"), a 4px accent bar, the content, the fine
  print under the card. Templates `{% extends "_layout.html" %}` and the code renders them with `subject`,
  `preheader`, `header_url`, `header_right` and `accent` (`primary`, `success`, `warning`, `danger`).
- `templates/_ui.html`: the components, `{% import "_ui.html" as ui %}`. `ui.row()` (a row of the card),
  `ui.h1`, `ui.section_title` (small caps), `ui.tag` (SUCCEEDED, 3 FAILED), `ui.button` (the call to action),
  `ui.callout` (what readers must not miss, e.g. a maintenance window), `ui.link`, `ui.text()` (a font style),
  `ui.footer` (why they get it, and how to stop it), and the palette: `ui.colors`, `ui.intents`.
- `markdown_email.py`: Markdown to the house style's HTML, with inline styles, and to plain text. For emails written
  by people: the tips (`tips/*.md`).
- `mail.py`: sends them, with the headers that keep auto-replies away.
- `preview.py`: renders every email to `.html` and `.txt` files, and sends them to you with `--send`:
  `uv run backend/backend/notifications/preview.py previews --send you@example.com`.

New emails extend the layout and use the components: don't copy the HTML of another template.

## Outlook rules
Outlook (Word) ignores or breaks on most modern HTML and CSS. `tests/test_notifications.py` checks the worst:

- **Tables for layout**, `role="presentation"`, widths in attributes (`width="600"`). No `<div>` layout, flexbox,
  grid, `position`, floats.
- **Inline styles** on every element, and `bgcolor` attributes next to `background-color`. `<style>` only for
  progressive enhancements (hover, the mobile layout).
- **Padding on table cells only**: Word ignores the padding of `<p>`, `<a>` and `<div>`. Margins on `<p>` work.
- **Buttons are VML** in Outlook (`<v:roundrect>`, with `arcsize` for the 3px corners), a link elsewhere: use
  `ui.button`. Give it a `width` that fits the label (`ui.button_width` does).
- **Fonts**: the system stack, Segoe UI in Outlook (`<!--[if mso]>` in the layout). No web fonts.
  `mso-line-height-rule:exactly` with every line height.
- **No** CSS variables, `background-image`, SVG, `border-radius` that matters, `@font-face`, `<pre>` (code blocks are
  table cells), images that carry meaning (they're blocked by default): every email reads well with images off.
- **Light only**: `color-scheme: light`. Dark-mode clients invert colors; Blueprint's palette survives it.

## What every email has
- A **subject** that says what happened or what's inside, specific, ≤ 60 characters, no emoji: "denoise-sweep: 3 of
  180 runs failed · isp/denoiser", "Compare your commit with the last release".
- A **preheader** (the line inbox lists show after the subject) that adds information, ≤ 100 characters.
- A **title** (`ui.h1`), then the point in one or two sentences.
- **One main call to action** (`ui.button`), and at most one secondary link next to it.
- **The fine print**: why they get this email, and how to stop it. Emails people didn't ask for have an unsubscribe
  link that works without signing in (and `List-Unsubscribe` headers, `tips_emails.headers_for`). Never more emails
  than people expect: say how many and how often.
- A **plain text part** with the same content and the links spelled out: some people read only that.

## Voice
Like the docs (`website/STYLE.md`): second person, present tense, the point first, the app's words (run, batch,
reference, milestone), UI labels in **bold** as the app writes them. Concrete over enthusiastic: no "powerful",
"seamless", "just", "simply", no exclamation marks. Nothing internal in built-in emails: no hostnames, people, ticket
ids (they ship with the open-source project).

## Colors
Blueprint's, like the web app (`ui.colors`, `ui.intents`):

| Use | Color |
|---|---|
| Text, titles | `#1C2127` |
| Body text | `#404854` |
| Secondary text, labels | `#5F6B7C`, fine print `#738091` |
| Links | `#1B69B6` |
| Button, accent | `#2D72D2` |
| Borders | `#E5E8EB`, page `#F0F2F5`, code `#F6F7F9` |
| Success / warning / danger | `#238551` / `#C87619` / `#CD4246` accents, with their light backgrounds |
