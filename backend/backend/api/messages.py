"""
Talking to users (Admin → Messages):
- announcements: banners in the web app, e.g. a scheduled maintenance, with a calendar file (.ics) for its window
- the tips emails: a welcome at the first sign-in, then a tip a week (backend/notifications/tips_emails.py)
- recipients: the emails of the people who use QA-Board, to paste in Outlook's To field
"""
import re
import html
import datetime
from urllib.parse import urlparse

from flask import request, jsonify, g, make_response
from sqlalchemy import or_

from backend import app, db_session
from ..config import qaboard_url
from ..models import Announcement, User
from ..audit import audit
from ..tokens import iso
from ..notifications import tips_emails
from ..notifications import mail
from ..notifications.mail import recipient_for, email_settings
from .auth import admin_required


INTENTS = ('primary', 'success', 'warning', 'danger')
MAX_TITLE = 120
MAX_MESSAGE = 600


# ---- Announcements

def utcnow():
  return datetime.datetime.utcnow()


def serialize_announcement(a: Announcement, admin=False) -> dict:
  data = {
    "id": a.id,
    "title": a.title,
    "message": a.message,
    "intent": a.intent,
    "link_url": a.link_url,
    "link_label": a.link_label,
    "starts_at": iso(a.starts_at),
    "ends_at": iso(a.ends_at),
    "dismissible": a.dismissible,
    "event_starts_at": iso(a.event_starts_at),
    "event_ends_at": iso(a.event_ends_at),
    # the web app remembers dismissals per version: an edited announcement shows again
    "revision": a.revision,
    "calendar_url": f"/api/v1/announcements/{a.id}/calendar.ics" if a.event_starts_at else None,
  }
  if admin:
    data.update({"created_at": iso(a.created_at), "updated_at": iso(a.updated_at), "created_by_id": a.created_by_id})
  return data


def active_announcements(now=None):
  now = now or utcnow()
  return (db_session.query(Announcement)
    .filter(Announcement.starts_at <= now, or_(Announcement.ends_at.is_(None), Announcement.ends_at > now))
    .order_by(Announcement.starts_at.desc())
    .all())


@app.route('/api/v1/announcements', methods=['GET'])
@app.route('/api/v1/announcements/', methods=['GET'])
def announcements_active():
  """The banners to show now. Public: the sign-in page shows them too."""
  # the most severe first
  rank = {intent: i for i, intent in enumerate(('danger', 'warning', 'primary', 'success'))}
  announcements = sorted(active_announcements(), key=lambda a: rank.get(a.intent, 9))
  response = jsonify({"announcements": [serialize_announcement(a) for a in announcements]})
  response.headers['Cache-Control'] = 'no-store'
  return response


def ics_text(value: str) -> str:
  # RFC 5545 3.3.11
  return (value or '').replace('\\', '\\\\').replace(';', '\\;').replace(',', '\\,').replace('\r\n', '\\n').replace('\n', '\\n')


def ics_date(value: datetime.datetime) -> str:
  return value.strftime('%Y%m%dT%H%M%SZ')


def ics_fold(line: str) -> str:
  """Lines of at most 75 octets, continued with a space (RFC 5545 3.1)"""
  encoded = line.encode('utf-8')
  if len(encoded) <= 75:
    return line
  parts, current = [], b''
  for char in line:
    c = char.encode('utf-8')
    if len(current) + len(c) > (75 if not parts else 74):
      parts.append(current.decode('utf-8'))
      current = b''
    current += c
  parts.append(current.decode('utf-8'))
  return '\r\n '.join(parts)


def calendar(a: Announcement, base_url: str, now=None) -> str:
  """
  The event as an iCalendar file. Opened in Outlook, it's an appointment: "Invite Attendees", paste the recipients
  (Admin → Messages), send. It doesn't block calendars (free, not busy). Edits keep its UID and bump its SEQUENCE.
  """
  host = urlparse(base_url).hostname or 'qaboard'
  link = a.link_url or base_url
  if link.startswith('/'):
    # calendars don't know where the web app is
    link = base_url.rstrip('/') + link
  description = '\n\n'.join(x for x in (a.message, f"{a.link_label or 'Details'}: {link}") if x)
  end = a.event_ends_at or (a.event_starts_at + datetime.timedelta(hours=1))
  lines = [
    'BEGIN:VCALENDAR',
    'VERSION:2.0',
    'PRODID:-//QA-Board//Announcements//EN',
    'CALSCALE:GREGORIAN',
    'METHOD:PUBLISH',
    'BEGIN:VEVENT',
    f'UID:announcement-{a.id}@{host}',
    f'SEQUENCE:{a.revision or 0}',
    f'DTSTAMP:{ics_date(now or utcnow())}',
    f'DTSTART:{ics_date(a.event_starts_at)}',
    f'DTEND:{ics_date(end)}',
    f'SUMMARY:{ics_text(a.title)}',
    f'DESCRIPTION:{ics_text(description)}',
    f'URL:{ics_text(link)}',
    'TRANSP:TRANSPARENT',
    'X-MICROSOFT-CDO-BUSYSTATUS:FREE',
    'STATUS:CONFIRMED',
    'END:VEVENT',
    'END:VCALENDAR',
  ]
  return '\r\n'.join(ics_fold(line) for line in lines) + '\r\n'


def filename_of(title: str) -> str:
  return (re.sub(r'[^A-Za-z0-9]+', '-', title).strip('-').lower() or 'event')[:60] + '.ics'


@app.route('/api/v1/announcements/<int:announcement_id>/calendar.ics', methods=['GET'])
def announcement_calendar(announcement_id):
  a = db_session.get(Announcement, announcement_id)
  if a is None or a.event_starts_at is None:
    return jsonify({"error": "No such event"}), 404
  response = make_response(calendar(a, qaboard_url))
  response.headers['Content-Type'] = 'text/calendar; charset=utf-8'
  response.headers['Content-Disposition'] = f'attachment; filename="{filename_of(a.title)}"'
  return response


def parse_date(value, name):
  """ISO 8601 to naive UTC"""
  if value in (None, ''):
    return None
  if not isinstance(value, str):
    raise ValueError(f"{name} must be an ISO 8601 date")
  try:
    date = datetime.datetime.fromisoformat(value.replace('Z', '+00:00'))
  except ValueError:
    raise ValueError(f"{name} must be an ISO 8601 date, e.g. 2026-11-20T18:00:00Z")
  if date.tzinfo is not None:
    date = date.astimezone(datetime.timezone.utc).replace(tzinfo=None)
  return date


def announcement_fields(data: dict, existing=None) -> dict:
  """Validates what admins send. Raises ValueError."""
  def get(key, default=None):
    if key in data:
      return data[key]
    return getattr(existing, key) if existing is not None else default
  title = (get('title') or '').strip()
  if not title:
    raise ValueError("The title is missing")
  if len(title) > MAX_TITLE:
    raise ValueError(f"The title is at most {MAX_TITLE} characters: details go in the message")
  message = (get('message') or '').strip()
  if len(message) > MAX_MESSAGE:
    raise ValueError(f"The message is at most {MAX_MESSAGE} characters: link to the details")
  intent = get('intent') or 'primary'
  if intent not in INTENTS:
    raise ValueError(f"intent must be one of {', '.join(INTENTS)}")
  link_url = (get('link_url') or '').strip() or None
  if link_url and urlparse(link_url).scheme not in ('http', 'https', 'mailto') and not link_url.startswith('/'):
    raise ValueError("The link must be an http(s) or mailto: URL, or a path of the web app like /docs/...")
  dates = {}
  for key in ('starts_at', 'ends_at', 'event_starts_at', 'event_ends_at'):
    dates[key] = parse_date(data[key], key) if key in data else (getattr(existing, key) if existing is not None else None)
  dates['starts_at'] = dates['starts_at'] or utcnow()
  if dates['ends_at'] and dates['ends_at'] <= dates['starts_at']:
    raise ValueError("The banner must end after it starts")
  if dates['event_ends_at'] and not dates['event_starts_at']:
    raise ValueError("The event needs a start")
  if dates['event_starts_at'] and dates['event_ends_at'] and dates['event_ends_at'] <= dates['event_starts_at']:
    raise ValueError("The event must end after it starts")
  dismissible = get('dismissible', True)
  return {
    'title': title, 'message': message, 'intent': intent,
    'link_url': link_url, 'link_label': ((get('link_label') or '').strip() or None) if link_url else None,
    'dismissible': bool(dismissible), **dates,
  }


@app.route('/api/v1/admin/announcements', methods=['GET'])
@app.route('/api/v1/admin/announcements/', methods=['GET'])
@admin_required
def admin_announcements():
  """Current and upcoming first, then the past ones (the last 50)"""
  now = utcnow()
  rows = db_session.query(Announcement).order_by(Announcement.starts_at.desc()).limit(200).all()
  current = [a for a in rows if not (a.ends_at and a.ends_at <= now)]
  past = [a for a in rows if a.ends_at and a.ends_at <= now][:50]
  return jsonify({
    "announcements": [serialize_announcement(a, admin=True) for a in current],
    "past": [serialize_announcement(a, admin=True) for a in past],
  })


@app.route('/api/v1/admin/announcements', methods=['POST'])
@app.route('/api/v1/admin/announcements/', methods=['POST'])
@admin_required
def admin_create_announcement():
  try:
    fields = announcement_fields(request.get_json(silent=True) or {})
  except ValueError as e:
    return jsonify({"error": str(e)}), 400
  actor = g.get('actor')
  a = Announcement(**fields, created_by_id=actor.user.id if actor and actor.user else None)
  db_session.add(a)
  db_session.flush()
  audit('announcement.create', 'announcement', a.id, a.title, meta={"intent": a.intent})
  db_session.commit()
  return jsonify(serialize_announcement(a, admin=True)), 201


@app.route('/api/v1/admin/announcements/<int:announcement_id>', methods=['PUT', 'PATCH'])
@admin_required
def admin_update_announcement(announcement_id):
  a = db_session.get(Announcement, announcement_id)
  if a is None:
    return jsonify({"error": "No such announcement"}), 404
  try:
    fields = announcement_fields(request.get_json(silent=True) or {}, existing=a)
  except ValueError as e:
    return jsonify({"error": str(e)}), 400
  changed = {k: v for k, v in fields.items() if getattr(a, k) != v}
  for key, value in changed.items():
    setattr(a, key, value)
  if changed:
    a.revision = (a.revision or 0) + 1
    a.updated_at = utcnow()
    audit('announcement.update', 'announcement', a.id, a.title, meta={"fields": sorted(changed)})
  db_session.commit()
  return jsonify(serialize_announcement(a, admin=True))


@app.route('/api/v1/admin/announcements/<int:announcement_id>', methods=['DELETE'])
@admin_required
def admin_delete_announcement(announcement_id):
  a = db_session.get(Announcement, announcement_id)
  if a is None:
    return jsonify({"error": "No such announcement"}), 404
  audit('announcement.delete', 'announcement', a.id, a.title)
  db_session.delete(a)
  db_session.commit()
  return jsonify({"deleted": True})


# ---- Recipients: who to email, to paste in Outlook

def active_ids_on(since, project=None):
  """People active since then: signed in, used the web app or ran qa. With `project`, who used it (or its subprojects)."""
  from sqlalchemy import text
  if not project:
    return tips_emails.active_user_ids(db_session, since)
  rows = db_session.execute(text("""
    SELECT DISTINCT user_id FROM usage_events
    WHERE created_at >= :since AND user_id IS NOT NULL AND (project_id = :project OR project_id LIKE :prefix)
  """), {"since": since, "project": project, "prefix": project.replace('%', r'\%').replace('_', r'\_') + '/%'}).fetchall()
  return {row[0] for row in rows}


def address(user, mail_settings) -> str:
  email = recipient_for(user.user_name, user.email, mail_settings)
  if not email:
    return None
  name = (user.full_name or '').replace('"', "'").strip()
  # Outlook reads "Name <email>; Name <email>"
  return f'"{name}" <{email}>' if name and re.search(r'[,;<>@()"]', name) else (f"{name} <{email}>" if name else email)


@app.route('/api/v1/admin/recipients', methods=['GET'])
@app.route('/api/v1/admin/recipients/', methods=['GET'])
@admin_required
def admin_recipients():
  """
  The email addresses of active accounts of people. ?active_days=30 (0: everyone), ?project=group/repo (who used it),
  ?admins=true (only the admins). Copy them in Outlook's To or Bcc field.
  """
  from ..admins import is_admin
  try:
    active_days = int(request.args.get('active_days', 30))
  except ValueError:
    return jsonify({"error": "active_days must be a number of days"}), 400
  project = (request.args.get('project') or '').strip().strip('/') or None
  query = db_session.query(User).filter(User.kind == 'human', User.status == 'active')
  if active_days > 0 or project:
    since = utcnow() - datetime.timedelta(days=active_days if active_days > 0 else 3650)
    ids = active_ids_on(since, project)
    query = query.filter(User.id.in_(ids or [-1]))
  users = query.order_by(User.full_name, User.user_name).all()
  if request.args.get('admins') in ('true', '1'):
    users = [u for u in users if is_admin(u) or u.is_admin]
  mail_settings = email_settings()
  recipients, missing = [], []
  for user in users:
    line = address(user, mail_settings)
    if line:
      recipients.append({"user_name": user.user_name, "full_name": user.full_name, "email": recipient_for(user.user_name, user.email, mail_settings), "address": line})
    else:
      missing.append(user.user_name)
  return jsonify({
    "recipients": recipients,
    "without_email": missing,
    # ready to paste in Outlook
    "to": '; '.join(r['address'] for r in recipients),
    "active_days": active_days,
    "project": project,
  })


# ---- Tips emails

@app.route('/api/v1/admin/tips', methods=['GET'])
@app.route('/api/v1/admin/tips/', methods=['GET'])
@admin_required
def admin_tips():
  return jsonify(tips_emails.overview(db_session))


def find_tip(key):
  tips, _ = tips_emails.current_tips()
  return next((t for t in tips if t.key == key), None), tips


def me():
  actor = g.get('actor')
  return actor.user if actor is not None else None


@app.route('/api/v1/admin/tips/<key>/preview', methods=['GET'])
@admin_required
def admin_tip_preview(key):
  """The email as the current admin would get it"""
  tip, tips = find_tip(key)
  if tip is None:
    return jsonify({"error": f"No tip {key}"}), 404
  user = me()
  info = tips_emails.info_for(user, tips, tip) if user is not None else {'qaboard_url': qaboard_url}
  subject, html_part, text_part = tips_emails.render_tip(tip, info)
  if request.args.get('format') == 'html':
    response = make_response(html_part)
    response.headers['Content-Type'] = 'text/html; charset=utf-8'
    # shown in an iframe of the admin page: no scripts in emails anyway
    response.headers['Content-Security-Policy'] = "default-src 'none'; style-src 'unsafe-inline'; img-src * data:; frame-ancestors 'self'; base-uri 'none'; form-action 'none'"
    return response
  return jsonify({"subject": subject, "html": html_part, "text": text_part})


@app.route('/api/v1/admin/tips/<key>/test', methods=['POST'])
@admin_required
def admin_tip_test(key):
  """Sends the tip to the admin who asks, e.g. to check how Outlook shows it"""
  tip, tips = find_tip(key)
  if tip is None:
    return jsonify({"error": f"No tip {key}"}), 404
  user = me()
  if user is None:
    return jsonify({"error": "Sign in as a person to get test emails"}), 400
  to = recipient_for(user.user_name, user.email)
  if not to:
    return jsonify({"error": "QA-Board doesn't know your email address"}), 400
  info = tips_emails.info_for(user, tips, tip)
  subject, html_part, text_part = tips_emails.render_tip(tip, info)
  try:
    mail.send_email(to, f"[Test] {subject}", html_part, text_part, headers=tips_emails.headers_for(info, tip))
  except Exception as e:
    return jsonify({"error": f"Could not send it: {type(e).__name__}: {e}"}), 502
  return jsonify({"sent_to": to})


@app.route('/api/v1/admin/tips/enroll', methods=['POST'])
@admin_required
def admin_tips_enroll():
  """People who already use QA-Board get the tips too (not the welcome)"""
  data = request.get_json(silent=True) or {}
  try:
    active_days = int(data.get('active_days', 30))
  except (TypeError, ValueError):
    return jsonify({"error": "active_days must be a number of days"}), 400
  if not 1 <= active_days <= 365:
    return jsonify({"error": "active_days must be between 1 and 365"}), 400
  enrolled = tips_emails.enroll_active_users(db_session, active_days)
  audit('tips.enroll', 'tips', None, None, meta={"active_days": active_days, "enrolled": enrolled})
  db_session.commit()
  return jsonify({"enrolled": enrolled})


# ---- Unsubscribing, from the link in the emails: no sign-in needed

PAGE = """<!DOCTYPE html><html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1">
<title>QA-Board tips</title>
<style>body{{margin:0;background:#F0F2F5;font-family:-apple-system,BlinkMacSystemFont,'Segoe UI',Roboto,Helvetica,Arial,sans-serif;color:#1C2127}}
main{{max-width:480px;margin:64px auto;background:#fff;padding:32px;border-top:4px solid #2D72D2}}h1{{font-size:20px;margin:0 0 12px}}
p{{color:#404854;line-height:22px}}button{{background:#2D72D2;color:#fff;border:0;border-radius:3px;padding:10px 18px;font-size:14px;font-weight:600;cursor:pointer}}
a{{color:#1B69B6}}</style></head><body><main>{body}</main></body></html>"""


@app.route('/api/v1/tips/unsubscribe', methods=['GET', 'POST'])
def tips_unsubscribe():
  """
  GET shows a button, POST unsubscribes: mail scanners (e.g. Outlook's Safe Links) open the links of emails, and must
  not unsubscribe people. Email clients' "Unsubscribe" buttons POST (RFC 8058).
  """
  key = request.values.get('key') or ''
  user_id = tips_emails.user_id_of(key)
  user = db_session.get(User, user_id) if user_id is not None else None
  settings_url = f"{qaboard_url.rstrip('/')}/settings/preferences"
  if user is None:
    body = f"<h1>This link doesn't work</h1><p>Turn the tips off in <a href=\"{html.escape(settings_url)}\">Settings → Preferences</a>.</p>"
    return PAGE.format(body=body), 404
  if request.method == 'POST':
    if tips_emails.wants_tips(user):
      tips_emails.set_wants_tips(user, False)
      audit('tips.unsubscribe', 'user', user.id, user.user_name, user=user)
      db_session.commit()
    body = ("<h1>No more tips</h1><p>You won't get QA-Board's tips emails anymore. You still get the emails you ask for, "
            f"e.g. when your batches end.</p><p>Changed your mind? <a href=\"{html.escape(settings_url)}\">Settings → Preferences</a>.</p>")
    return PAGE.format(body=body)
  body = (f"<h1>Stop the tips?</h1><p>{html.escape(user.full_name or user.user_name)}, you get a tip about QA-Board each week, "
          "for a few weeks after you first signed in.</p>"
          f"<form method=\"post\"><input type=\"hidden\" name=\"key\" value=\"{html.escape(key)}\"><button type=\"submit\">Stop the tips</button></form>")
  return PAGE.format(body=body)
