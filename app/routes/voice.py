from flask import Blueprint, request, jsonify
from flask_login import login_required, current_user
from pymongo.errors import PyMongoError
from app.extensions import mongo, limiter
from app.services.confirmation import ConfirmationService, MongoPendingStore
from app.services.reminder_service import MAX_TITLE_LENGTH
from app.services.voice_service import MESSAGES, clean_task, clean_text, handle_voice_command
from app.utils.validators import _check_fields

voice_bp = Blueprint('voice', __name__, url_prefix='/api')


def _bad_request(message):
    return jsonify({'error': message, 'code': 'invalid_request'}), 400


@voice_bp.route('/voice-command', methods=['POST'])
@limiter.limit('30 per minute')
@login_required
def voice_command():
    """The user is always taken from the session. The body can never name a user or say
    "confirmed": unknown fields are rejected, and only a server-stored pending action can run."""
    data = request.get_json(silent=True)
    problem = _check_fields(data, allowed=['text', 'pending_id', 'task'], required=['text'])
    if problem:
        return _bad_request(problem)
    text, pending_id, task = data['text'], data.get('pending_id'), data.get('task')

    if not isinstance(text, str):
        return _bad_request('text must be a string')
    if pending_id is not None and (not isinstance(pending_id, str) or not 0 < len(pending_id) <= 128):
        return _bad_request('pending_id must be a short string')   # blocks {"$ne": null} style input
    if task is not None:
        if not isinstance(task, str):
            return _bad_request('task must be a string')
        task = clean_task(task)
        if not 1 <= len(task) <= MAX_TITLE_LENGTH:
            return _bad_request(f'task must be 1-{MAX_TITLE_LENGTH} characters')
    if pending_id is not None and task is not None:
        return _bad_request('send either pending_id or task, not both')

    confirmations = ConfirmationService(MongoPendingStore(mongo.db.pending_actions))
    try:
        payload, status = handle_voice_command(confirmations, current_user.id, clean_text(text),
                                               pending_id=pending_id, task=task)
    except PyMongoError:
        bn, en = MESSAGES['db_error']
        return jsonify({'status': 'failed', 'code': 'db_error', 'speak_bn': bn, 'speak_en': en}), 503
    return jsonify(payload), status
