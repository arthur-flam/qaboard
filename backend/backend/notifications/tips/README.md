# Onboarding emails

A welcome email right after someone signs in for the first time, then one email a week. Each file is one email.

## Format
`NN-<key>.md`: front matter, then the body in Markdown.

```yaml
---
key: welcome            # stable unique id, kebab-case
subject: "..."          # <= 60 characters, specific, no emoji
preheader: "..."        # <= 100 characters, shown after the subject in inbox lists: adds information
new_users_only: true    # optional: not sent to people enrolled after using QA-Board for a while
cta:
  label: "..."          # 2-4 words, starting with a verb
  url: "/docs/..."      # a docs page, or app:/<path> for a page of the web app
---
```

Body: 60-180 words (the welcome up to ~200), short paragraphs, at most one list and one short code block. Links:
`[text](/docs/<doc-id>)` (the id is the page's folder plus its frontmatter `id`, or its `slug`), `[text](app:/<path>)`
for the web app (`app:/`, `app:/settings/tokens`, `app:/#whats-new`), full `https://` URLs for GitHub. No title (the
layout shows the subject), no greeting, no sign-off, no footer (the layout adds unsubscribe and "why you get this").

## Rules
- **Keys are stable.** A key says who already got an email: never reuse one for different content. Rewording an
  email keeps its key; a new topic gets a new key.
- **The order is the filename prefix** (`01`..`NN`). Renumbering changes the order only.
- **Adding a file sends it** to everyone still in the sequence, when their next email is due.
- **Deleting a file or renaming its key is safe**: nobody gets it anymore, and nobody gets an email twice.
- **One idea per email**, one main call to action, one thing to try today. The last email closes the sequence.
- **Every claim is in the code or the docs** (see "Claims" in `website/STYLE.md`), and every `/docs/` link exists:
  check the id and the `#anchor` in `website/docs/`. When a docs page moves, update the links here.

## Voice checklist
- [ ] Follows "Positioning" and "Voice" in `website/STYLE.md`: quality tracking, every commit next to a reference.
- [ ] Second person, present tense, active voice. Short sentences, the point first.
- [ ] Concrete: a UI label in bold as the app writes it, a key, a command, a real metric (PSNR, p99, ΔE 2000).
- [ ] No hype ("powerful", "seamless"), no "just", "simply", "easy", "please", "note that", no exclamation marks.
- [ ] The app's words: run, batch, reference, milestone, the web app, *qaboard.yaml*, `qa`.
- [ ] Skimmable in under a minute; the reader knows what to try and where it is.
- [ ] Nothing internal: no hostnames, people, ticket ids. Feedback goes to GitHub issues or the reader's admins.

## How they're sent
`backend/notifications/tips_emails.py`, turned on with **Admin → Settings → Tips emails** (`tips.enabled`):

- People are enrolled at their first sign-in. Admins enroll those already using QA-Board from **Admin → Messages**:
  they skip the welcome (`new_users_only`).
- The jobs worker checks every five minutes: each person gets the first tip, in order, whose key they didn't get
  yet, once `tips.every_days` (7) passed since their last one. The welcome goes right away.
- Not to people who turned them off (Settings → Preferences, or the link in every email), disabled accounts, service
  accounts, or people without an email address. People who didn't use QA-Board for `tips.inactive_days` (30) are
  paused until they're back.
- `user_messages` records each key sent to each user: that's what makes edits safe.
- Sites add, replace or remove tips with a folder of their own: the `tips.folder` setting
  (see `website/docs/backend-admin/messaging-users.mdx`).

Check how they look: `uv run backend/backend/notifications/preview.py previews` renders every tip to HTML and text,
`--send you@example.com` emails them. `tests/test_tips_emails.py` checks the format, the lengths, and that every
`/docs/` link exists. The emails' house style: `backend/notifications/STYLE.md`.
