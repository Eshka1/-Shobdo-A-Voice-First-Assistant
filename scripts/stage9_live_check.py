"""Stage 9 live check against your REAL MongoDB. Throw-away users and reminders are removed afterwards.
NOTE: claim_due() is global, so it also claims any other genuinely due reminder in your dev database
(that is exactly what the real scheduler does). Prints booleans and statuses only.
Run from the project folder:  python scripts/stage9_live_check.py
"""
import os
import secrets
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from datetime import datetime, timedelta, timezone
from bson import ObjectId
from app import create_app
from app.extensions import mongo
from app.models.reminder import Reminder
from app.models.user import User
from app.services.scheduler_service import claim_due

app = create_app()
DUE = '/api/reminders/due'
UTC = timezone.utc


def client_for(user_id):
    c = app.test_client()
    with c.session_transaction() as s:
        s['_user_id'] = user_id
        s['_fresh'] = True
    return c, c.get('/api/auth/csrf-token').get_json()['csrf_token']


def field(rid, name):
    return mongo.db.reminders.find_one({'_id': ObjectId(rid)}).get(name)


tag = secrets.token_hex(4)
user_ids = []
try:
    alice = User.create('SelfTest A', f'selftest-a-{tag}@example.invalid', 'SelfTestPass123')
    bob = User.create('SelfTest B', f'selftest-b-{tag}@example.invalid', 'SelfTestPass123')
    user_ids = [alice, bob]
    now = datetime.now(UTC)
    due = Reminder.create(alice, 'selftest due', now - timedelta(minutes=1))
    future = Reminder.create(alice, 'selftest future', now + timedelta(hours=1))
    stale = Reminder.create(alice, 'selftest stale', now - timedelta(days=2))
    cancelled = Reminder.create(alice, 'selftest cancelled', now - timedelta(minutes=1))
    Reminder.transition(cancelled, alice, 'cancelled')

    result = claim_due()
    print('1 claim ran: claimed>=1 and missed>=1   ->', result['claimed'] >= 1, result['missed'] >= 1)
    print('2 due reminder claimed                   ->', field(due, 'notified_at') is not None)
    print('3 future reminder NOT claimed            ->', field(future, 'notified_at') is None)
    print('4 cancelled reminder NOT claimed         ->', field(cancelled, 'notified_at') is None)
    print('5 2-day-old reminder marked missed       ->', field(stale, 'missed') is True)
    first = field(due, 'notified_at')
    claim_due()
    print('6 second run does not re-claim           ->', field(due, 'notified_at') == first)

    a, ta = client_for(alice)
    b, tb = client_for(bob)
    r = a.get(DUE)
    items = r.get_json()['reminders']
    print('7 Alice /due (expect 200, 1 item)        ->', r.status_code, len(items))
    print('8 Bob /due sees nothing                  ->', len(b.get(DUE).get_json()['reminders']) == 0)
    url = f'/api/reminders/{due}/ack'
    print('9 Bob acks Alice (expect 404)            ->', b.post(url, headers={'X-CSRFToken': tb}).status_code)
    print('10 Alice acks (expect 200)               ->', a.post(url, headers={'X-CSRFToken': ta}).status_code)
    print('11 Alice acks again (expect 409)         ->', a.post(url, headers={'X-CSRFToken': ta}).status_code)
    print('12 Alice /due now empty                  ->', len(a.get(DUE).get_json()['reminders']) == 0)
finally:
    oids = [ObjectId(u) for u in user_ids]
    mongo.db.reminders.delete_many({'user_id': {'$in': oids}})
    for oid in oids:
        mongo.db.users.delete_one({'_id': oid})
    print('cleanup: removed the throw-away users and reminders')
