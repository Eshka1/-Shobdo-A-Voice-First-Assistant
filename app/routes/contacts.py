import re
import unicodedata

from flask import Blueprint, request, jsonify
from flask_login import login_required, current_user
from pymongo.errors import DuplicateKeyError

from app.extensions import limiter
from app.models.contact import (Contact, clean_phone, MAX_ALIASES, MAX_CONTACTS_PER_USER,
                                MAX_NAME_LENGTH)
from app.services.contact_service import match_contact
from app.utils.validators import _check_fields

contacts_bp = Blueprint('contacts', __name__, url_prefix='/api/contacts')

# Bangla letters, English letters, digits, space, dot and hyphen. No <, >, quotes or slashes.
_NAME_RE = re.compile(r'^[ঀ-৿A-Za-z0-9 .\-]+$')


def _error(message, code, status):
    return jsonify({'error': message, 'code': code}), status


def _clean_name(value):
    """Return a tidy name, or None if it is not an acceptable name."""
    if not isinstance(value, str):
        return None
    name = ' '.join(unicodedata.normalize('NFC', value).split())
    if not 1 <= len(name) <= MAX_NAME_LENGTH or not _NAME_RE.match(name):
        return None
    return name


@contacts_bp.route('', methods=['GET'])
@login_required
def list_contacts():
    items = Contact.find_by_user(current_user.id)        # owner comes from the session
    return jsonify({'contacts': [Contact.to_json(c) for c in items]}), 200


@contacts_bp.route('', methods=['POST'])
@limiter.limit('20 per minute')
@login_required
def create_contact():
    data = request.get_json(silent=True)
    problem = _check_fields(data, allowed=['name', 'phone', 'aliases'], required=['name', 'phone'])
    if problem:
        return _error(problem, 'invalid_request', 400)

    name = _clean_name(data['name'])
    if name is None:
        return _error(f'name must be 1-{MAX_NAME_LENGTH} letters, digits or spaces', 'invalid_name', 400)
    phone = clean_phone(data['phone'])
    if phone is None:
        return _error('phone must be 3-15 digits, optionally starting with +', 'invalid_phone', 400)

    raw_aliases = data.get('aliases', [])
    if not isinstance(raw_aliases, list) or len(raw_aliases) > MAX_ALIASES:
        return _error(f'aliases must be a list of at most {MAX_ALIASES} names', 'invalid_aliases', 400)
    aliases = [_clean_name(a) for a in raw_aliases]
    if None in aliases:
        return _error('every alias must be a valid name', 'invalid_aliases', 400)

    if Contact.count_for_user(current_user.id) >= MAX_CONTACTS_PER_USER:
        return _error(f'You can save at most {MAX_CONTACTS_PER_USER} contacts', 'limit_reached', 409)
    try:
        contact_id = Contact.create(current_user.id, name, phone, aliases)
    except DuplicateKeyError:
        return _error('You already have a contact with this name', 'duplicate_contact', 409)
    return jsonify({'contact': Contact.to_json(Contact.find_by_id(contact_id, current_user.id))}), 201


@contacts_bp.route('/<contact_id>', methods=['DELETE'])
@limiter.limit('20 per minute')
@login_required
def delete_contact(contact_id):
    if not Contact.delete(contact_id, current_user.id):   # missing and "someone else's" look identical
        return _error('Not found', 'not_found', 404)
    return jsonify({'deleted': True}), 200


@contacts_bp.route('/match', methods=['POST'])
@limiter.limit('30 per minute')
@login_required
def match():
    """Look a spoken name up in the user's own contacts. This only FINDS people: it never
    calls anyone. 'many' means the page must ask the user which one."""
    data = request.get_json(silent=True)
    problem = _check_fields(data, allowed=['name'], required=['name'])
    if problem:
        return _error(problem, 'invalid_request', 400)
    if not isinstance(data['name'], str) or not 1 <= len(data['name'].strip()) <= MAX_NAME_LENGTH:
        return _error(f'name must be 1-{MAX_NAME_LENGTH} characters', 'invalid_name', 400)
    found = match_contact(Contact.find_by_user(current_user.id), data['name'])
    return jsonify({'result': found['result'],
                    'matches': [Contact.to_json(c) for c in found['matches']]}), 200
