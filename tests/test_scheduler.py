"""Stage 9 scheduler tests. Throw-away Flask app + in-memory fake MongoDB (mongomock)."""
from datetime import datetime, timedelta, timezone

import pytest
from bson import ObjectId
from flask import Flask, jsonify

mongomock = pytest.importorskip('mongomock')   # pip install mongomock
from pymongo.errors import PyMongoError

from app.extensions import mongo, login_manager, csrf, limiter
from app.models import init_indexes
from app.routes.auth import auth_bp
from app.routes.reminders import reminders_bp
from app.services import scheduler_service as sched
from app.utils.security import init_security

UTC = timezone.utc
DUE = '/api/reminders/due'


def ack_url(rid):
    return f'/api/reminders/{rid}/ack'


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

    @app.errorhandler(403)
    def forbidden(error):
        return jsonify({'error': 'Forbidden'}), 403

    with app.app_context():
        init_indexes()
    return app


@pytest.fixture
def app():
    return make_app()


def new_user(name):
    return str(mongo.db.users.insert_one(
        {'name': name, 'email': f'{name}@example.com', 'role': 'user',
         'password_hash': 'x', 'created_at': datetime.now(UTC)}).inserted_id)


def add_reminder(user_id, title, minutes_from_now, status='pending'):
    when = datetime.now(UTC) + timedelta(minutes=minutes_from_now)
    return str(mongo.db.reminders.insert_one(
        {'user_id': ObjectId(user_id), 'title': title, 'description': '', 'status': status,
         'remind_at': when, 'created_at': datetime.now(UTC),
         'updated_at': datetime.now(UTC)}).inserted_id)


def doc(rid):
    return mongo.db.reminders.find_one({'_id': ObjectId(rid)})


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


# ---- claiming --------------------------------------------------------------
def test_due_reminder_is_claimed_and_future_one_is_not(app):
    alice = new_user('alice')
    due, future = add_reminder(alice, 'due', -1), add_reminder(alice, 'future', 60)
    assert sched.claim_due() == {'claimed': 1, 'missed': 0}
    assert doc(due).get('notified_at') is not None
    assert doc(future).get('notified_at') is None


def test_a_reminder_is_claimed_only_once(app):
    add_reminder(new_user('alice'), 'due', -1)
    assert sched.claim_due()['claimed'] == 1
    assert sched.claim_due()['claimed'] == 0


@pytest.mark.parametrize('status', ['cancelled', 'completed'])
def test_cancelled_and_completed_reminders_never_fire(app, status):
    rid = add_reminder(new_user('alice'), 'x', -1, status=status)
    assert sched.claim_due() == {'claimed': 0, 'missed': 0}
    assert doc(rid).get('notified_at') is None


def test_very_old_reminder_is_marked_missed_and_never_announced(app):
    alice = new_user('alice')
    rid = add_reminder(alice, 'old', -25 * 60)
    assert sched.claim_due() == {'claimed': 0, 'missed': 1}
    assert doc(rid)['missed'] is True
    assert Client(app, alice).get(DUE).get_json() == {'reminders': []}


def test_late_flag(app):
    alice = new_user('alice')
    add_reminder(alice, 'late one', -10)
    add_reminder(alice, 'on time', -0.1)
    sched.claim_due()
    items = {r['title']: r for r in Client(app, alice).get(DUE).get_json()['reminders']}
    assert items['late one']['late'] is True
    assert items['on time']['late'] is False


# ---- the /due and /ack routes ----------------------------------------------
def test_due_requires_login(app):
    r = Client(app).get(DUE)
    assert r.status_code == 401 and r.get_json() == {'error': 'Authentication required'}


def test_a_user_sees_only_their_own_due_reminders(app):
    alice, bob = new_user('alice'), new_user('bob')
    add_reminder(alice, 'alice private', -1)
    sched.claim_due()
    assert len(Client(app, alice).get(DUE).get_json()['reminders']) == 1
    r = Client(app, bob).get(DUE)
    assert r.get_json() == {'reminders': []} and 'alice private' not in r.get_data(as_text=True)


def test_due_response_has_safe_fields_only_and_is_json(app):
    alice = new_user('alice')
    add_reminder(alice, 'x', -1)
    sched.claim_due()
    r = Client(app, alice).get(DUE)
    assert r.content_type == 'application/json'
    item = r.get_json()['reminders'][0]
    assert set(item) == {'id', 'title', 'description', 'remind_at', 'status', 'created_at', 'late'}
    assert alice not in r.get_data(as_text=True)          # no user_id leaks


def test_ack_flow(app):
    alice = new_user('alice')
    rid = add_reminder(alice, 'x', -1)
    sched.claim_due()
    c = Client(app, alice)
    assert c.post(ack_url(rid)).status_code == 200
    assert c.get(DUE).get_json() == {'reminders': []}
    assert c.post(ack_url(rid)).status_code == 409          # already acknowledged


def test_ack_someone_elses_reminder_is_404(app):
    alice, bob = new_user('alice'), new_user('bob')
    rid = add_reminder(alice, 'x', -1)
    sched.claim_due()
    assert Client(app, bob).post(ack_url(rid)).status_code == 404
    assert doc(rid).get('acknowledged_at') is None


def test_ack_before_it_has_fired_is_409(app):
    alice = new_user('alice')
    rid = add_reminder(alice, 'future', 60)
    assert Client(app, alice).post(ack_url(rid)).status_code == 409


def test_ack_with_a_bad_id_is_404(app):
    assert Client(app, new_user('alice')).post(ack_url('not-an-id')).status_code == 404


def test_script_in_a_title_is_returned_as_plain_json_data(app):
    alice = new_user('alice')
    add_reminder(alice, '<script>alert(1)</script>', -1)
    sched.claim_due()
    r = Client(app, alice).get(DUE)
    assert r.content_type == 'application/json'      # never rendered as HTML by the server
    assert r.get_json()['reminders'][0]['title'] == '<script>alert(1)</script>'


# ---- the scheduler itself --------------------------------------------------
def test_scheduler_has_one_non_overlapping_job_and_is_not_started(app):
    scheduler = sched.build_scheduler(app)
    jobs = scheduler.get_jobs()
    assert [j.id for j in jobs] == [sched.JOB_ID]
    assert jobs[0].max_instances == 1 and jobs[0].coalesce is True
    assert not scheduler.running


def test_run_tick_claims_due_reminders(app):
    rid = add_reminder(new_user('alice'), 'due', -1)
    sched.run_tick(app)
    assert doc(rid).get('notified_at') is not None


def test_a_database_error_does_not_kill_the_tick(app, monkeypatch):
    def boom(*a, **k):
        raise PyMongoError('boom')
    monkeypatch.setattr(sched, 'claim_due', boom)
    sched.run_tick(app)          # must not raise
