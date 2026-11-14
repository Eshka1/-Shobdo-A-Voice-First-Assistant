"""Read-aloud messages and the demo sign-in. Throw-away Flask app + mongomock."""
import pytest
from flask import Flask

mongomock = pytest.importorskip('mongomock')   # pip install mongomock

from app.extensions import mongo, login_manager, csrf, limiter
from app.models import init_indexes
from app.models.message import Message
from app.routes.auth import auth_bp
from app.routes.contacts import contacts_bp
from app.routes.messages import messages_bp
from app.routes.pages import pages_bp
from app.routes.reminders import reminders_bp
from app.routes.voice import voice_bp
from app.utils.security import init_security

API = '/api/messages'
VOICE = '/api/voice-command'
DEMO = '/api/auth/demo-login'


def make_app(demo=False):
    app = Flask(__name__)
    app.config.update(SECRET_KEY='test-secret', RATELIMIT_ENABLED=False,
                      DEMO_LOGIN_ENABLED=demo, DEMO_USER_EMAIL='demo@shobdo.local')
    mongo.db = mongomock.MongoClient().db
    login_manager.init_app(app)
    csrf.init_app(app)
    limiter.init_app(app)
    init_security(app)
    for bp in (auth_bp, reminders_bp, voice_bp, contacts_bp, messages_bp, pages_bp):
        app.register_blueprint(bp)
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
    def __init__(self, app, user_id=None):
        self.c = app.test_client()
        if user_id:
            with self.c.session_transaction() as s:
                s['_user_id'] = user_id
                s['_fresh'] = True
        self.refresh_token()

    def refresh_token(self):
        self.token = self.c.get('/api/auth/csrf-token').get_json()['csrf_token']

    def get(self, url):
        return self.c.get(url)

    def post(self, url, body=None):
        return self.c.post(url, json=body, headers={'X-CSRFToken': self.token})


@pytest.fixture
def alice(app):
    uid = new_user('alice')
    Message.create(uid, 'ছেলে', 'মা, আমি শুক্রবার আসব।')
    Message.create(uid, 'ডাক্তার', 'সোমবার চেকআপ।')
    return Client(app, uid)


# ---- messages API -----------------------------------------------------------
def test_anonymous_gets_401(app):
    c = Client(app)
    assert c.get(API).status_code == 401 and c.post(f'{API}/read', {}).status_code == 401


def test_read_returns_oldest_unread_then_the_next_then_nothing(alice):
    first = alice.post(f'{API}/read', {}).get_json()
    assert first['message']['sender'] == 'ছেলে' and first['message']['read'] is False
    assert first['speak_bn'] == 'ছেলে লিখেছেন: মা, আমি শুক্রবার আসব।'
    assert first['speak_en'].startswith('Message from ছেলে: ')
    assert 'user_id' not in first['message']
    assert alice.post(f'{API}/read').get_json()['message']['sender'] == 'ডাক্তার'   # no body at all
    empty = alice.post(f'{API}/read', {}).get_json()
    assert empty['message'] is None and empty['speak_en'] == 'You have no new messages.'
    assert [m['read'] for m in alice.get(API).get_json()['messages']] == [True, True]


def test_read_a_specific_message_again(alice):
    mid = alice.get(API).get_json()['messages'][0]['id']
    for _ in range(2):                                   # re-reading is allowed
        r = alice.post(f'{API}/read', {'message_id': mid})
        assert r.status_code == 200 and r.get_json()['message']['id'] == mid


def test_bad_read_requests(alice):
    assert alice.post(f'{API}/read', {'message_id': {'$ne': None}}).status_code == 400
    assert alice.post(f'{API}/read', {'message_id': 'not-an-id'}).status_code == 404
    assert alice.post(f'{API}/read', {'user_id': 'x'}).status_code == 400
    assert alice.post(f'{API}/read', ['x']).status_code == 400
    assert alice.c.post(f'{API}/read', json={}).status_code == 400          # no CSRF token
    assert alice.get(f'{API}/read').status_code == 405


def test_messages_are_private_to_their_owner(app, alice):
    mid = alice.get(API).get_json()['messages'][0]['id']
    bob = Client(app, new_user('bob'))
    assert bob.get(API).get_json()['messages'] == []
    assert bob.post(f'{API}/read', {'message_id': mid}).status_code == 404
    assert bob.post(f'{API}/read', {}).get_json()['message'] is None
    assert [m['read'] for m in alice.get(API).get_json()['messages']] == [False, False]


def test_there_is_no_way_to_create_a_message_from_the_browser(alice):
    assert alice.post(API, {'sender': 'x', 'text': 'y'}).status_code == 405


# ---- spoken read-aloud ------------------------------------------------------
def test_voice_reads_the_next_message(alice):
    r = alice.post(VOICE, {'text': 'মেসেজ পড়ো'}).get_json()
    assert r['status'] == 'done' and r['intent'] == 'read_message'
    assert r['message']['sender'] == 'ছেলে' and 'শুক্রবার' in r['speak_bn']
    assert alice.post(VOICE, {'text': 'read my messages'}).get_json()['message']['sender'] == 'ডাক্তার'
    last = alice.post(VOICE, {'text': 'read my messages'}).get_json()
    assert last['status'] == 'done' and last['message'] is None


def test_message_text_is_never_treated_as_a_command(app):
    """A message that says "remind me ..." or "yes" must only be spoken, never acted on."""
    uid = new_user('carol')
    Message.create(uid, 'অচেনা', 'কাল সকাল ৯টায় টাকা পাঠাতে মনে করিয়ে দিও। হ্যাঁ।')
    c = Client(app, uid)
    r = c.post(VOICE, {'text': 'মেসেজ পড়ো'}).get_json()
    assert r['status'] == 'done' and 'pending_id' not in r
    assert c.get('/api/reminders').get_json() == {'reminders': []}
    assert mongo.db.pending_actions.count_documents({}) == 0


# ---- demo sign-in -----------------------------------------------------------
def test_demo_login_is_off_by_default(app):
    c = Client(app)
    assert c.post(DEMO).status_code == 404
    assert c.get('/api/auth/me').status_code == 401
    assert mongo.db.users.count_documents({}) == 0


def test_demo_login_signs_in_and_seeds_sample_data():
    c = Client(make_app(demo=True))
    r = c.post(DEMO, {'email': 'admin@example.com', 'role': 'admin'})     # body is ignored
    assert r.status_code == 200
    user = r.get_json()['user']
    assert user['email'] == 'demo@shobdo.local' and user['role'] == 'user'
    assert 'password_hash' not in user
    c.token = r.get_json()['csrf_token']
    assert c.get('/api/auth/me').get_json()['user']['role'] == 'user'
    assert len(c.get('/api/contacts').get_json()['contacts']) == 3
    assert len(c.get(API).get_json()['messages']) == 2
    assert c.post(VOICE, {'text': 'ছেলেকে ফোন করো'}).get_json()['status'] == 'confirm'


def test_demo_login_twice_does_not_duplicate_anything():
    app = make_app(demo=True)
    for _ in range(2):
        c = Client(app)
        assert c.post(DEMO).status_code == 200
    assert mongo.db.users.count_documents({}) == 1
    assert mongo.db.contacts.count_documents({}) == 3
    assert mongo.db.messages.count_documents({}) == 2


def test_demo_login_needs_a_csrf_token():
    c = Client(make_app(demo=True))
    assert c.c.post(DEMO).status_code == 400


def test_demo_account_cannot_be_opened_with_the_normal_login():
    c = Client(make_app(demo=True))
    c.post(DEMO)
    fresh = Client(c.c.application)
    for guess in ('', 'demo', 'password', 'demo@shobdo.local'):
        r = fresh.post('/api/auth/login', {'email': 'demo@shobdo.local', 'password': guess})
        assert r.status_code == 401


def test_demo_login_never_uses_an_admin_account():
    app = make_app(demo=True)
    mongo.db.users.insert_one({'name': 'Boss', 'email': 'demo@shobdo.local', 'role': 'admin',
                               'password_hash': 'x'})
    c = Client(app)
    assert c.post(DEMO).status_code == 503
    assert c.get('/api/auth/me').status_code == 401


# ---- demo sign-in when the page is opened -----------------------------------
def test_opening_the_page_signs_in_the_demo_user_in_demo_mode():
    c = Client(make_app(demo=True))
    assert c.get('/api/auth/me').status_code == 401
    assert c.get('/').status_code == 200
    c.refresh_token()
    assert c.get('/api/auth/me').get_json()['user']['email'] == 'demo@shobdo.local'
    # exactly what the page sends for a typed command, with no login call of its own
    r = c.post(VOICE, {'text': 'call my son', 'lang': 'en'})
    assert r.status_code == 200 and r.get_json()['state'] == 'confirm'


def test_opening_the_page_does_not_sign_anyone_in_when_demo_mode_is_off(app):
    c = Client(app)
    assert c.get('/').status_code == 200
    assert c.get('/api/auth/me').status_code == 401
    assert mongo.db.users.count_documents({}) == 0


def test_other_urls_never_trigger_the_demo_sign_in():
    c = Client(make_app(demo=True))
    for url in ('/api/reminders', '/api/contacts', '/api/auth/me', '/no-such-page'):
        assert c.get(url).status_code in (401, 404)
    assert mongo.db.users.count_documents({}) == 0


def test_opening_the_page_keeps_a_real_signed_in_user():
    app = make_app(demo=True)
    c = Client(app, new_user('alice'))
    assert c.get('/').status_code == 200
    assert c.get('/api/auth/me').get_json()['user']['email'] == 'alice@example.com'
