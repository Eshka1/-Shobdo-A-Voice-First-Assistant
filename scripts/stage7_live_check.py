"""Stage 7 live check against your REAL MongoDB.
Creates two throw-away users, runs the reminder flow through the real app, then deletes
everything it created. Run from the project folder:  python scripts/stage7_live_check.py
"""
import os
import secrets
import sys
from datetime import datetime, timedelta

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from bson import ObjectId
from app import create_app
from app.extensions import mongo
from app.models.user import User
from app.services.bangla_datetime import TZ
from app.services.confirmation import ConfirmationService, MongoPendingStore

app = create_app()
API = '/api/reminders'


def client_for(user_id):
    c = app.test_client()
    with c.session_transaction() as s:
        s['_user_id'] = user_id
        s['_fresh'] = True
    token = c.get('/api/auth/csrf-token').get_json()['csrf_token']
    return c, {'X-CSRFToken': token}


def show(label, response):
    body = response.get_json() or {}
    print(f'{label:<44}-> {response.status_code} {body.get("code", "")}')
    return response


# NOTE: no outer app context here. Flask would reuse one context for every request and
# cache the first logged-in user, so Bob would look like Alice.
tag = secrets.token_hex(4)
users = []
try:
    a = User.create('SelfTest A', f'selftest-a-{tag}@example.invalid', 'SelfTestPass123')
    b = User.create('SelfTest B', f'selftest-b-{tag}@example.invalid', 'SelfTestPass123')
    users = [a, b]
    svc = ConfirmationService(MongoPendingStore(mongo.db.pending_actions))
    when = (datetime.now(TZ) + timedelta(hours=24)).replace(microsecond=0).isoformat()

    def pend(user, task='ওষুধ খেতে'):
        return svc.propose(user, 'reminder', {'task': task, 'remind_at': when}, 'a', 'b')['pending_id']

    indexes = mongo.db.reminders.index_information().values()
    print('0 partial unique index exists            ->',
          any(i.get('unique') and 'partialFilterExpression' in i for i in indexes))

    ca, ha = client_for(a)
    cb, hb = client_for(b)
    p1 = pend(a)
    r = show('1 create (expect 201)', ca.post(API, json={'pending_id': p1}, headers=ha))
    rid = r.get_json()['reminder']['id']
    show('2 same confirmation again (409 duplicate)', ca.post(API, json={'pending_id': p1}, headers=ha))
    show('3 same reminder, new confirmation (409)', ca.post(API, json={'pending_id': pend(a)}, headers=ha))
    print('4 Alice sees 1 reminder                  ->', len(ca.get(API).get_json()['reminders']))
    show('5 Bob completes Alice\'s (expect 404)', cb.post(f'{API}/{rid}/complete', headers=hb))
    print('6 Bob sees 0 reminders                   ->', len(cb.get(API).get_json()['reminders']))
    show('7 browser sends confirmed:true (400)', ca.post(API, json={'confirmed': True, 'title': 'x'}, headers=ha))
    show('8 Alice completes (expect 200)', ca.post(f'{API}/{rid}/complete', headers=ha))
    show('9 Alice completes again (expect 409)', ca.post(f'{API}/{rid}/complete', headers=ha))
    show('10 recreate after completion (201)', ca.post(API, json={'pending_id': pend(a)}, headers=ha))
    no_token = app.test_client()
    with no_token.session_transaction() as s:
        s['_user_id'] = a
    show('11 no CSRF token (expect 400)', no_token.post(API, json={'pending_id': pend(a, 'অন্য')}))
finally:
    for uid in users:
        mongo.db.reminders.delete_many({'user_id': ObjectId(uid)})
        mongo.db.pending_actions.delete_many({'user_id': uid})
        mongo.db.users.delete_one({'_id': ObjectId(uid)})
    print('cleanup: removed the throw-away users, reminders and pending actions')
