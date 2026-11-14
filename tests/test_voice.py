"""Stage 10 voice-command tests: service logic (fixed clock) and the route (throw-away app + mongomock)."""
import json
from datetime import datetime, timedelta, timezone

import pytest
from bson import ObjectId
from flask import Flask, jsonify

mongomock = pytest.importorskip('mongomock')   # pip install mongomock

from app.extensions import mongo, login_manager, csrf, limiter
from app.models import init_indexes
from app.routes.auth import auth_bp
from app.routes.reminders import reminders_bp
from app.routes.voice import voice_bp
from app.services.bangla_datetime import TZ
from app.services.confirmation import ConfirmationService, InMemoryPendingStore
from app.services.voice_service import describe_time, handle_voice_command
from app.utils.security import init_security

NOW = datetime(2026, 10, 29, 10, 0, tzinfo=TZ)        # Thursday 10:00 Dhaka
VOICE = '/api/voice-command'
CMD = 'কাল সকাল ৯টায় ওষুধ খেতে মনে করিয়ে দিও'      # "remind me tomorrow 9 am to take medicine"


# ---- helpers ---------------------------------------------------------------
@pytest.fixture
def db():
    mongo.db = mongomock.MongoClient().db
    init_indexes()
    return mongo.db


@pytest.fixture
def svc():
    return ConfirmationService(InMemoryPendingStore())


def new_user(name):
    return str(mongo.db.users.insert_one(
        {'name': name, 'email': f'{name}@example.com', 'role': 'user',
         'password_hash': 'x', 'created_at': datetime.now(timezone.utc)}).inserted_id)


def say(svc, user, text, now=NOW, **kw):
    payload, _http = handle_voice_command(svc, user, text, now=now, **kw)
    return payload


def saved(user):
    return mongo.db.reminders.count_documents({'user_id': ObjectId(user)})


# ---- the happy path and "nothing before yes" -------------------------------
def test_full_flow_saves_only_after_yes(db, svc):
    alice = new_user('alice')
    p = say(svc, alice, CMD)
    assert p['status'] == 'confirm' and p['reminder']['task'] == 'ওষুধ খেতে'
    assert 'কাল সকাল ৯টা' in p['speak_bn'] and 'tomorrow at 9:00 AM' in p['speak_en']
    assert saved(alice) == 0                                   # read-back only: nothing saved
    done = say(svc, alice, 'হ্যাঁ', pending_id=p['pending_id'])
    assert done['status'] == 'done' and saved(alice) == 1


def test_the_payload_never_contains_the_user_id(db, svc):
    alice = new_user('alice')
    assert alice not in json.dumps(say(svc, alice, CMD))


# ---- clarification ---------------------------------------------------------
def test_missing_time_asks_and_saves_nothing(db, svc):
    alice = new_user('alice')
    p = say(svc, alice, 'কাল ওষুধ খেতে মনে করিয়ে দিও')
    assert p['status'] == 'clarify' and p['code'] == 'missing_time' and p['task'] == 'ওষুধ খেতে'
    assert 'pending_id' not in p and saved(alice) == 0


def test_answering_the_time_question_leads_to_a_read_back(db, svc):
    alice = new_user('alice')
    p = say(svc, alice, 'রাত ৯টায়', task='ওষুধ খেতে')
    assert p['status'] == 'confirm' and 'আজ রাত ৯টা' in p['speak_bn']
    again = say(svc, alice, 'হয়তো', task='ওষুধ খেতে')
    assert again['status'] == 'clarify' and again['code'] == 'missing_time' and again['task'] == 'ওষুধ খেতে'
    assert saved(alice) == 0


def test_misheard_commands_never_become_actions(db, svc):
    alice = new_user('alice')
    p = say(svc, alice, 'আজ আবহাওয়া কেমন')
    assert p['status'] == 'clarify' and p['code'] == 'unknown_command' and saved(alice) == 0


def test_a_very_long_task_is_refused(db, svc):
    alice = new_user('alice')
    p = say(svc, alice, 'কাল সকাল ৯টায় ' + 'ক' * 250 + ' মনে করিয়ে দিও')
    assert p['status'] == 'clarify' and p['code'] == 'too_long' and saved(alice) == 0


# ---- replies: no, unclear, correction --------------------------------------
@pytest.mark.parametrize('reply', ['না', 'থামো'])
def test_no_or_stop_cancels(db, svc, reply):
    alice = new_user('alice')
    p = say(svc, alice, CMD)
    r = say(svc, alice, reply, pending_id=p['pending_id'])
    assert r['status'] == 'cancelled' and saved(alice) == 0
    assert say(svc, alice, 'হ্যাঁ', pending_id=p['pending_id'])['status'] == 'cancelled'


def test_unclear_reply_keeps_waiting_and_repeats_the_details(db, svc):
    alice = new_user('alice')
    p = say(svc, alice, CMD)
    w = say(svc, alice, 'হয়তো', pending_id=p['pending_id'])
    assert w['status'] == 'waiting' and w['pending_id'] == p['pending_id']
    assert 'কাল সকাল ৯টা' in w['speak_bn'] and saved(alice) == 0
    assert say(svc, alice, 'হ্যাঁ', pending_id=p['pending_id'])['status'] == 'done'


def test_correction_with_no_replaces_the_old_read_back(db, svc):
    alice = new_user('alice')
    p1 = say(svc, alice, CMD)
    p2 = say(svc, alice, 'না, কাল রাত ৮টায় ওষুধ খেতে মনে করিয়ে দিও', pending_id=p1['pending_id'])
    assert p2['status'] == 'confirm' and p2['pending_id'] != p1['pending_id']
    assert p2['reminder']['task'] == 'ওষুধ খেতে'              # the leading "না" is not part of the task
    assert say(svc, alice, 'হ্যাঁ', pending_id=p1['pending_id'])['status'] == 'cancelled'   # old one is dead
    done = say(svc, alice, 'হ্যাঁ', pending_id=p2['pending_id'])
    assert done['status'] == 'done' and done['reminder']['remind_at'].startswith('2026-10-30T20:00')
    assert saved(alice) == 1


def test_a_long_correction_without_a_no_word_also_replaces(db, svc):
    alice = new_user('alice')
    p1 = say(svc, alice, CMD)
    p2 = say(svc, alice, 'কাল রাত ৮টায় ওষুধ খেতে মনে করিয়ে দিও', pending_id=p1['pending_id'])
    assert p2['status'] == 'confirm' and p2['pending_id'] != p1['pending_id'] and saved(alice) == 0


def test_an_incomplete_correction_voids_the_old_read_back(db, svc):
    alice = new_user('alice')
    p1 = say(svc, alice, CMD)
    c = say(svc, alice, 'ঘুমের ওষুধ মনে করিয়ে দিও', pending_id=p1['pending_id'])
    assert c['status'] == 'clarify' and c['code'] == 'missing_time'
    assert say(svc, alice, 'হ্যাঁ', pending_id=p1['pending_id'])['status'] == 'cancelled'
    assert saved(alice) == 0


# ---- safety: duplicates, expiry, other users -------------------------------
def test_a_second_yes_is_a_duplicate_and_saves_nothing_more(db, svc):
    alice = new_user('alice')
    p = say(svc, alice, CMD)
    assert say(svc, alice, 'হ্যাঁ', pending_id=p['pending_id'])['status'] == 'done'
    r = say(svc, alice, 'হ্যাঁ', pending_id=p['pending_id'])
    assert r['status'] == 'failed' and r['code'] == 'duplicate' and saved(alice) == 1


def test_an_expired_confirmation_saves_nothing(db, svc):
    alice = new_user('alice')
    p = say(svc, alice, CMD)
    r = say(svc, alice, 'হ্যাঁ', pending_id=p['pending_id'], now=NOW + timedelta(minutes=3))
    assert r['status'] == 'failed' and r['code'] == 'expired' and saved(alice) == 0


def test_another_user_cannot_confirm_my_pending_action(db, svc):
    alice, bob = new_user('alice'), new_user('bob')
    p = say(svc, alice, CMD)
    r = say(svc, bob, 'হ্যাঁ', pending_id=p['pending_id'])
    assert r['status'] == 'failed' and r['code'] == 'not_found'
    assert saved(alice) == 0 and saved(bob) == 0


def test_a_made_up_pending_id_is_not_found(db, svc):
    r = say(svc, new_user('alice'), 'হ্যাঁ', pending_id='made-up-id')
    assert r['status'] == 'failed' and r['code'] == 'not_found'


# ---- controls and features that are not built yet --------------------------
@pytest.mark.parametrize('text,action', [('আবার বলো', 'repeat'), ('আস্তে বলো', 'slow_down'), ('থামো', 'stop')])
def test_control_commands_are_returned_to_the_browser(db, svc, text, action):
    p = say(svc, new_user('alice'), text)
    assert p['status'] == 'control' and p['action'] == action


def test_repeat_while_waiting_does_not_cancel(db, svc):
    alice = new_user('alice')
    p = say(svc, alice, CMD)
    c = say(svc, alice, 'আবার বলো', pending_id=p['pending_id'])
    assert c['status'] == 'control' and c['action'] == 'repeat'
    assert say(svc, alice, 'হ্যাঁ', pending_id=p['pending_id'])['status'] == 'done'


@pytest.mark.parametrize('text', ['আম্মুকে ফোন করো', 'মেসেজ পড়ে শোনাও'])
def test_call_and_read_message_are_not_available_yet(db, svc, text):
    alice = new_user('alice')
    p = say(svc, alice, text)
    assert p['status'] == 'unsupported' and saved(alice) == 0


# ---- read-back wording -----------------------------------------------------
def test_describe_time_uses_bangla_digits_and_day_words():
    at = lambda d, h, m=0: datetime(2026, 10, d, h, m, tzinfo=TZ)
    assert describe_time(at(30, 21), NOW) == ('কাল রাত ৯টা', 'tomorrow at 9:00 PM')
    assert describe_time(at(30, 9, 30), NOW) == ('কাল সকাল ৯টা ৩০ মিনিট', 'tomorrow at 9:30 AM')
    assert describe_time(at(29, 17), NOW) == ('আজ বিকাল ৫টা', 'today at 5:00 PM')
    assert describe_time(at(31, 14), NOW) == ('৩১ অক্টোবর দুপুর ২টা', 'October 31 at 2:00 PM')


# ============================== the route ====================================
def make_app(rate_limit=False):
    app = Flask(__name__)
    app.config.update(SECRET_KEY='test-secret', RATELIMIT_ENABLED=rate_limit)
    mongo.db = mongomock.MongoClient().db
    login_manager.init_app(app)
    csrf.init_app(app)
    limiter.init_app(app)
    init_security(app)
    app.register_blueprint(auth_bp)
    app.register_blueprint(reminders_bp)
    app.register_blueprint(voice_bp)

    @app.errorhandler(403)
    def forbidden(error):
        return jsonify({'error': 'Forbidden'}), 403

    with app.app_context():
        init_indexes()
    return app


@pytest.fixture
def app():
    return make_app()


class Client:
    def __init__(self, app, user_id=None):
        self.c = app.test_client()
        if user_id:
            with self.c.session_transaction() as s:
                s['_user_id'] = user_id
                s['_fresh'] = True
        self.token = self.c.get('/api/auth/csrf-token').get_json()['csrf_token']

    def get(self, url):
        return self.c.get(url)

    def post(self, url, body=None):
        return self.c.post(url, json=body, headers={'X-CSRFToken': self.token})


def test_anonymous_gets_401(app):
    r = Client(app).post(VOICE, {'text': CMD})
    assert r.status_code == 401 and r.get_json() == {'error': 'Authentication required'}


def test_only_post_is_allowed(app):
    assert Client(app, new_user('alice')).get(VOICE).status_code == 405


def test_csrf_token_is_required(app):
    c = Client(app, new_user('alice'))
    assert c.c.post(VOICE, json={'text': CMD}).status_code == 400


def test_the_browser_cannot_send_confirmed_true(app):
    r = Client(app, new_user('alice')).post(VOICE, {'text': 'হ্যাঁ', 'pending_id': 'x', 'confirmed': True})
    assert r.status_code == 400 and 'confirmed' in r.get_json()['error']


@pytest.mark.parametrize('body', [
    {}, ['not', 'an', 'object'], {'text': 5}, {'text': 'x', 'pending_id': 5},
    {'text': 'x', 'pending_id': {'$ne': None}}, {'text': 'x', 'task': 5},
    {'text': 'x', 'task': '   '}, {'text': 'x', 'task': 'a' * 500},
    {'text': 'x', 'task': 'a', 'pending_id': 'b'},
])
def test_bad_bodies_are_400(app, body):
    assert Client(app, new_user('alice')).post(VOICE, body).status_code == 400


def test_end_to_end_through_the_route(app):
    alice, bob = new_user('alice'), new_user('bob')
    a, b = Client(app, alice), Client(app, bob)
    p = a.post(VOICE, {'text': CMD}).get_json()
    assert p['status'] == 'confirm' and a.get('/api/reminders').get_json() == {'reminders': []}
    r = a.post(VOICE, {'text': 'হ্যাঁ', 'pending_id': p['pending_id']})
    assert r.status_code == 200 and r.content_type == 'application/json' and r.get_json()['status'] == 'done'
    items = a.get('/api/reminders').get_json()['reminders']
    assert len(items) == 1 and items[0]['title'] == 'ওষুধ খেতে'
    assert alice not in r.get_data(as_text=True)
    p2 = a.post(VOICE, {'text': 'কাল রাত ৮টায় চা খেতে মনে করিয়ে দিও'}).get_json()
    other = b.post(VOICE, {'text': 'হ্যাঁ', 'pending_id': p2['pending_id']}).get_json()
    assert other['status'] == 'failed' and other['code'] == 'not_found'
    assert b.get('/api/reminders').get_json() == {'reminders': []}


def test_script_tags_in_speech_are_neutralised(app):
    c = Client(app, new_user('alice'))
    r = c.post(VOICE, {'text': 'কাল সকাল ৯টায় <script>alert(1)</script> মনে করিয়ে দিও'})
    body = r.get_json()
    assert body['status'] == 'confirm'
    assert '<' not in r.get_data(as_text=True) and '>' not in r.get_data(as_text=True)


def test_script_tags_in_the_task_field_are_neutralised(app):
    c = Client(app, new_user('alice'))
    r = c.post(VOICE, {'text': 'রাত ৯টায়', 'task': '<img src=x onerror=alert(1)>'})
    assert r.get_json()['status'] == 'confirm'
    assert '<' not in r.get_data(as_text=True) and '>' not in r.get_data(as_text=True)


def test_voice_command_is_rate_limited_to_30_per_minute():
    app = make_app(rate_limit=True)
    c = Client(app, new_user('alice'))
    codes = [c.post(VOICE, {'text': 'আবার বলো'}).status_code for _ in range(31)]
    assert codes[:30] == [200] * 30 and codes[30] == 429
