"""Reminder API tests. Uses a throw-away Flask app and an in-memory fake MongoDB
(mongomock), so it never touches your real database."""
from datetime import datetime, timedelta, timezone

import pytest
from flask import Flask
from pymongo.errors import PyMongoError

mongomock = pytest.importorskip('mongomock')   # pip install mongomock

from app.extensions import mongo, login_manager, csrf, limiter
from app.models import init_indexes
from app.models.reminder import Reminder
from app.routes.auth import auth_bp
from app.routes.reminders import reminders_bp
from app.services.bangla_datetime import TZ
from app.services.confirmation import ConfirmationService, MongoPendingStore
from app.utils.security import init_security

API = '/api/reminders'


def make_app(rate_limits=False):
    app = Flask(__name__)
    app.config.update(SECRET_KEY='test-secret', RATELIMIT_ENABLED=rate_limits)
    mongo.db = mongomock.MongoClient().db
    login_manager.init_app(app)
    csrf.init_app(app)
    limiter.init_app(app)
    init_security(app)
    app.register_blueprint(auth_bp)
    app.register_blueprint(reminders_bp)
    with app.app_context():
        init_indexes()
    return app


@pytest.fixture
def app():
    return make_app()


def new_user(name):
    return str(mongo.db.users.insert_one(
        {'name': name, 'email': f'{name}@example.com', 'role': 'user'}).inserted_id)


class Client:
    """A browser that is logged in as one user and sends CSRF tokens like the real frontend."""

    def __init__(self, app, user_id=None):
        self.c = app.test_client()
        self.user_id = user_id
        if user_id:
            with self.c.session_transaction() as s:
                s['_user_id'] = user_id
                s['_fresh'] = True
        self.token = self.c.get('/api/auth/csrf-token').get_json()['csrf_token']

    def get(self, url):
        return self.c.get(url)

    def post(self, url, body=None, csrf_token=True):
        headers = {'X-CSRFToken': self.token} if csrf_token else {}
        return self.c.post(url, json=body, headers=headers)


def pending(app, user_id, task='ওষুধ খেতে', hours=24, now=None, **kw):
    """What POST /api/voice-command will do later: store a pending reminder on the server."""
    when = (datetime.now(TZ) + timedelta(hours=hours)).replace(microsecond=0)
    svc = ConfirmationService(MongoPendingStore(mongo.db.pending_actions))
    with app.app_context():
        return svc.propose(user_id, 'reminder', {'task': task, 'remind_at': when.isoformat()},
                           'সারাংশ', 'Summary', now=now, **kw)['pending_id']


def pending_state(pending_id):
    return mongo.db.pending_actions.find_one({'_id': pending_id})['state']


# ---- authentication --------------------------------------------------------
def test_everything_requires_login(app):
    anon = Client(app)
    assert anon.get(API).status_code == 401
    assert anon.post(API, {'pending_id': 'x'}).status_code == 401
    assert anon.post(f'{API}/abc/complete').status_code == 401
    assert anon.post(f'{API}/abc/cancel').status_code == 401
    assert anon.get(API).get_json() == {'error': 'Authentication required'}


# ---- create: happy path ----------------------------------------------------
def test_create_then_list_after_refresh(app):
    alice = Client(app, new_user('alice'))
    pid = pending(app, alice.user_id)
    r = alice.post(API, {'pending_id': pid})
    assert r.status_code == 201
    created = r.get_json()['reminder']
    assert created['title'] == 'ওষুধ খেতে' and created['status'] == 'pending'
    assert created['remind_at'].endswith('+06:00')           # unambiguous Dhaka time
    assert 'user_id' not in created and 'idempotency_key' not in created
    assert pending_state(pid) == 'completed'
    # a "page refresh" is a brand new browser session for the same user
    again = Client(app, alice.user_id).get(API).get_json()['reminders']
    assert [x['id'] for x in again] == [created['id']]


def test_list_filter_and_invalid_filter(app):
    alice = Client(app, new_user('alice'))
    rid = alice.post(API, {'pending_id': pending(app, alice.user_id)}).get_json()['reminder']['id']
    alice.post(f'{API}/{rid}/complete')
    assert alice.get(f'{API}?status=pending').get_json()['reminders'] == []
    assert len(alice.get(f'{API}?status=completed').get_json()['reminders']) == 1
    assert alice.get(f'{API}?status=hacked').status_code == 400


# ---- create: confirmation cannot be bypassed -------------------------------
def test_missing_csrf_token_is_rejected(app):
    alice = Client(app, new_user('alice'))
    pid = pending(app, alice.user_id)
    assert alice.post(API, {'pending_id': pid}, csrf_token=False).status_code == 400
    assert pending_state(pid) == 'awaiting_confirmation'
    assert mongo.db.reminders.count_documents({}) == 0


@pytest.mark.parametrize('body', [
    {'confirmed': True, 'title': 'free reminder', 'remind_at': '2030-01-01T09:00:00+06:00'},
    {'pending_id': 'x', 'title': 'sneaky'},
    {'pending_id': 'x', 'user_id': 'someone-else'},
    {}, [], 'text', None,
])
def test_browser_cannot_bypass_confirmation(app, body):
    alice = Client(app, new_user('alice'))
    assert alice.post(API, body).status_code == 400
    assert mongo.db.reminders.count_documents({}) == 0


@pytest.mark.parametrize('bad', [{'$ne': None}, ['a'], 123, None])
def test_non_string_pending_id_rejected(app, bad):
    alice = Client(app, new_user('alice'))
    assert alice.post(API, {'pending_id': bad}).status_code == 400


def test_made_up_pending_id_is_404(app):
    alice = Client(app, new_user('alice'))
    r = alice.post(API, {'pending_id': 'made-up-by-the-browser'})
    assert r.status_code == 404 and r.get_json()['code'] == 'not_found'
    assert mongo.db.reminders.count_documents({}) == 0


def test_cannot_use_another_users_pending_action(app):
    alice, bob = Client(app, new_user('alice')), Client(app, new_user('bob'))
    pid = pending(app, alice.user_id)
    assert bob.post(API, {'pending_id': pid}).status_code == 404
    assert mongo.db.reminders.count_documents({}) == 0
    assert alice.post(API, {'pending_id': pid}).status_code == 201     # still Alice's to confirm


def test_same_confirmation_twice_creates_one_reminder(app):
    alice = Client(app, new_user('alice'))
    pid = pending(app, alice.user_id)
    assert alice.post(API, {'pending_id': pid}).status_code == 201
    second = alice.post(API, {'pending_id': pid})
    assert second.status_code == 409 and second.get_json()['code'] == 'duplicate'
    assert mongo.db.reminders.count_documents({}) == 1


def test_expired_pending_action_is_410(app):
    alice = Client(app, new_user('alice'))
    long_ago = datetime.now(timezone.utc) - timedelta(minutes=10)
    pid = pending(app, alice.user_id, now=long_ago)
    r = alice.post(API, {'pending_id': pid})
    assert r.status_code == 410 and r.get_json()['code'] == 'expired'
    assert mongo.db.reminders.count_documents({}) == 0


def test_cancelled_pending_action_cannot_create(app):
    alice = Client(app, new_user('alice'))
    pid = pending(app, alice.user_id)
    ConfirmationService(MongoPendingStore(mongo.db.pending_actions)).cancel(alice.user_id, pid)
    assert alice.post(API, {'pending_id': pid}).status_code == 409
    assert mongo.db.reminders.count_documents({}) == 0


def test_time_that_passed_before_confirmation_is_rejected(app):
    alice = Client(app, new_user('alice'))
    pid = pending(app, alice.user_id, hours=-1)
    r = alice.post(API, {'pending_id': pid})
    assert r.status_code == 400 and r.get_json()['code'] == 'past'
    assert pending_state(pid) == 'error' and mongo.db.reminders.count_documents({}) == 0


# ---- duplicates ------------------------------------------------------------
def test_duplicate_pending_reminder_is_blocked_by_the_database(app):
    alice = Client(app, new_user('alice'))
    when = (datetime.now(TZ) + timedelta(hours=24)).replace(microsecond=0).isoformat()
    svc = ConfirmationService(MongoPendingStore(mongo.db.pending_actions))
    ids = []
    for _ in range(2):          # same words said twice, each confirmed separately
        with app.app_context():
            p = svc.propose(alice.user_id, 'reminder', {'task': 'ওষুধ', 'remind_at': when}, 'a', 'b')
        ids.append(p['pending_id'])
        if len(ids) == 1:
            assert alice.post(API, {'pending_id': p['pending_id']}).status_code == 201
    second = alice.post(API, {'pending_id': ids[1]})
    assert second.status_code == 409 and second.get_json()['code'] == 'duplicate_reminder'
    assert mongo.db.reminders.count_documents({}) == 1


def test_cancelled_reminder_can_be_created_again(app):
    alice = Client(app, new_user('alice'))
    when = (datetime.now(TZ) + timedelta(hours=24)).replace(microsecond=0).isoformat()
    svc = ConfirmationService(MongoPendingStore(mongo.db.pending_actions))

    def create():
        with app.app_context():
            p = svc.propose(alice.user_id, 'reminder', {'task': 'ওষুধ', 'remind_at': when}, 'a', 'b')
        return alice.post(API, {'pending_id': p['pending_id']})

    first = create()
    assert first.status_code == 201
    alice.post(f"{API}/{first.get_json()['reminder']['id']}/cancel")
    assert create().status_code == 201


# ---- database failure ------------------------------------------------------
def test_database_failure_is_reported_not_hidden(app, monkeypatch):
    alice = Client(app, new_user('alice'))
    pid = pending(app, alice.user_id)

    def boom(*a, **k):
        raise PyMongoError('db down')
    monkeypatch.setattr(Reminder, 'create', staticmethod(boom))
    r = alice.post(API, {'pending_id': pid})
    assert r.status_code == 503 and 'Nothing was saved' in r.get_json()['error']
    assert pending_state(pid) == 'error' and mongo.db.reminders.count_documents({}) == 0


# ---- complete / cancel + cross-user access (IDOR) --------------------------
def make_reminder(app, client, task='ওষুধ'):
    pid = pending(app, client.user_id, task=task)
    return client.post(API, {'pending_id': pid}).get_json()['reminder']['id']


def test_complete_and_cancel_rules(app):
    alice = Client(app, new_user('alice'))
    a, b = make_reminder(app, alice, 'এক'), make_reminder(app, alice, 'দুই')
    r = alice.post(f'{API}/{a}/complete')
    assert r.status_code == 200 and r.get_json()['reminder']['status'] == 'completed'
    assert alice.post(f'{API}/{a}/complete').status_code == 409      # already completed
    assert alice.post(f'{API}/{a}/cancel').status_code == 409        # cannot cancel a completed one
    assert alice.post(f'{API}/{b}/cancel').get_json()['reminder']['status'] == 'cancelled'
    assert alice.post(f'{API}/{b}/complete').status_code == 409      # cannot complete a cancelled one


def test_other_user_cannot_see_complete_or_cancel(app):
    alice, bob = Client(app, new_user('alice')), Client(app, new_user('bob'))
    rid = make_reminder(app, alice)
    assert bob.get(API).get_json()['reminders'] == []
    for action in ('complete', 'cancel'):
        r = bob.post(f'{API}/{rid}/{action}')
        assert r.status_code == 404                                   # same as "does not exist"
        assert r.get_json() == {'error': 'Not found', 'code': 'not_found'}
    assert alice.get(API).get_json()['reminders'][0]['status'] == 'pending'


@pytest.mark.parametrize('rid', ['abc', '123', 'z' * 24, "{'$ne': 1}", '0' * 24])
def test_invalid_or_unknown_reminder_id_is_404(app, rid):
    alice = Client(app, new_user('alice'))
    assert alice.post(f'{API}/{rid}/complete').status_code == 404
    assert alice.post(f'{API}/{rid}/cancel').status_code == 404


def test_wrong_http_method_is_405(app):
    alice = Client(app, new_user('alice'))
    assert alice.get(f'{API}/abc/complete').status_code == 405


# ---- rate limit ------------------------------------------------------------
def test_creation_is_rate_limited_to_20_per_minute():
    app = make_app(rate_limits=True)
    alice = Client(app, new_user('alice'))
    codes = [alice.post(API, {'pending_id': 'nope'}).status_code for _ in range(21)]
    assert codes[:20] == [404] * 20 and codes[20] == 429


# ---- model-level ownership (defence in depth) ------------------------------
def test_model_never_returns_another_users_reminder(app):
    alice, bob = Client(app, new_user('alice')), Client(app, new_user('bob'))
    rid = make_reminder(app, alice)
    assert Reminder.find_by_id(rid, alice.user_id) is not None
    assert Reminder.find_by_id(rid, bob.user_id) is None
    assert Reminder.find_by_user(bob.user_id) == []
    assert Reminder.transition(rid, bob.user_id, 'cancelled') == 'not_found'
    assert Reminder.delete(rid, bob.user_id) is False
    assert Reminder.find_by_id(rid, 'not-an-id') is None
    assert Reminder.find_by_user('not-an-id') == []
