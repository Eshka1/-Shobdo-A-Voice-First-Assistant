"""Contacts, contact matching and the spoken call flow. Throw-away Flask app + mongomock."""
import pytest
from flask import Flask

mongomock = pytest.importorskip('mongomock')   # pip install mongomock

from app.extensions import mongo, login_manager, csrf, limiter
from app.models import init_indexes
from app.models.contact import Contact, clean_phone
from app.routes.auth import auth_bp
from app.routes.contacts import contacts_bp
from app.routes.reminders import reminders_bp
from app.routes.voice import voice_bp
from app.services.contact_service import match_contact
from app.services.intent import interpret
from app.utils.security import init_security

API = '/api/contacts'
VOICE = '/api/voice-command'


def make_app(rate_limit=False):
    app = Flask(__name__)
    app.config.update(SECRET_KEY='test-secret', RATELIMIT_ENABLED=rate_limit)
    mongo.db = mongomock.MongoClient().db
    login_manager.init_app(app)
    csrf.init_app(app)
    limiter.init_app(app)
    init_security(app)
    for bp in (auth_bp, reminders_bp, voice_bp, contacts_bp):
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
        self.token = self.c.get('/api/auth/csrf-token').get_json()['csrf_token']

    def get(self, url):
        return self.c.get(url)

    def post(self, url, body=None):
        return self.c.post(url, json=body, headers={'X-CSRFToken': self.token})

    def delete(self, url):
        return self.c.delete(url, headers={'X-CSRFToken': self.token})


def people(*names):
    return [{'_id': i, 'name': n, 'aliases': []} for i, n in enumerate(names)]


# ---- matching (pure logic) --------------------------------------------------
def test_exact_match_wins():
    found = match_contact(people('রহিম', 'রহিমা', 'করিম'), 'রহিম')
    assert found['result'] == 'one' and found['matches'][0]['name'] == 'রহিম'


def test_bangla_case_ending_is_ignored():
    assert match_contact(people('ছেলে', 'মেয়ে'), 'ছেলেকে')['matches'][0]['name'] == 'ছেলে'
    assert match_contact(people('রহিম'), 'রহিমের')['matches'][0]['name'] == 'রহিম'


def test_alias_and_english_case_match():
    contacts = [{'_id': 1, 'name': 'ছেলে', 'aliases': ['Son', 'রহিম']}]
    assert match_contact(contacts, 'son')['result'] == 'one'
    assert match_contact(contacts, 'রহিমকে')['result'] == 'one'


def test_similar_names_are_never_chosen_silently():
    found = match_contact(people('Rahim Uddin', 'Rahim Uddim'), 'Rahim Uddi')
    assert found['result'] == 'many' and len(found['matches']) == 2


def test_two_contacts_with_the_same_alias_ask():
    contacts = [{'_id': 1, 'name': 'বড় ছেলে', 'aliases': ['ছেলে']},
                {'_id': 2, 'name': 'ছোট ছেলে', 'aliases': ['ছেলে']}]
    assert match_contact(contacts, 'ছেলেকে')['result'] == 'many'


def test_small_typo_matches_but_unknown_name_does_not():
    assert match_contact(people('Doctor Karim'), 'Doctor Karin')['result'] == 'one'
    assert match_contact(people('ছেলে', 'মেয়ে'), 'পুলিশ')['result'] == 'none'
    assert match_contact(people('ছেলে'), '   ')['result'] == 'none'
    assert match_contact(people('ছেলে'), None)['result'] == 'none'


def test_call_target_keeps_names_starting_with_kor():
    assert interpret('করিমকে ফোন করো')['target'] == 'করিমকে'
    assert interpret('ডাক্তারকে একটা ফোন দাও')['target'] == 'ডাক্তারকে'
    assert interpret('call my son')['target'] == 'son'


def test_clean_phone():
    assert clean_phone('+880 1700-000001') == '+8801700000001'
    assert clean_phone('999') == '999'
    for bad in ('', '12', 'abc', '+88017<script>', '0170000000100000000', None, 123, 'tel:999;x'):
        assert clean_phone(bad) is None


# ---- contacts API -----------------------------------------------------------
def test_anonymous_gets_401(app):
    c = Client(app)
    assert c.get(API).status_code == 401
    assert c.post(API, {'name': 'x', 'phone': '999'}).status_code == 401


def test_create_list_delete(app):
    c = Client(app, new_user('alice'))
    r = c.post(API, {'name': '  ছেলে ', 'phone': '+880 1700-000001', 'aliases': ['Son']})
    assert r.status_code == 201
    contact = r.get_json()['contact']
    assert contact['name'] == 'ছেলে' and contact['phone'] == '+8801700000001'
    assert 'user_id' not in contact
    assert [x['name'] for x in c.get(API).get_json()['contacts']] == ['ছেলে']
    assert c.delete(f"{API}/{contact['id']}").status_code == 200
    assert c.get(API).get_json()['contacts'] == []


@pytest.mark.parametrize('body', [
    {'name': 'x'}, {'phone': '999'}, {'name': '', 'phone': '999'},
    {'name': '<script>', 'phone': '999'}, {'name': 'a' * 61, 'phone': '999'},
    {'name': 'ok', 'phone': 'call me'}, {'name': 'ok', 'phone': {'$ne': None}},
    {'name': {'$ne': None}, 'phone': '999'}, {'name': 'ok', 'phone': '999', 'user_id': 'x'},
    {'name': 'ok', 'phone': '999', 'aliases': 'son'}, {'name': 'ok', 'phone': '999', 'aliases': ['<b>']},
    {'name': 'ok', 'phone': '999', 'aliases': ['a'] * 6}, [], None,
])
def test_bad_contact_is_rejected(app, body):
    c = Client(app, new_user('alice'))
    assert c.post(API, body).status_code == 400
    assert c.get(API).get_json()['contacts'] == []


def test_duplicate_name_is_409(app):
    c = Client(app, new_user('alice'))
    assert c.post(API, {'name': 'Rahim', 'phone': '999'}).status_code == 201
    r = c.post(API, {'name': 'rahim', 'phone': '998'})
    assert r.status_code == 409 and r.get_json()['code'] == 'duplicate_contact'


def test_contacts_are_private_to_their_owner(app):
    alice, bob = Client(app, new_user('alice')), Client(app, new_user('bob'))
    cid = alice.post(API, {'name': 'ছেলে', 'phone': '999'}).get_json()['contact']['id']
    assert bob.get(API).get_json()['contacts'] == []
    assert bob.delete(f'{API}/{cid}').status_code == 404
    assert bob.delete(f'{API}/not-an-id').status_code == 404
    assert bob.post(f'{API}/match', {'name': 'ছেলে'}).get_json()['result'] == 'none'
    assert len(alice.get(API).get_json()['contacts']) == 1


def test_match_route(app):
    c = Client(app, new_user('alice'))
    c.post(API, {'name': 'ছেলে', 'phone': '999'})
    body = c.post(f'{API}/match', {'name': 'ছেলেকে'}).get_json()
    assert body['result'] == 'one' and body['matches'][0]['name'] == 'ছেলে'
    assert c.post(f'{API}/match', {'name': {'$ne': None}}).status_code == 400
    assert c.post(f'{API}/match', {}).status_code == 400


# ---- spoken call flow -------------------------------------------------------
@pytest.fixture
def alice(app):
    uid = new_user('alice')
    Contact.create(uid, 'ছেলে', '+8801700000001', ['son'])
    Contact.create(uid, 'মেয়ে', '+8801700000002', ['daughter'])
    return Client(app, uid)


def test_call_is_read_back_and_needs_yes(alice):
    r = alice.post(VOICE, {'text': 'ছেলেকে ফোন করো', 'lang': 'bn', 'pending_id': None}).get_json()
    assert r['status'] == 'confirm' and r['intent'] == 'call' and r['contact'] == {'name': 'ছেলে'}
    assert 'tel' not in r and '+880' not in str(r)          # no number before the "yes"
    done = alice.post(VOICE, {'text': 'হ্যাঁ', 'pending_id': r['pending_id']}).get_json()
    assert done['status'] == 'done' and done['action'] == 'open_dialer'
    assert done['tel'] == 'tel:+8801700000001'
    again = alice.post(VOICE, {'text': 'হ্যাঁ', 'pending_id': r['pending_id']}).get_json()
    assert again['status'] == 'failed' and 'tel' not in again     # one "yes", one handoff


def test_no_cancels_the_call(alice):
    r = alice.post(VOICE, {'text': 'call my son'}).get_json()
    cancelled = alice.post(VOICE, {'text': 'না', 'pending_id': r['pending_id']}).get_json()
    assert cancelled['status'] == 'cancelled' and 'tel' not in cancelled
    late = alice.post(VOICE, {'text': 'yes', 'pending_id': r['pending_id']}).get_json()
    assert late['status'] == 'cancelled' and 'tel' not in late       # a late yes cannot revive it


def test_correction_replaces_the_pending_call(alice):
    first = alice.post(VOICE, {'text': 'ছেলেকে ফোন করো'}).get_json()
    second = alice.post(VOICE, {'text': 'না মেয়েকে ফোন করো', 'pending_id': first['pending_id']}).get_json()
    assert second['status'] == 'confirm' and second['contact'] == {'name': 'মেয়ে'}
    old = alice.post(VOICE, {'text': 'হ্যাঁ', 'pending_id': first['pending_id']}).get_json()
    assert old['status'] == 'cancelled' and 'tel' not in old


def test_missing_and_unknown_names_ask_and_the_answer_is_a_name(alice):
    r = alice.post(VOICE, {'text': 'ফোন করো'}).get_json()
    assert r['status'] == 'clarify' and r['code'] == 'missing_contact' and r['awaiting'] == 'contact'
    r = alice.post(VOICE, {'text': 'পুলিশ', 'awaiting': 'contact'}).get_json()
    assert r['status'] == 'clarify' and r['code'] == 'contact_not_found'
    r = alice.post(VOICE, {'text': 'ছেলে', 'awaiting': 'contact'}).get_json()
    assert r['status'] == 'confirm' and r['contact'] == {'name': 'ছেলে'}


def test_similar_contacts_ask_which_one(app):
    uid = new_user('carol')
    Contact.create(uid, 'বড় ছেলে', '111', ['ছেলে'])
    Contact.create(uid, 'ছোট ছেলে', '222', ['ছেলে'])
    c = Client(app, uid)
    r = c.post(VOICE, {'text': 'ছেলেকে ফোন করো'}).get_json()
    assert r['status'] == 'clarify' and r['code'] == 'ambiguous_contact'
    assert sorted(r['choices']) == sorted(['বড় ছেলে', 'ছোট ছেলে']) and 'pending_id' not in r
    r = c.post(VOICE, {'text': 'ছোট ছেলে', 'awaiting': 'contact'}).get_json()
    assert r['status'] == 'confirm' and r['contact'] == {'name': 'ছোট ছেলে'}


def test_contact_deleted_before_yes_is_not_dialled(app, alice):
    r = alice.post(VOICE, {'text': 'ছেলেকে ফোন করো'}).get_json()
    cid = next(x['id'] for x in alice.get(API).get_json()['contacts'] if x['name'] == 'ছেলে')
    alice.delete(f'{API}/{cid}')
    done = alice.post(VOICE, {'text': 'হ্যাঁ', 'pending_id': r['pending_id']}).get_json()
    assert done['status'] == 'failed' and done['code'] == 'contact_gone' and 'tel' not in done


def test_another_user_cannot_confirm_my_call(app, alice):
    r = alice.post(VOICE, {'text': 'ছেলেকে ফোন করো'}).get_json()
    bob = Client(app, new_user('bob'))
    stolen = bob.post(VOICE, {'text': 'হ্যাঁ', 'pending_id': r['pending_id']}).get_json()
    assert stolen['status'] == 'failed' and stolen['code'] == 'not_found' and 'tel' not in stolen


def test_a_call_action_cannot_become_a_reminder(alice):
    r = alice.post(VOICE, {'text': 'ছেলেকে ফোন করো'}).get_json()
    assert alice.post('/api/reminders', {'pending_id': r['pending_id']}).status_code == 400
    assert alice.get('/api/reminders').get_json() == {'reminders': []}


def test_request_field_rules(alice):
    assert alice.post(VOICE, {'text': 'x', 'lang': 'fr'}).status_code == 400
    assert alice.post(VOICE, {'text': 'x', 'awaiting': 'yes'}).status_code == 400
    assert alice.post(VOICE, {'text': 'x', 'awaiting': 'contact', 'task': 'y'}).status_code == 400
    assert alice.post(VOICE, {'text': 'x', 'confirmed': True}).status_code == 400


def test_reply_carries_both_field_names(alice):
    r = alice.post(VOICE, {'text': 'আবার বলো', 'lang': 'en'}).get_json()
    assert r['state'] == r['status'] == 'control' and r['action'] == 'repeat'
    assert r['message_bn'] == r['speak_bn'] and r['message_en'] == r['speak_en']
