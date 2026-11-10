"""Talking to users: announcements (banners), the tips emails, the recipients admins copy in Outlook"""
import datetime

import pytest

from .conftest import login


@pytest.fixture
def admin(logged_in):
  return logged_in('root', is_admin=True)


def iso(date):
  return date.replace(microsecond=0).isoformat() + 'Z'


now = datetime.datetime.utcnow


# ---------------------------------------------------------------------------
# Announcements
# ---------------------------------------------------------------------------

def test_announcements_show_between_their_dates(admin, client):
  maintenance = {
    "title": "QA-Board maintenance on Sunday", "message": "Uploads fail during the upgrade.", "intent": "warning",
    "starts_at": iso(now() - datetime.timedelta(minutes=1)), "ends_at": iso(now() + datetime.timedelta(days=3)),
    "event_starts_at": iso(now() + datetime.timedelta(days=2)), "event_ends_at": iso(now() + datetime.timedelta(days=2, hours=2)),
    "link_url": "https://status.example.com", "link_label": "Status page", "dismissible": False,
  }
  response = admin.post('/api/v1/admin/announcements', json=maintenance)
  assert response.status_code == 201, response.get_json()
  created = response.get_json()
  assert created['revision'] == 0 and created['dismissible'] is False
  later = admin.post('/api/v1/admin/announcements', json={"title": "Next week", "starts_at": iso(now() + datetime.timedelta(days=7))})
  assert later.status_code == 201
  ended = admin.post('/api/v1/admin/announcements', json={
    "title": "Over", "starts_at": iso(now() - datetime.timedelta(days=2)), "ends_at": iso(now() - datetime.timedelta(days=1))})
  assert ended.status_code == 201

  # anyone sees the current ones, even signed out
  shown = client.get('/api/v1/announcements').get_json()['announcements']
  assert [a['title'] for a in shown] == ["QA-Board maintenance on Sunday"]
  assert shown[0]['calendar_url'] == f"/api/v1/announcements/{created['id']}/calendar.ics"

  # admins see the upcoming and the past ones apart
  listed = admin.get('/api/v1/admin/announcements').get_json()
  assert {a['title'] for a in listed['announcements']} == {"QA-Board maintenance on Sunday", "Next week"}
  assert [a['title'] for a in listed['past']] == ["Over"]

  # edits bump the revision: dismissed banners show again, calendars update the event
  edited = admin.put(f"/api/v1/admin/announcements/{created['id']}", json={"message": "Uploads and runs fail."}).get_json()
  assert edited['revision'] == 1 and edited['title'] == maintenance['title']
  assert admin.delete(f"/api/v1/admin/announcements/{created['id']}").status_code == 200
  assert client.get('/api/v1/announcements').get_json()['announcements'] == []


def test_announcements_are_validated(admin):
  for body, error in [
    ({}, "title"),
    ({"title": "x", "intent": "loud"}, "intent"),
    ({"title": "x", "starts_at": "tomorrow"}, "ISO 8601"),
    ({"title": "x", "starts_at": iso(now()), "ends_at": iso(now() - datetime.timedelta(hours=1))}, "end after"),
    ({"title": "x", "link_url": "javascript:alert(1)"}, "link"),
    ({"title": "x" * 200}, "at most"),
  ]:
    response = admin.post('/api/v1/admin/announcements', json=body)
    assert response.status_code == 400 and error in response.get_json()['error'], (body, response.get_json())


def test_maintenance_calendar_file(admin, client):
  start = datetime.datetime(2026, 11, 22, 18, 0)
  created = admin.post('/api/v1/admin/announcements', json={
    "title": "Maintenance, upgrade to PostgreSQL 16", "message": "Line one;\nline two, with commas",
    "event_starts_at": "2026-11-22T20:00:00+02:00", "event_ends_at": "2026-11-22T22:00:00+02:00",
  }).get_json()
  assert created['event_starts_at'].startswith(start.isoformat())
  response = client.get(created['calendar_url'])
  assert response.status_code == 200
  assert response.headers['Content-Type'].startswith('text/calendar')
  assert 'attachment; filename="maintenance-upgrade-to-postgresql-16.ics"' == response.headers['Content-Disposition']
  ics = response.get_data(as_text=True)
  assert ics.startswith('BEGIN:VCALENDAR\r\n') and ics.endswith('END:VCALENDAR\r\n')
  assert 'DTSTART:20261122T180000Z' in ics and 'DTEND:20261122T200000Z' in ics
  assert 'SUMMARY:Maintenance\\, upgrade to PostgreSQL 16' in ics
  assert 'Line one\\;\\nline two\\, with commas' in ics.replace('\r\n ', '')
  # it doesn't block people's calendars
  assert 'X-MICROSOFT-CDO-BUSYSTATUS:FREE' in ics and 'TRANSP:TRANSPARENT' in ics
  assert all(len(line.encode()) <= 75 for line in ics.split('\r\n'))
  # without an event, no calendar
  plain = admin.post('/api/v1/admin/announcements', json={"title": "News"}).get_json()
  assert plain['calendar_url'] is None
  assert client.get(f"/api/v1/announcements/{plain['id']}/calendar.ics").status_code == 404


# ---------------------------------------------------------------------------
# Recipients
# ---------------------------------------------------------------------------

def test_recipients_to_paste_in_outlook(admin, make_user):
  from backend.database import db_session
  from backend.models import User, UsageEvent
  make_user('alice', full_name='Alice Martin', last_login_at=now())
  make_user('bob', full_name='Bob, Jr.', last_login_at=now() - datetime.timedelta(days=60))
  make_user('carol', status='disabled', last_login_at=now())
  make_user('robot', kind='service', last_login_at=now())
  bob = User.query.filter(User.user_name == 'bob').one()
  db_session.add(UsageEvent(source='webapp', event='page_view', user_id=bob.id, user_name='bob', project_id='isp/denoiser',
                            created_at=now() - datetime.timedelta(days=2), received_at=now(), properties={}))
  db_session.commit()

  everyone = admin.get('/api/v1/admin/recipients?active_days=0').get_json()
  assert {r['user_name'] for r in everyone['recipients']} == {'root', 'alice', 'bob'}
  recent = admin.get('/api/v1/admin/recipients?active_days=30').get_json()
  # bob didn't sign in lately, but used the web app
  assert {r['user_name'] for r in recent['recipients']} == {'root', 'alice', 'bob'}
  assert 'Alice Martin <alice@example.com>' in recent['to']
  assert '"Bob, Jr." <bob@example.com>' in recent['to']
  assert recent['to'].count('; ') == 2
  on_project = admin.get('/api/v1/admin/recipients?active_days=30&project=isp').get_json()
  assert [r['user_name'] for r in on_project['recipients']] == ['bob']
  assert admin.get('/api/v1/admin/recipients?active_days=30&project=isp/other').get_json()['recipients'] == []


# ---------------------------------------------------------------------------
# Tips emails
# ---------------------------------------------------------------------------

@pytest.fixture
def outbox(monkeypatch):
  """The emails we'd send"""
  from backend.notifications import tips_emails, mail
  sent = []
  monkeypatch.setenv('QABOARD_SMTP_HOST', 'smtp.example.com')
  monkeypatch.setattr(mail, 'send_email', lambda to, subject, html, text, headers=None, settings=None: sent.append({"to": to, "subject": subject, "html": html, "text": text, "headers": headers}))
  return sent


@pytest.fixture
def tips_on(monkeypatch):
  monkeypatch.setenv('QABOARD_TIPS_EMAILS', '1')


def send_due(when=None):
  from backend.database import db_session
  from backend.notifications.tips_emails import send_due_tips
  return send_due_tips(db_session, now=when)


def test_first_sign_in_enrolls_and_welcomes(tips_on, outbox, make_user, make_client):
  from backend.models import User, UserMessage
  make_user('alice')
  login(make_client(), 'alice')
  alice = User.query.filter(User.user_name == 'alice').one()
  assert alice.tips_started_at is not None
  assert send_due() == {'sent': 1}
  assert outbox[0]['to'] == 'alice@example.com'
  assert outbox[0]['subject'].startswith('Welcome to QA-Board')
  assert outbox[0]['headers']['List-Unsubscribe-Post'] == 'List-Unsubscribe=One-Click'
  # nothing more until a week later
  assert send_due() == {}
  assert send_due(now() + datetime.timedelta(days=7, minutes=1)) == {'sent': 1}
  assert 'Tip 1 of' in outbox[1]['html']
  keys = [m.key for m in UserMessage.query.order_by(UserMessage.id)]
  assert keys[0] == 'welcome' and len(set(keys)) == 2
  # signing in again doesn't enroll again
  login(make_client(), 'alice')
  assert send_due() == {}


def test_no_tips_when_off(outbox, make_user, make_client):
  from backend.models import User
  make_user('alice')
  login(make_client(), 'alice')
  assert User.query.filter(User.user_name == 'alice').one().tips_started_at is None
  assert send_due() == {}


def test_enroll_existing_users_skips_the_welcome(tips_on, outbox, admin, make_user):
  make_user('alice', last_login_at=now() - datetime.timedelta(days=3))
  make_user('old', last_login_at=now() - datetime.timedelta(days=300))
  # root signed in as the admin: enrolled at their first sign-in
  response = admin.post('/api/v1/admin/tips/enroll', json={"active_days": 30})
  assert response.get_json() == {"enrolled": 1}
  send_due()
  subjects = {m['to']: m['subject'] for m in outbox}
  assert subjects['root@example.com'].startswith('Welcome')
  assert not subjects['alice@example.com'].startswith('Welcome')
  assert 'old@example.com' not in subjects
  overview = admin.get('/api/v1/admin/tips').get_json()
  assert overview['people']['enrolled'] == 2 and overview['people']['waiting'] == 2
  assert overview['tips'][0]['key'] == 'welcome' and overview['tips'][0]['sent'] == 1 and overview['tips'][0]['skipped'] == 1


def test_inactive_people_are_paused(tips_on, outbox, make_user, make_client):
  from backend.database import db_session
  from backend.models import User, UserSession
  make_user('alice')
  login(make_client(), 'alice')
  send_due()
  alice = User.query.filter(User.user_name == 'alice').one()
  # 40 days later, she didn't come back
  later = now() + datetime.timedelta(days=40)
  assert send_due(later) == {}
  # she's back
  alice.last_login_at = later
  db_session.commit()
  assert send_due(later) == {'sent': 1}


def test_failures_are_retried_then_given_up(tips_on, monkeypatch, make_user, make_client):
  from backend.notifications import mail
  from backend.models import UserMessage
  attempts = []
  def failing(*args, **kwargs):
    attempts.append(1)
    raise ConnectionRefusedError("SMTP is down")
  monkeypatch.setenv('QABOARD_SMTP_HOST', 'smtp.example.com')
  monkeypatch.setattr(mail, 'send_email', failing)
  make_user('alice')
  login(make_client(), 'alice')
  assert send_due() == {'failed': 1}
  # not right away
  assert send_due() == {}
  assert send_due(now() + datetime.timedelta(hours=2)) == {'failed': 1}
  assert send_due(now() + datetime.timedelta(hours=4)) == {'failed': 1}
  row = UserMessage.query.one()
  assert row.attempts == 3 and 'SMTP is down' in row.error
  # given up on the welcome: the next tip comes, but not before a week
  assert send_due(now() + datetime.timedelta(hours=6)) == {}
  assert len(attempts) == 3


def test_unsubscribe(tips_on, outbox, make_user, make_client, client):
  from backend.database import db_session
  from backend.models import User
  make_user('alice')
  login(make_client(), 'alice')
  send_due()
  url = outbox[0]['headers']['List-Unsubscribe'].strip('<>')
  path = url[url.index('/api/'):]
  # opening the link (or a mail scanner opening it) doesn't unsubscribe
  page = client.get(path)
  assert page.status_code == 200 and b'Stop the tips' in page.data
  db_session.expire_all()
  assert User.query.filter(User.user_name == 'alice').one().preferences.get('tips_emails') is None
  # one-click from the email client: a POST, no cookies
  response = make_client().post(path, data={"List-Unsubscribe": "One-Click"})
  assert response.status_code == 200 and b'No more tips' in response.data
  db_session.expire_all()
  assert User.query.filter(User.user_name == 'alice').one().preferences['tips_emails'] is False
  assert send_due(now() + datetime.timedelta(days=8)) == {}
  # forged keys don't work
  assert client.post('/api/v1/tips/unsubscribe?key=eyJ1IjoxfQ.forged').status_code == 404


def test_tip_preview_and_test_email(tips_on, outbox, admin):
  preview = admin.get('/api/v1/admin/tips/compare-with-a-release/preview').get_json()
  assert preview['subject'] and '<v:roundrect' in preview['html'] and 'http' in preview['text']
  html = admin.get('/api/v1/admin/tips/welcome/preview?format=html')
  assert html.headers['Content-Type'].startswith('text/html') and "default-src 'none'" in html.headers['Content-Security-Policy']
  assert admin.get('/api/v1/admin/tips/nope/preview').status_code == 404
  sent = admin.post('/api/v1/admin/tips/welcome/test').get_json()
  assert sent == {"sent_to": "root@example.com"}
  assert outbox[-1]['subject'].startswith('[Test] Welcome')
