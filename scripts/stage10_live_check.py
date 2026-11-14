"""Stage 10 live check against your REAL MongoDB. Throw-away users, reminders and pending actions
are removed afterwards. Prints statuses and booleans only. Run: python scripts/stage10_live_check.py
"""
import os
import secrets
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from bson import ObjectId
from app import create_app
from app.extensions import mongo
from app.models.user import User

app = create_app()
VOICE = '/api/voice-command'
CMD = 'কাল সকাল ৯টায় ওষুধ খেতে মনে করিয়ে দিও'


def client_for(user_id=None):
    c = app.test_client()
    if user_id:
        with c.session_transaction() as s:
            s['_user_id'] = user_id
            s['_fresh'] = True
    return c, c.get('/api/auth/csrf-token').get_json()['csrf_token']


def say(client, body):
    c, token = client
    r = c.post(VOICE, json=body, headers={'X-CSRFToken': token})
    return r.status_code, (r.get_json(silent=True) or {}), r


def count(user_id):
    return mongo.db.reminders.count_documents({'user_id': ObjectId(user_id)})


tag = secrets.token_hex(4)
user_ids = []
try:
    alice = User.create('SelfTest A', f'selftest-a-{tag}@example.invalid', 'SelfTestPass123')
    bob = User.create('SelfTest B', f'selftest-b-{tag}@example.invalid', 'SelfTestPass123')
    user_ids = [alice, bob]
    a, b = client_for(alice), client_for(bob)

    print('1 anonymous (expect 401)                 ->', say(client_for(), {'text': CMD})[0])
    s, p, _ = say(a, {'text': CMD})
    print('2 command -> confirm (expect 200 confirm)->', s, p.get('status'))
    print('3 nothing saved before "yes"             ->', count(alice) == 0)
    s, d, _ = say(a, {'text': 'হ্যাঁ', 'pending_id': p['pending_id']})
    print('4 yes saves it (expect 200 done, 1)      ->', s, d.get('status'), count(alice))
    s, d, _ = say(a, {'text': 'হ্যাঁ', 'pending_id': p['pending_id']})
    print('5 yes again (expect failed duplicate, 1) ->', s, d.get('status'), d.get('code'), count(alice))

    s, p2, _ = say(a, {'text': 'কাল রাত ৮টায় ঘুমের ওষুধ মনে করিয়ে দিও'})
    s, d, _ = say(b, {'text': 'হ্যাঁ', 'pending_id': p2['pending_id']})
    print('6 Bob confirms Alice (expect not_found)  ->', d.get('status'), d.get('code'), count(bob) == 0)
    s, p3, _ = say(a, {'text': 'না, কাল রাত ৯টায় ঘুমের ওষুধ মনে করিয়ে দিও', 'pending_id': p2['pending_id']})
    print('7 correction (expect confirm, new id)    ->', p3.get('status'), p3.get('pending_id') != p2['pending_id'])
    s, d, _ = say(a, {'text': 'হ্যাঁ', 'pending_id': p2['pending_id']})
    print('8 old read-back is dead (cancelled)      ->', d.get('status'))
    s, d, _ = say(a, {'text': 'না', 'pending_id': p3['pending_id']})
    print('9 "no" cancels, still only 1 saved       ->', d.get('status'), count(alice))
    s, d, _ = say(a, {'text': 'কাল ওষুধ খেতে মনে করিয়ে দিও'})
    print('10 missing time (expect clarify)         ->', d.get('status'), d.get('code'), d.get('task'))
    s, d, _ = say(a, {'text': 'আবার বলো'})
    print('11 repeat (expect control)               ->', d.get('status'), d.get('action'))
    s, d, _ = say(a, {'text': 'হ্যাঁ', 'pending_id': 'x', 'confirmed': True})
    print('12 confirmed:true (expect 400)           ->', s)
    s, d, r = say(a, {'text': 'ক' * 20000})
    print('13 20 KB body (expect 413 + JSON)        ->', s, r.content_type)
finally:
    oids = [ObjectId(u) for u in user_ids]
    mongo.db.reminders.delete_many({'user_id': {'$in': oids}})
    mongo.db.pending_actions.delete_many({'user_id': {'$in': user_ids}})
    for oid in oids:
        mongo.db.users.delete_one({'_id': oid})
    print('cleanup: removed the throw-away users, reminders and pending actions')
