from flask import Blueprint, request, jsonify
from flask_login import login_required, current_user

from app.extensions import limiter
from app.models.message import Message
from app.services.message_service import describe_message
from app.utils.validators import _check_fields

messages_bp = Blueprint('messages', __name__, url_prefix='/api/messages')


def _error(message, code, status):
    return jsonify({'error': message, 'code': code}), status


@messages_bp.route('', methods=['GET'])
@login_required
def list_messages():
    items = Message.find_by_user(current_user.id)        # owner comes from the session
    return jsonify({'messages': [Message.to_json(m) for m in items]}), 200


@messages_bp.route('/read', methods=['POST'])
@limiter.limit('30 per minute')
@login_required
def read_message():
    """Return one of the user's OWN messages as text to speak, and mark it as read.
    Body: {} for the oldest unread message, or {"message_id": "..."} for a specific one.
    POST (not GET) because it changes data: the message becomes "read"."""
    data = request.get_json(silent=True)
    if data is None:
        data = {}
    problem = _check_fields(data, allowed=['message_id'], required=[])
    if problem:
        return _error(problem, 'invalid_request', 400)

    message_id = data.get('message_id')
    if message_id is None:
        doc = Message.oldest_unread(current_user.id)
    else:
        if not isinstance(message_id, str):          # blocks {"$ne": null} style input
            return _error('message_id must be a string', 'invalid_request', 400)
        doc = Message.find_by_id(message_id, current_user.id)
        if doc is None:                              # missing and "someone else's" look identical
            return _error('Not found', 'not_found', 404)

    if doc is not None:
        Message.mark_read(str(doc['_id']), current_user.id)
    bn, en = describe_message(doc)
    return jsonify({'message': Message.to_json(doc) if doc else None,
                    'speak_bn': bn, 'speak_en': en}), 200
