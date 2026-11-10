"""
Tests for the tips emails (backend/notifications/tips_emails.py) and the Markdown of emails (markdown_email.py):
who gets which tip when, the files' format, and that every built-in tip renders and links to docs pages that exist.
With the database: integration_tests/test_messages.py.
"""
import re
import datetime
from pathlib import Path

import pytest
import yaml

from backend.notifications.tips_emails import Tip, Received, plan, parse_tip, load_tips, render_tip, link_resolver, MAX_ATTEMPTS
from backend.notifications.markdown_email import render


now = datetime.datetime(2026, 11, 9, 10, 0)
day = datetime.timedelta(days=1)
WEBSITE = Path(__file__).resolve().parents[2] / 'website'


def tip(key, new_users_only=False, order=1):
  return Tip(key=key, order=order, subject=key.title(), preheader='', body='Body', new_users_only=new_users_only)


sequence = [tip('welcome', new_users_only=True), tip('one', order=2), tip('two', order=3)]


def test_welcome_right_away_then_weekly():
  decision = plan(sequence, [], now, 7, active=True)
  assert (decision.state, decision.tip.key) == ('send', 'welcome')
  received = [Received('welcome', 'sent', 1, now)]
  decision = plan(sequence, received, now + 6 * day, 7, active=True)
  assert (decision.state, decision.tip.key, decision.due_at) == ('wait', 'one', now + 7 * day)
  assert plan(sequence, received, now + 7 * day, 7, active=True).state == 'send'
  received.append(Received('one', 'sent', 1, now + 7 * day))
  received.append(Received('two', 'sent', 1, now + 14 * day))
  assert plan(sequence, received, now + 30 * day, 7, active=True).state == 'finished'


def test_existing_users_never_get_the_welcome():
  # enrolled by an admin: the welcome is skipped
  decision = plan(sequence, [Received('welcome', 'skipped')], now, 7, active=True)
  assert (decision.state, decision.tip.key) == ('send', 'one')
  # a welcome added to the sequence later doesn't go to people who already got tips
  decision = plan(sequence, [Received('one', 'sent', 1, now - 8 * day)], now, 7, active=True)
  assert decision.tip.key == 'two'


def test_edits_never_send_twice():
  # renamed or deleted tips are ignored (but count for the weekly rhythm), reordered ones go by key,
  # new ones reach people still in the sequence
  received = [Received('welcome', 'sent', 1, now - 20 * day), Received('two', 'sent', 1, now - 13 * day), Received('gone', 'sent', 1, now - 6 * day)]
  decision = plan(sequence, received, now, 7, active=True)
  assert (decision.state, decision.tip.key, decision.due_at) == ('wait', 'one', now + day)


def test_inactive_people_are_paused_after_the_welcome():
  assert plan(sequence, [], now, 7, active=False).state == 'send'
  received = [Received('welcome', 'sent', 1, now - 40 * day)]
  assert plan(sequence, received, now, 7, active=False).state == 'paused'


def test_failures():
  failed_once = [Received('welcome', 'failed', 1, now - datetime.timedelta(minutes=10))]
  assert plan(sequence, failed_once, now, 7, active=True).state == 'wait'
  assert plan(sequence, failed_once, now + datetime.timedelta(hours=1), 7, active=True).state == 'send'
  # given up: the next one comes a week after the last attempt
  given_up = [Received('welcome', 'failed', MAX_ATTEMPTS, now)]
  decision = plan(sequence, given_up, now, 7, active=True)
  assert (decision.state, decision.tip.key, decision.due_at) == ('wait', 'one', now + 7 * day)


def test_parse_tip():
  text = "---\nkey: compare-to-a-release\nsubject: Compare\ncta:\n  label: Try it\n  url: /docs/x\n---\nHello **you**"
  parsed = parse_tip(text, '02-compare.md')
  assert (parsed.key, parsed.order, parsed.cta_url, parsed.body) == ('compare-to-a-release', 2.0, '/docs/x', 'Hello **you**')
  assert parse_tip("---\nkey: k\nsubject: S\norder: 2.5\n---\nx", 'k.md').order == 2.5
  for bad, error in [
    ("no front matter", "front matter"),
    ("---\nkey: Not Kebab\nsubject: S\n---\nx", "kebab"),
    ("---\nkey: k\n---\nx", "subject"),
    ("---\nkey: k\nsubject: S\n---\n", "no text"),
    ("---\nkey: k\nsubject: S\ncta:\n  label: x\n---\ny", "cta"),
    ("---\nkey: k\nsubject: S\n---\nx", "NN-<key>"),
  ]:
    with pytest.raises(ValueError, match=error):
      parse_tip(bad, '02-k.md' if 'NN' not in error else 'k.md')


def test_site_folder_adds_replaces_and_removes(tmp_path):
  (tmp_path / '02.5-our-support.md').write_text("---\nkey: our-support\nsubject: Where to get help\n---\nAsk in our channel.")
  (tmp_path / '03-where-images-differ.md').write_text("---\nkey: where-images-differ\nsubject: Ours\n---\nOur version.")
  (tmp_path / '99-off.md').write_text("---\nkey: beyond-images\ndisabled: true\n---\n")
  (tmp_path / '50-broken.md').write_text("oops")
  tips, errors = load_tips(str(tmp_path))
  keys = [t.key for t in tips]
  assert keys.index('our-support') == keys.index('compare-with-a-release') + 1
  replaced = next(t for t in tips if t.key == 'where-images-differ')
  assert (replaced.subject, replaced.source) == ('Ours', 'site')
  assert 'beyond-images' not in keys
  assert len(errors) == 1 and '50-broken.md' in errors[0]


def test_link_resolver():
  resolve = link_resolver('https://qa.example.com/', '/')
  assert resolve('/docs/user-guide/overview#x') == 'https://qa.example.com/docs/user-guide/overview#x'
  assert resolve('app:/settings/tokens') == 'https://qa.example.com/settings/tokens'
  assert resolve('app:/#whats-new') == 'https://qa.example.com/#whats-new'
  assert resolve('https://github.com/Samsung/qaboard') == 'https://github.com/Samsung/qaboard'
  public = link_resolver('https://qa.example.com', 'https://samsung.github.io/qaboard/')
  assert public('/docs/apis') == 'https://samsung.github.io/qaboard/docs/apis'


def test_markdown_for_outlook():
  html, text = render("A [link](/docs/x), `qa run`, **bold**\n\n- one\n- two\n\n```\nqa batch\n```\n\n<script>alert(1)</script>",
                      link_resolver('https://qa'))
  assert '<a style="color:#1B69B6;text-decoration:none;" href="https://qa/docs/x">link</a>' in html
  assert '<li style=' in html and '<ul style="margin:0 0 16px 24px;padding:0;">' in html
  # code blocks are tables: Word ignores <pre>'s background and padding
  assert '<pre' not in html and 'qa&nbsp;batch' in html
  assert '<script>' not in html and '&lt;script&gt;' in html
  for unsupported in ('display:flex', 'var(--', 'background-image', 'position:'):
    assert unsupported not in html
  assert 'A link (https://qa/docs/x), `qa run`, bold' in text
  assert '- one\n- two' in text and '    qa batch' in text


def doc_pages():
  """The docs' URLs: /docs/<folder>/<id or file name>, or their slug"""
  pages = {}
  for path in (WEBSITE / 'docs').rglob('*.md*'):
    text = path.read_text(encoding='utf-8')
    meta = {}
    match = re.match(r'^---\n(.*?)\n---', text, re.S)
    if match:
      meta = yaml.safe_load(match.group(1)) or {}
    relative = path.relative_to(WEBSITE / 'docs').with_suffix('')
    doc_id = str(relative.parent / meta['id']) if meta.get('id') else str(relative)
    doc_id = doc_id.removeprefix('./')
    slug = meta.get('slug')
    url = slug.strip('/') if isinstance(slug, str) and slug.startswith('/') else doc_id
    pages[url] = text
  return pages


def anchors_of(text):
  explicit = set(re.findall(r'\{#([\w-]+)\}', text)) | set(re.findall(r'id=["\']([\w-]+)["\']', text))
  headings = {re.sub(r'[^\w\- ]', '', h.strip().lower()).replace(' ', '-') for h in re.findall(r'^#{2,6} (.+?)\s*$', text, re.M)}
  return explicit | headings


@pytest.mark.skipif(not (WEBSITE / 'docs').is_dir(), reason="the website isn't next to the backend")
def test_built_in_tips_render_and_link_to_docs_that_exist():
  tips, errors = load_tips()
  assert not errors
  assert tips[0].new_users_only, "the sequence starts with the welcome"
  assert len({t.key for t in tips}) == len(tips)
  pages = doc_pages()
  broken = []
  for t in tips:
    assert len(t.subject) <= 60, t.key
    assert t.preheader and len(t.preheader) <= 100, t.key
    assert '!' not in t.subject, t.key
    links = re.findall(r'\]\((/docs/[^)\s]+)\)', t.body) + ([t.cta_url] if t.cta_url and t.cta_url.startswith('/docs/') else [])
    for link in links:
      page, _, anchor = link[len('/docs/'):].partition('#')
      if page not in pages:
        broken.append(f"{t.file}: {link} (no such page)")
      elif anchor and anchor not in anchors_of(pages[page]):
        broken.append(f"{t.file}: {link} (no such anchor)")
    subject, html, text = render_tip(t, {'qaboard_url': 'https://qa.example.com', 'unsubscribe_url': 'https://qa.example.com/u', 'position': 1, 'total': len(tips) - 1})
    assert subject == t.subject and '<v:roundrect' in html and 'Stop these tips' in html and 'https://qa.example.com/u' in text
    assert '](' not in text, f"{t.file}: a link wasn't rendered"
  assert not broken, '\n'.join(broken)
