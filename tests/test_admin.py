"""Admin route tests. Throw-away Flask app + in-memory fake MongoDB (mongomock)."""
import json
from datetime import datetime, timedelta, timezone

import pytest
from bson import ObjectId
from flask import Flask, jsonify

mongomock = pytest.importorskip('mongomock')   # pip install mongomock

from app.extensions import mongo, login_manager, csrf, limiter
from app.models import init_indexes
from app.routes.admin import admin_bp
from app.routes.auth import auth_bp
from app.routes.reminders import reminders_bp
from app.utils.security import init_security

USERS, STATS = '/api/admin/users', '/api/admin/stats'


def make_app():
    app = Flask(__name__)
    app.config.update(SECRET_KEY='test-secret', RATELIMIT_ENABLED=False)
    mongo.db = mongomock.MongoClient().db
    login_manager.init_app(app)
    csrf.init_app(app)
    limiter.init_app(app)
    init_security(app)
    app.register_blueprint(auth_bp)
    app.register_blueprint(reminders_bp)
    app.register_blueprint(admin_bp)

    @app.errorhandler(403)                      # same JSON handler as the real app
    def forbidden(error):
        return jsonify({'error': 'Forbidden'}), 403

    with app.app_context():
        init_indexes()
    return app


@pytest.fixture
def app():
    return make_app()


def new_user(name, role='user'):
    return str(mongo.db.users.insert_one(
        {'name': name, 'email': f'{name}@example.com', 'role': role,
         'password_hash': '$2b$12$SECRETHASHSECRETHASHSECRETHASHSECRETHASHSECRETHASHSECR',
         'created_at': datetime.now(timezone.utc)}).inserted_id)


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


# ---- who may enter ---------------------------------------------------------
@pytest.mark.parametrize('url', [USERS, STATS])
def test_anonymous_gets_401(app, url):
    r = Client(app).get(url)
    assert r.status_code == 401 and r.get_json() == {'error': 'Authentication required'}


@pytest.mark.parametrize('url', [USERS, STATS])
def test_normal_user_gets_403(app, url):
    r = Client(app, new_user('alice')).get(url)
    assert r.status_code == 403 and r.get_json() == {'error': 'Forbidden'}


@pytest.mark.parametrize('url', [USERS, STATS])
def test_admin_gets_200(app, url):
    assert Client(app, new_user('root', 'admin')).get(url).status_code == 200


def test_role_is_checked_against_the_database_on_every_request(app):
    admin_id = new_user('root', 'admin')
    admin = Client(app, admin_id)
    assert admin.get(USERS).status_code == 200
    mongo.db.users.update_one({'_id': ObjectId(admin_id)}, {'$set': {'role': 'user'}})   # demoted
    assert admin.get(USERS).status_code == 403          # the old cookie does not keep admin rights


def test_deleted_user_with_old_cookie_is_logged_out(app):
    admin_id = new_user('root', 'admin')
    admin = Client(app, admin_id)
    mongo.db.users.delete_one({'_id': ObjectId(admin_id)})
    assert admin.get(USERS).status_code == 401


def test_admin_routes_are_read_only(app):
    admin = Client(app, new_user('root', 'admin'))
    for url in (USERS, STATS):
        assert admin.post(url, {}).status_code == 405


# ---- what an admin may see -------------------------------------------------
def test_user_list_never_contains_password_hashes(app):
    new_user('alice')
    admin = Client(app, new_user('root', 'admin'))
    body = admin.get(USERS).get_json()
    assert body['total'] == 2
    for u in body['users']:
        assert set(u) == {'id', 'name', 'email', 'role', 'created_at'}
        assert u['created_at'].endswith('+00:00')
    assert 'password_hash' not in json.dumps(body) and 'SECRETHASH' not in json.dumps(body)


def test_pagination(app):
    for i in range(5):
        new_user(f'u{i}')
    admin = Client(app, new_user('root', 'admin'))
    page = admin.get(f'{USERS}?limit=2&skip=1').get_json()
    assert len(page['users']) == 2 and page['total'] == 6 and page['skip'] == 1


@pytest.mark.parametrize('query', ['limit=0', 'limit=101', 'limit=-1', 'limit=abc',
                                   'skip=-1', 'skip=x', 'limit=1.5', 'limit='])
def test_bad_pagination_is_400(app, query):
    admin = Client(app, new_user('root', 'admin'))
    assert admin.get(f'{USERS}?{query}').status_code == 400


def test_stats_are_counts_only(app):
    alice_id = new_user('alice')
    admin = Client(app, new_user('root', 'admin'))
    for status in ('pending', 'pending', 'completed'):
        mongo.db.reminders.insert_one({'user_id': ObjectId(alice_id), 'title': 'PRIVATE TEXT',
                                       'status': status,
                                       'remind_at': datetime.now(timezone.utc) + timedelta(days=1),
                                       'created_at': datetime.now(timezone.utc)})
    r = admin.get(STATS)
    assert r.get_json() == {'users': 2, 'admins': 1,
                            'reminders': {'pending': 2, 'completed': 1, 'cancelled': 0}}
    assert 'PRIVATE TEXT' not in r.get_data(as_text=True)


# ---- no back door through the other APIs -----------------------------------
def test_admin_cannot_read_another_users_reminders_through_the_reminder_api(app):
    alice_id = new_user('alice')
    mongo.db.reminders.insert_one({'user_id': ObjectId(alice_id), 'title': 'PRIVATE', 'status': 'pending',
                                   'remind_at': datetime.now(timezone.utc) + timedelta(days=1),
                                   'created_at': datetime.now(timezone.utc)})
    admin = Client(app, new_user('root', 'admin'))
    assert admin.get('/api/reminders').get_json() == {'reminders': []}
