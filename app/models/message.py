"""Demo inbox (Eleen, backend).

Shobdo does NOT read the phone's real SMS. These are messages stored for one user inside
Shobdo (seeded for the demo). There is deliberately no API that lets a browser create one.
"""
from datetime import datetime, timezone

from app.extensions import mongo
from app.models.reminder import _iso, _oid

UTC = timezone.utc
MAX_SENDER_LENGTH = 60
MAX_TEXT_LENGTH = 500


class Message:
    collection = 'messages'

    @staticmethod
    def create(user_id, sender, text, received_at=None):
        owner = _oid(user_id)
        if owner is None:
            raise ValueError('invalid user_id')
        doc = {'user_id': owner, 'sender': sender[:MAX_SENDER_LENGTH], 'text': text[:MAX_TEXT_LENGTH],
               'received_at': received_at or datetime.now(UTC), 'read_at': None}
        return str(mongo.db[Message.collection].insert_one(doc).inserted_id)

    @staticmethod
    def find_by_user(user_id, limit=20):
        owner = _oid(user_id)
        if owner is None:
            return []
        return list(mongo.db[Message.collection].find({'user_id': owner})
                    .sort('received_at', -1).limit(limit))

    @staticmethod
    def find_by_id(message_id, user_id):
        """Only if it belongs to this user (prevents IDOR)."""
        mid, owner = _oid(message_id), _oid(user_id)
        if mid is None or owner is None:
            return None
        return mongo.db[Message.collection].find_one({'_id': mid, 'user_id': owner})

    @staticmethod
    def oldest_unread(user_id):
        owner = _oid(user_id)
        if owner is None:
            return None
        return mongo.db[Message.collection].find_one(
            {'user_id': owner, 'read_at': None}, sort=[('received_at', 1)])

    @staticmethod
    def count_for_user(user_id):
        owner = _oid(user_id)
        return 0 if owner is None else mongo.db[Message.collection].count_documents({'user_id': owner})

    @staticmethod
    def mark_read(message_id, user_id):
        mid, owner = _oid(message_id), _oid(user_id)
        if mid is None or owner is None:
            return False
        result = mongo.db[Message.collection].update_one(
            {'_id': mid, 'user_id': owner, 'read_at': None},
            {'$set': {'read_at': datetime.now(UTC)}})
        return result.modified_count > 0

    @staticmethod
    def to_json(doc):
        """Never includes user_id."""
        return {'id': str(doc['_id']), 'sender': doc['sender'], 'text': doc['text'],
                'received_at': _iso(doc['received_at']), 'read': doc.get('read_at') is not None}
