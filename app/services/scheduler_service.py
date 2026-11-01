"""Stage 9: reminder scheduler (Eleen, backend).

One background job wakes every TICK_SECONDS and CLAIMS reminders that are due. The database is the
source of truth (no in-memory jobs), so a server restart loses nothing.
Delivery to the browser is by polling GET /api/reminders/due (the server cannot push).
"""
import logging
from datetime import datetime, timedelta, timezone

from apscheduler.schedulers.background import BackgroundScheduler
from pymongo import ReturnDocument
from pymongo.errors import PyMongoError

from app.extensions import mongo
from app.models.reminder import Reminder, _oid

UTC = timezone.utc
JOB_ID = 'deliver_due_reminders'
TICK_SECONDS = 20
STALE_AFTER = timedelta(hours=24)    # older than this: never announce (marked "missed")
LATE_AFTER = timedelta(minutes=2)    # announced this long after its time: flagged "late"
MAX_PER_TICK = 100                   # cap so one tick can never run away

log = logging.getLogger(__name__)


def _naive_utc(value):
    """MongoDB stores UTC. Normalise so comparisons never mix naive and aware datetimes."""
    return value if value.tzinfo is None else value.astimezone(UTC).replace(tzinfo=None)


def _aware_utc(value):
    return value.replace(tzinfo=UTC) if value.tzinfo is None else value.astimezone(UTC)


def claim_due(now=None, limit=MAX_PER_TICK):
    """Mark due reminders as notified. Returns {'claimed': n, 'missed': n}.

    Each claim is ONE atomic find_one_and_update that only matches a reminder with no
    notified_at yet, so two ticks or two workers can never claim the same reminder.
    Only status == 'pending' reminders match: cancelled/completed ones never fire.
    """
    now = _aware_utc(now) if now else datetime.now(UTC)
    now_db = _naive_utc(now)
    col = mongo.db[Reminder.collection]

    # Far too old: do not announce a 2-day-old medicine reminder. Fixed filter, no user input.
    missed = col.update_many(
        {'status': 'pending', 'notified_at': None,
         'remind_at': {'$lt': _naive_utc(now - STALE_AFTER)}},
        {'$set': {'notified_at': now_db, 'missed': True}})

    claimed = 0
    for _ in range(limit):
        doc = col.find_one_and_update(
            {'status': 'pending', 'notified_at': None, 'remind_at': {'$lte': now_db}},
            {'$set': {'notified_at': now_db}},
            sort=[('remind_at', 1)],
            return_document=ReturnDocument.AFTER)
        if doc is None:
            break
        claimed += 1
    return {'claimed': claimed, 'missed': missed.modified_count}


def due_to_json(doc):
    """Same safe fields as a normal reminder (no user_id) plus a 'late' flag."""
    data = Reminder.to_json(doc)
    data['late'] = (_aware_utc(doc['notified_at']) - _aware_utc(doc['remind_at'])) > LATE_AFTER
    return data


def due_for_user(user_id, limit=20):
    """This user's fired, not yet acknowledged reminders. user_id comes from the session."""
    owner = _oid(user_id)
    if owner is None:
        return []
    cursor = (mongo.db[Reminder.collection]
              .find({'user_id': owner, 'status': 'pending',
                     'notified_at': {'$ne': None}, 'acknowledged_at': None,
                     'missed': {'$ne': True}})
              .sort('remind_at', 1).limit(limit))
    return [due_to_json(d) for d in cursor]


def acknowledge(reminder_id, user_id):
    """The user heard it. Returns 'ok', 'not_found' (missing OR someone else's) or 'conflict'."""
    rid, owner = _oid(reminder_id), _oid(user_id)
    if rid is None or owner is None:
        return 'not_found'
    col = mongo.db[Reminder.collection]
    now = datetime.now(UTC)
    result = col.update_one(
        {'_id': rid, 'user_id': owner, 'status': 'pending',      # owner filter = no IDOR
         'notified_at': {'$ne': None}, 'acknowledged_at': None, 'missed': {'$ne': True}},
        {'$set': {'acknowledged_at': _naive_utc(now), 'updated_at': now}})
    if result.modified_count > 0:
        return 'ok'
    exists = col.find_one({'_id': rid, 'user_id': owner}, {'_id': 1})
    return 'conflict' if exists else 'not_found'


def run_tick(app):
    """One scheduler tick. A database problem is logged (no personal data) and retried next tick."""
    with app.app_context():
        try:
            result = claim_due()
            if result['claimed'] or result['missed']:
                log.info('scheduler: claimed=%s missed=%s', result['claimed'], result['missed'])
        except PyMongoError as exc:
            log.warning('scheduler tick failed: %s', type(exc).__name__)


def build_scheduler(app):
    """Create the scheduler (not started). One job; never overlaps itself; runs once at startup."""
    scheduler = BackgroundScheduler(timezone=UTC)
    scheduler.add_job(run_tick, 'interval', seconds=TICK_SECONDS, args=[app], id=JOB_ID,
                      max_instances=1, coalesce=True, replace_existing=True,
                      next_run_time=datetime.now(UTC))     # catch up right after a restart
    return scheduler


def start_scheduler(app):
    scheduler = build_scheduler(app)
    scheduler.start()
    return scheduler
