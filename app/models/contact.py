"""Contacts (Eleen, backend). A contact always belongs to exactly one user."""
import re
import unicodedata
from datetime import datetime, timezone

from app.extensions import mongo
from app.models.reminder import _oid

UTC = timezone.utc
MAX_NAME_LENGTH = 60
MAX_ALIASES = 5
MAX_CONTACTS_PER_USER = 50
_PHONE_RE = re.compile(r'^\+?[0-9]{3,15}$')


def name_key(value):
    """Comparison form of a name: NFC, lowercase, single spaces."""
    return ' '.join(unicodedata.normalize('NFC', value).lower().split())


def clean_phone(value):
    """Return the phone number with spaces/dashes removed, or None if it is not a phone number.
    Only digits and one leading '+' survive, so the value is always safe inside a tel: link."""
    if not isinstance(value, str):
        return None
    phone = re.sub(r'[\s\-()]', '', value)
    return phone if _PHONE_RE.match(phone) else None


class Contact:
    collection = 'contacts'

    @staticmethod
    def create(user_id, name, phone, aliases=()):
        """Raises DuplicateKeyError if this user already has a contact with the same name."""
        owner = _oid(user_id)
        if owner is None:
            raise ValueError('invalid user_id')
        doc = {'user_id': owner, 'name': name, 'name_key': name_key(name), 'phone': phone,
               'aliases': list(aliases), 'created_at': datetime.now(UTC)}
        return str(mongo.db[Contact.collection].insert_one(doc).inserted_id)

    @staticmethod
    def find_by_user(user_id):
        owner = _oid(user_id)
        if owner is None:
            return []
        return list(mongo.db[Contact.collection].find({'user_id': owner}).sort('name_key', 1))

    @staticmethod
    def find_by_id(contact_id, user_id):
        """Only if it belongs to this user (prevents IDOR)."""
        cid, owner = _oid(contact_id), _oid(user_id)
        if cid is None or owner is None:
            return None
        return mongo.db[Contact.collection].find_one({'_id': cid, 'user_id': owner})

    @staticmethod
    def count_for_user(user_id):
        owner = _oid(user_id)
        return 0 if owner is None else mongo.db[Contact.collection].count_documents({'user_id': owner})

    @staticmethod
    def delete(contact_id, user_id):
        cid, owner = _oid(contact_id), _oid(user_id)
        if cid is None or owner is None:
            return False
        return mongo.db[Contact.collection].delete_one({'_id': cid, 'user_id': owner}).deleted_count > 0

    @staticmethod
    def to_json(doc):
        """Never includes user_id."""
        return {'id': str(doc['_id']), 'name': doc['name'], 'phone': doc['phone'],
                'aliases': doc.get('aliases', [])}
