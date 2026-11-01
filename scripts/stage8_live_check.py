"""Stage 8 live check against your REAL MongoDB.
Creates one throw-away admin and one throw-away normal user, checks the admin routes,
then deletes both. Run from the project folder:  python scripts/stage8_live_check.py
It prints counts and statuses only (no emails, no hashes).
"""
import json
import os
import secrets
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from bson import ObjectId
from app import create_app
from app.extensions import mongo
from app.models.user import User

app = create_app()
USERS, STATS = '/api/admin/users', '/api/admin/stats'


def client_for(user_id=None):
    # NOTE: no outer app context here; Flask would reuse it and cache the first user.
    c = app.test_client()
    if user_id:
        with c.session_transaction() as s:
            s['_user_id'] = user_id
            s['_fresh'] = True
    return c


def show(label, response):
    print(f'{label:<46}-> {response.status_code}')
    return response


tag = secrets.token_hex(4)
created = []
try:
    admin_id = User.create('SelfTest Admin', f'selftest-admin-{tag}@example.invalid', 'SelfTestPass123', role='admin')
    user_id = User.create('SelfTest User', f'selftest-user-{tag}@example.invalid', 'SelfTestPass123')
    created = [admin_id, user_id]

    show('1 anonymous  GET users (expect 401)', client_for().get(USERS))
    show('2 anonymous  GET stats (expect 401)', client_for().get(STATS))
    show('3 normal user GET users (expect 403)', client_for(user_id).get(USERS))
    show('4 normal user GET stats (expect 403)', client_for(user_id).get(STATS))

    admin = client_for(admin_id)
    r = show('5 admin GET users (expect 200)', admin.get(USERS))
    body = r.get_json()
    print('6 hash leaked anywhere in the response   ->', 'password_hash' in json.dumps(body) or '$2b$' in json.dumps(body))
    print('7 admin sees at least our 2 users        ->', body['total'] >= 2)
    show('8 admin GET users?limit=1000 (expect 400)', admin.get(USERS + '?limit=1000'))
    r = show('9 admin GET stats (expect 200)', admin.get(STATS))
    print('10 stats keys                            ->', sorted(r.get_json()))

    mongo.db.users.update_one({'_id': ObjectId(admin_id)}, {'$set': {'role': 'user'}})
    show('11 admin demoted in DB, same cookie (403)', admin.get(USERS))
finally:
    for uid in created:
        mongo.db.users.delete_one({'_id': ObjectId(uid)})
    print('cleanup: removed the throw-away users')
