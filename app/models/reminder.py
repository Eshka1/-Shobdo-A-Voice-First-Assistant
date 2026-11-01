from datetime import datetime, timezone
from bson import ObjectId
from bson.errors import InvalidId
from app.extensions import mongo
from app.services.bangla_datetime import TZ

UTC = timezone.utc
STATUSES = ('pending', 'completed', 'cancelled')


def _oid(value):
    """Return an ObjectId, or None if value is not a valid id (never raises)."""
    if isinstance(value, ObjectId):
        return value
    if not isinstance(value, str):
        return None
    try:
        return ObjectId(value)
    except (InvalidId, TypeError):
        return None


def _iso(value):
    """MongoDB returns naive UTC datetimes. Send the browser an unambiguous Dhaka time."""
    if not isinstance(value, datetime):
        return value
    if value.tzinfo is None:
        value = value.replace(tzinfo=UTC)
    return value.astimezone(TZ).isoformat()


class Reminder:
    collection = 'reminders'

    @staticmethod
    def create(user_id, title, remind_at, description='', idempotency_key=None):
        """Create a new reminder for a specific user. Raises DuplicateKeyError for a duplicate."""
        owner = _oid(user_id)
        if owner is None:
            raise ValueError('invalid user_id')
        if not isinstance(remind_at, datetime) or remind_at.tzinfo is None:
            raise ValueError('remind_at must be a timezone-aware datetime')
        now = datetime.now(UTC)
        reminder_data = {
            'user_id': owner,          # Ownership link
            'title': title,
            'description': description,
            'remind_at': remind_at,
            'status': 'pending',       # pending, completed, cancelled
            'created_at': now,
            'updated_at': now,
        }
        if idempotency_key:
            reminder_data['idempotency_key'] = idempotency_key
        result = mongo.db[Reminder.collection].insert_one(reminder_data)
        return str(result.inserted_id)

    @staticmethod
    def find_by_id(reminder_id, user_id):
        """Find reminder by ID, but ONLY if it belongs to this user (prevents IDOR)"""
        rid, owner = _oid(reminder_id), _oid(user_id)
        if rid is None or owner is None:
            return None
        return mongo.db[Reminder.collection].find_one({'_id': rid, 'user_id': owner})

    @staticmethod
    def find_by_user(user_id, status=None):
        """Get all reminders for a user, optionally filtered by status"""
        owner = _oid(user_id)
        if owner is None or (status is not None and status not in STATUSES):
            return []
        query = {'user_id': owner}
        if status:
            query['status'] = status
        return list(mongo.db[Reminder.collection].find(query).sort('remind_at', 1))

    @staticmethod
    def transition(reminder_id, user_id, new_status):
        """Move a PENDING reminder to completed/cancelled, only if owner.

        Returns 'ok', 'not_found' (missing OR someone else's) or 'conflict' (not pending).
        """
        if new_status not in ('completed', 'cancelled'):
            raise ValueError('new_status must be completed or cancelled')
        rid, owner = _oid(reminder_id), _oid(user_id)
        if rid is None or owner is None:
            return 'not_found'
        result = mongo.db[Reminder.collection].update_one(
            {'_id': rid, 'user_id': owner, 'status': 'pending'},   # atomic: only from pending
            {'$set': {'status': new_status, 'updated_at': datetime.now(UTC)}},
        )
        if result.modified_count > 0:
            return 'ok'
        exists = mongo.db[Reminder.collection].find_one({'_id': rid, 'user_id': owner}, {'_id': 1})
        return 'conflict' if exists else 'not_found'

    @staticmethod
    def update_status(reminder_id, user_id, new_status):
        """Kept for compatibility: True only if a pending reminder was changed."""
        return Reminder.transition(reminder_id, user_id, new_status) == 'ok'

    @staticmethod
    def delete(reminder_id, user_id):
        """Delete reminder, only if owner"""
        rid, owner = _oid(reminder_id), _oid(user_id)
        if rid is None or owner is None:
            return False
        result = mongo.db[Reminder.collection].delete_one({'_id': rid, 'user_id': owner})
        return result.deleted_count > 0

    @staticmethod
    def to_json(reminder):
        """Convert reminder document to JSON (never includes user_id or the idempotency key)."""
        return {
            'id': str(reminder['_id']),
            'title': reminder['title'],
            'description': reminder.get('description', ''),
            'remind_at': _iso(reminder['remind_at']),
            'status': reminder['status'],
            'created_at': _iso(reminder['created_at']),
        }
