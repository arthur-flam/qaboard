"""
The tips emails: a welcome right after people's first sign-in, then one tip a week, then they stop.
The tips are Markdown files in backend/notifications/tips/ (see its README.md), plus those of the `tips.folder` setting.

Rules, to stay useful and never spammy:
- Each tip has a stable key. We remember the keys each user got (UserMessage): editing, reordering or adding tips
  never sends one twice. A new tip reaches the people who are still in the sequence.
- At most one email per `tips.every_days` (7) per person, at about the time of the week they first signed in
  (we send when it's due, and the worker checks every few minutes).
- Only to active accounts of people, with an email address, who didn't turn them off (Settings → Preferences, or the
  link in every email). People who didn't use QA-Board for `tips.inactive_days` are paused, and resume if they're back.
- Welcome emails (`new_users_only`) only go to people who never got a tip, and not to existing users that admins enroll.

The decisions are pure functions (plan), tested in tests/test_tips_emails.py. send_due_tips() runs in the jobs worker.
"""
import re
import datetime
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable, Dict, List, Optional, Tuple
from urllib.parse import urljoin

import yaml
from jinja2 import Environment, FileSystemLoader, select_autoescape
from markupsafe import Markup

from .markdown_email import render as render_markdown


SEQUENCE = 'tips'
TIPS_DIR = Path(__file__).parent / 'tips'
MAX_ATTEMPTS = 3
RETRY_AFTER = datetime.timedelta(hours=1)
# per run of the worker (every few minutes): enrolling hundreds of people at once spreads their welcome over a while
MAX_PER_RUN = 50
KEY = re.compile(r'^[a-z0-9][a-z0-9-]{0,62}$')
# preferences.tips_emails: false turns them off
PREFERENCE = 'tips_emails'


@dataclass
class Tip:
  key: str
  order: float
  subject: str
  preheader: str
  body: str
  cta_label: Optional[str] = None
  cta_url: Optional[str] = None
  new_users_only: bool = False
  title: Optional[str] = None
  source: str = 'built-in'
  file: Optional[str] = None
  disabled: bool = False

  def describe(self) -> dict:
    return {
      'key': self.key, 'order': self.order, 'subject': self.subject, 'preheader': self.preheader,
      'cta_label': self.cta_label, 'cta_url': self.cta_url, 'new_users_only': self.new_users_only,
      'source': self.source, 'file': self.file,
    }


def parse_tip(text: str, file_name: str, source: str = 'built-in') -> Tip:
  """A tip's file: YAML front matter, then Markdown. Raises ValueError saying what's wrong."""
  match = re.match(r'^---\s*\n(.*?)\n---\s*\n?(.*)$', text, re.S)
  if not match:
    raise ValueError(f"{file_name}: starts with front matter between --- lines")
  try:
    meta = yaml.safe_load(match.group(1)) or {}
  except yaml.YAMLError as e:
    raise ValueError(f"{file_name}: invalid front matter: {e}")
  if not isinstance(meta, dict):
    raise ValueError(f"{file_name}: the front matter is not a mapping")
  key = meta.get('key')
  if not isinstance(key, str) or not KEY.match(key):
    raise ValueError(f"{file_name}: `key` must be kebab-case, e.g. compare-with-a-release")
  prefix = re.match(r'^(\d+(?:\.\d+)?)-', Path(file_name).name)
  order = meta.get('order', float(prefix.group(1)) if prefix else None)
  if not isinstance(order, (int, float)) or isinstance(order, bool):
    raise ValueError(f"{file_name}: name the file NN-<key>.md, or set `order`")
  disabled = bool(meta.get('disabled'))
  subject = meta.get('subject')
  body = match.group(2).strip()
  if not disabled and (not isinstance(subject, str) or not subject.strip()):
    raise ValueError(f"{file_name}: `subject` is missing")
  if not disabled and not body:
    raise ValueError(f"{file_name}: the email has no text")
  cta = meta.get('cta') or {}
  if not isinstance(cta, dict) or (cta and not (cta.get('label') and cta.get('url'))):
    raise ValueError(f"{file_name}: `cta` has a `label` and a `url`")
  return Tip(
    key=key, order=float(order), subject=(subject or '').strip(), preheader=str(meta.get('preheader') or '').strip(), body=body,
    cta_label=cta.get('label'), cta_url=cta.get('url'), new_users_only=bool(meta.get('new_users_only')),
    title=meta.get('title'), source=source, file=Path(file_name).name, disabled=disabled,
  )


def load_tips(folder: Optional[str] = None) -> Tuple[List[Tip], List[str]]:
  """
  The sequence, in order: the built-in tips, and those of `folder` (the `tips.folder` setting). A file in the folder
  with a built-in tip's key replaces it, and `disabled: true` removes it. Returns (tips, errors): bad files are skipped.
  """
  tips: Dict[str, Tip] = {}
  errors = []
  sources = [(TIPS_DIR, 'built-in')] + ([(Path(folder), 'site')] if folder else [])
  for directory, source in sources:
    if not directory.is_dir():
      errors.append(f"{directory}: not a folder")
      continue
    for path in sorted(directory.glob('*.md')):
      if path.name.upper() == 'README.MD' or path.name.startswith('_'):
        continue
      try:
        tip = parse_tip(path.read_text(encoding='utf-8'), str(path), source)
      except (ValueError, OSError, UnicodeDecodeError) as e:
        errors.append(str(e))
        continue
      if tip.key in tips and tips[tip.key].source == source:
        errors.append(f"{path.name}: the key {tip.key} is already used by {tips[tip.key].file}")
        continue
      tips[tip.key] = tip
  ordered = sorted((t for t in tips.values() if not t.disabled), key=lambda t: (t.order, t.source != 'built-in', t.file or ''))
  return ordered, errors


# ---- Who gets what, when

@dataclass
class Received:
  """A row of UserMessage, the parts plan() needs"""
  key: str
  status: str
  attempts: int = 0
  sent_at: Optional[datetime.datetime] = None


@dataclass
class Plan:
  # send, wait (not due yet), paused (inactive), finished
  state: str
  tip: Optional[Tip] = None
  due_at: Optional[datetime.datetime] = None


def done(row: Received) -> bool:
  """Whether that tip is over for the user: we never send it (again)"""
  return row.status in ('sent', 'skipped', 'sending') or (row.status == 'failed' and row.attempts >= MAX_ATTEMPTS)


def plan(tips: List[Tip], received: List[Received], now: datetime.datetime, every_days: int, active: bool) -> Plan:
  """What to do for a user enrolled in the tips. `active`: they used QA-Board in the last `tips.inactive_days`."""
  rows = {row.key: row for row in received}
  sent = [row.sent_at for row in received if row.status == 'sent' and row.sent_at]
  last_sent = max(sent) if sent else None
  for tip in tips:
    row = rows.get(tip.key)
    if row is not None and done(row):
      continue
    # welcomes are for people who never got a tip
    if tip.new_users_only and last_sent is not None:
      continue
    # a week after the last email we sent or tried to send (not counting this one's failures)
    attempts = [r.sent_at for r in received if r.key != tip.key and r.status in ('sent', 'failed') and r.sent_at]
    due_at = max(attempts) + datetime.timedelta(days=every_days) if attempts else now
    if row is not None and row.status == 'failed' and row.sent_at:
      due_at = max(due_at, row.sent_at + RETRY_AFTER)
    # after the welcome, only to people who still use QA-Board
    if last_sent is not None and not active:
      return Plan('paused', tip, due_at)
    if due_at > now:
      return Plan('wait', tip, due_at)
    return Plan('send', tip, due_at)
  return Plan('finished')


# ---- Rendering

_environment = Environment(
  loader=FileSystemLoader(Path(__file__).parent / 'templates'),
  autoescape=select_autoescape(['html']),
  trim_blocks=True,
  lstrip_blocks=True,
)


def link_resolver(qaboard_url: str, docs_root: str = '/') -> Callable[[str], str]:
  """/docs/<page> to the docs (served by this server, or the `web.docs_root` setting), app:/<path> to the web app"""
  base = qaboard_url.rstrip('/') + '/'
  docs_root = docs_root or '/'
  docs_base = docs_root if docs_root.startswith('http') else urljoin(base, docs_root.lstrip('/'))
  docs_base = docs_base.rstrip('/') + '/'
  def resolve(url: str) -> str:
    url = (url or '').strip()
    if url.startswith('app:'):
      return urljoin(base, url[4:].lstrip('/'))
    if url.startswith('/docs/') or url == '/docs':
      return urljoin(docs_base, url.lstrip('/'))
    if url.startswith('/'):
      return urljoin(base, url.lstrip('/'))
    return url
  return resolve


def render_tip(tip: Tip, info: dict) -> Tuple[str, str, str]:
  """
  Returns (subject, html, text). info: {qaboard_url, docs_root, unsubscribe_url, position, total, started_at (text)}
  """
  resolve = link_resolver(info['qaboard_url'], info.get('docs_root') or '/')
  body_html, body_text = render_markdown(tip.body, resolve)
  position, total = info.get('position'), info.get('total')
  if tip.new_users_only:
    header_right = 'Welcome'
  elif position and total:
    header_right = f"Tip {position} of {total}"
  else:
    header_right = 'Tip'
  weekly = f"one tip a week, {total} in all" if total else "one tip a week"
  reason = (f"You get this email because you signed in to QA-Board{' on ' + info['started_at'] if info.get('started_at') else ''}: "
            f"a welcome, then {weekly}, and then they stop.")
  context = {
    'subject': tip.subject,
    'title': tip.title or tip.subject,
    'preheader': tip.preheader,
    'header_url': resolve('app:/'),
    'header_right': header_right,
    'accent': 'primary',
    'body': Markup(body_html),
    'body_text': body_text,
    'cta_label': tip.cta_label,
    'cta_url': resolve(tip.cta_url) if tip.cta_url else None,
    'reason': reason,
    'unsubscribe_url': info.get('unsubscribe_url'),
    'preferences_url': resolve('app:/settings/preferences'),
    'github_url': 'https://github.com/Samsung/qaboard',
  }
  html = _environment.get_template('tip.html').render(**context)
  text = _environment.get_template('tip.txt').render(**context)
  return tip.subject, html, text


# ---- Unsubscribing: a link that works without signing in

def _serializer():
  from itsdangerous import URLSafeSerializer
  from backend import app
  return URLSafeSerializer(app.secret_key, salt='qaboard-tips-unsubscribe')


def unsubscribe_token(user_id: int) -> str:
  return _serializer().dumps({'u': user_id})


def user_id_of(token: str) -> Optional[int]:
  from itsdangerous import BadSignature
  try:
    data = _serializer().loads(token)
  except BadSignature:
    return None
  return data.get('u') if isinstance(data, dict) and isinstance(data.get('u'), int) else None


def unsubscribe_url(qaboard_url: str, user_id: int) -> str:
  return f"{qaboard_url.rstrip('/')}/api/v1/tips/unsubscribe?key={unsubscribe_token(user_id)}"


def wants_tips(user) -> bool:
  return (user.preferences or {}).get(PREFERENCE) is not False


def set_wants_tips(user, value: bool):
  preferences = dict(user.preferences or {})
  if value:
    preferences.pop(PREFERENCE, None)
  else:
    preferences[PREFERENCE] = False
  user.preferences = preferences
  user.preferences_updated_at = datetime.datetime.utcnow()


# ---- With the database

def settings():
  from ..settings import setting
  return {
    'enabled': bool(setting('tips.enabled')),
    'every_days': int(setting('tips.every_days') or 7),
    'inactive_days': int(setting('tips.inactive_days') or 30),
    'folder': setting('tips.folder') or None,
  }


def current_tips() -> Tuple[List[Tip], List[str]]:
  return load_tips(settings()['folder'])


def enroll_new_user(user):
  """At their first sign-in. Doesn't commit."""
  if user.kind != 'human' or user.tips_started_at is not None:
    return
  if not settings()['enabled']:
    return
  user.tips_started_at = datetime.datetime.utcnow()


def active_user_ids(session, since: datetime.datetime) -> set:
  """People who signed in, used the web app or ran `qa` since then"""
  from sqlalchemy import text
  rows = session.execute(text("""
    SELECT u.id FROM users u
    WHERE u.kind = 'human' AND (
      u.last_login_at >= :since
      OR EXISTS (SELECT 1 FROM user_sessions s WHERE s.user_id = u.id AND s.last_seen_at >= :since)
      OR EXISTS (SELECT 1 FROM usage_events e WHERE e.user_id = u.id AND e.created_at >= :since)
    )
  """), {"since": since}).fetchall()
  return {row[0] for row in rows}


def enroll_active_users(session, active_days: int) -> int:
  """
  Admins enroll people who already use QA-Board: they get the tips, not the welcome. Commits. Returns how many.
  """
  from backend.models import User, UserMessage
  now = datetime.datetime.utcnow()
  active = active_user_ids(session, now - datetime.timedelta(days=active_days))
  users = (session.query(User)
    .filter(User.kind == 'human', User.status == 'active', User.tips_started_at.is_(None), User.id.in_(active or [-1]))
    .all())
  tips, _ = current_tips()
  welcomes = [t.key for t in tips if t.new_users_only]
  for user in users:
    user.tips_started_at = now
    for key in welcomes:
      session.add(UserMessage(user_id=user.id, sequence=SEQUENCE, key=key, status='skipped', error='enrolled by an admin', created_at=now))
  session.commit()
  return len(users)


def received_by_user(session, user_ids) -> Dict[int, List[Received]]:
  from backend.models import UserMessage
  received: Dict[int, List[Received]] = {}
  if not user_ids:
    return received
  rows = (session.query(UserMessage.user_id, UserMessage.key, UserMessage.status, UserMessage.attempts, UserMessage.sent_at)
    .filter(UserMessage.sequence == SEQUENCE, UserMessage.user_id.in_(list(user_ids)))
    .all())
  for user_id, key, status, attempts, sent_at in rows:
    received.setdefault(user_id, []).append(Received(key, status, attempts or 0, sent_at))
  return received


def enrolled_users(session):
  from backend.models import User
  return (session.query(User)
    .filter(User.tips_started_at.isnot(None), User.kind == 'human', User.status == 'active')
    .all())


def overview(session) -> dict:
  """For Admin → Messages: the sequence, how many got each tip, and where people are"""
  from sqlalchemy import func
  from backend.models import User, UserMessage
  from .mail import recipient_for, email_settings
  config = settings()
  tips, errors = current_tips()
  counts = dict(((key, status), n) for key, status, n in session
    .query(UserMessage.key, UserMessage.status, func.count())
    .filter(UserMessage.sequence == SEQUENCE)
    .group_by(UserMessage.key, UserMessage.status).all())
  now = datetime.datetime.utcnow()
  users = enrolled_users(session)
  received = received_by_user(session, [u.id for u in users])
  active = active_user_ids(session, now - datetime.timedelta(days=config['inactive_days']))
  mail_settings = email_settings()
  people = {'enrolled': len(users), 'waiting': 0, 'due': 0, 'paused': 0, 'finished': 0, 'opted_out': 0, 'no_email': 0}
  for user in users:
    if not wants_tips(user):
      people['opted_out'] += 1
      continue
    if not recipient_for(user.user_name, user.email, mail_settings):
      people['no_email'] += 1
      continue
    state = plan(tips, received.get(user.id, []), now, config['every_days'], user.id in active).state
    people[{'send': 'due', 'wait': 'waiting'}.get(state, state)] += 1
  people['not_enrolled_active'] = (session.query(func.count(User.id))
    .filter(User.kind == 'human', User.status == 'active', User.tips_started_at.is_(None), User.id.in_(active or [-1]))
    .scalar())
  return {
    **config,
    'email_enabled': mail_settings.enabled,
    'errors': errors,
    'tips': [{
      **tip.describe(), 'position': index + 1,
      'sent': counts.get((tip.key, 'sent'), 0), 'failed': counts.get((tip.key, 'failed'), 0), 'skipped': counts.get((tip.key, 'skipped'), 0),
    } for index, tip in enumerate(tips)],
    'people': people,
  }


def info_for(user, tips: List[Tip], tip: Tip) -> dict:
  from ..config import qaboard_url
  from ..settings import setting
  numbered = [t for t in tips if not t.new_users_only]
  started = user.tips_started_at
  started_at = f"{started.day} {started:%B %Y}" if started else None
  return {
    'qaboard_url': qaboard_url,
    'docs_root': setting('web.docs_root') or '/',
    'unsubscribe_url': unsubscribe_url(qaboard_url, user.id),
    'position': numbered.index(tip) + 1 if tip in numbered else None,
    'total': len(numbered),
    'started_at': started_at,
  }


def headers_for(info: dict, tip: Tip) -> dict:
  return {
    # Outlook, Gmail and others show an "Unsubscribe" button; One-Click (RFC 8058) POSTs to the URL
    'List-Unsubscribe': f"<{info['unsubscribe_url']}>" if info.get('unsubscribe_url') else None,
    'List-Unsubscribe-Post': 'List-Unsubscribe=One-Click' if info.get('unsubscribe_url') else None,
    'List-Id': 'QA-Board tips <tips.qaboard>',
    # for users' mail rules
    'X-QABoard-Message': f"tips/{tip.key}",
  }


def send_tip(session, user, tip: Tip, tips: List[Tip], existing: Optional[Received]) -> str:
  """Claims the tip for the user, then sends it. Returns the new status. Commits."""
  from sqlalchemy.exc import IntegrityError
  from backend.models import UserMessage
  from .mail import recipient_for, send_email
  now = datetime.datetime.utcnow()
  recipient = recipient_for(user.user_name, user.email)
  if existing is None:
    row = UserMessage(user_id=user.id, sequence=SEQUENCE, key=tip.key, status='sending', recipient=recipient, created_at=now)
    session.add(row)
    try:
      session.commit()
    except IntegrityError:
      session.rollback()
      return 'taken'
  else:
    claimed = (session.query(UserMessage)
      .filter(UserMessage.user_id == user.id, UserMessage.sequence == SEQUENCE, UserMessage.key == tip.key, UserMessage.status == 'failed')
      .update({'status': 'sending', 'recipient': recipient}, synchronize_session=False))
    session.commit()
    if not claimed:
      return 'taken'
    row = session.query(UserMessage).filter(UserMessage.user_id == user.id, UserMessage.sequence == SEQUENCE, UserMessage.key == tip.key).one()
  try:
    info = info_for(user, tips, tip)
    subject, html, text = render_tip(tip, info)
    send_email(recipient, subject, html, text, headers=headers_for(info, tip))
    row.status, row.error = 'sent', None
  except Exception as e:
    row.status, row.error = 'failed', f"{type(e).__name__}: {e}"[:2000]
    print(f"WARNING: could not send the tip {tip.key} to {user.user_name}: {row.error}")
  row.attempts = (row.attempts or 0) + 1
  row.sent_at = datetime.datetime.utcnow()
  session.commit()
  return row.status


def send_due_tips(session, now: Optional[datetime.datetime] = None, limit: int = MAX_PER_RUN) -> dict:
  """Sends the tips that are due. Run by the jobs worker, one worker at a time. Returns {status: count}."""
  from .mail import email_settings, recipient_for
  config = settings()
  mail_settings = email_settings()
  if not config['enabled'] or not mail_settings.enabled:
    return {}
  now = now or datetime.datetime.utcnow()
  tips, errors = current_tips()
  for error in errors:
    print(f"WARNING: tips emails: {error}")
  if not tips:
    return {}
  users = [u for u in enrolled_users(session) if wants_tips(u) and recipient_for(u.user_name, u.email, mail_settings)]
  received = received_by_user(session, [u.id for u in users])
  active = active_user_ids(session, now - datetime.timedelta(days=config['inactive_days']))
  results: Dict[str, int] = {}
  for user in users:
    if sum(results.values()) >= limit:
      break
    rows = received.get(user.id, [])
    decision = plan(tips, rows, now, config['every_days'], user.id in active)
    if decision.state != 'send':
      continue
    existing = next((r for r in rows if r.key == decision.tip.key), None)
    status = send_tip(session, user, decision.tip, tips, existing)
    results[status] = results.get(status, 0) + 1
  return results
