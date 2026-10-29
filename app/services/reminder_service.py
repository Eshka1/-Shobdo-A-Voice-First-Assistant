"""Executes a CONFIRMED reminder action. Used by POST /api/reminders now and by the
voice-command route later, so there is exactly one place that creates reminders."""
from datetime import datetime, timezone
from pymongo.errors import DuplicateKeyError, PyMongoError
from app.models.reminder import Reminder

MAX_TITLE_LENGTH = 200


def _read_params(params):
    """Return (title, remind_at) or None. Re-validated even though the server stored it."""
    if not isinstance(params, dict):
        return None
    title, when = params.get('task'), params.get('remind_at')
    if not isinstance(title, str) or not 1 <= len(title.strip()) <= MAX_TITLE_LENGTH:
        return None
    try:
        remind_at = datetime.fromisoformat(when)
    except (TypeError, ValueError):
        return None
    if remind_at.tzinfo is None:
        return None
    return title.strip(), remind_at


def create_from_action(user_id, action, now=None):
    """Return {'result': 'created', 'reminder': {...}} or {'result': <error code>}."""
    if action.get('type') != 'reminder':
        return {'result': 'unsupported'}
    parsed = _read_params(action.get('params'))
    if parsed is None:
        return {'result': 'invalid'}
    title, remind_at = parsed
    if remind_at <= (now or datetime.now(timezone.utc)):
        return {'result': 'past'}
    try:
        reminder_id = Reminder.create(user_id, title, remind_at,
                                      idempotency_key=action.get('idempotency_key'))
        doc = Reminder.find_by_id(reminder_id, user_id)
        return {'result': 'created', 'reminder': Reminder.to_json(doc)}
    except DuplicateKeyError:
        return {'result': 'duplicate_reminder'}
    except PyMongoError:
        return {'result': 'db_error'}      # never claim "saved" when it was not


def execute_confirmed(confirmations, user_id, action, now=None):
    """Run a released action and report back so the pending action ends completed or error."""
    outcome = create_from_action(user_id, action, now=now)
    if outcome['result'] == 'created':
        confirmations.complete(user_id, action['pending_id'])
    else:
        confirmations.fail(user_id, action['pending_id'], outcome['result'])
    return outcome
