from flask import Blueprint, request, jsonify
from flask_login import login_required, current_user
from pymongo.errors import PyMongoError
from app.extensions import mongo, limiter
from app.services.confirmation import ConfirmationService, MongoPendingStore
from app.services.directory import MongoDirectory
from app.services.reminder_service import MAX_TITLE_LENGTH
from app.services.voice_service import MESSAGES, clean_task, clean_text, handle_voice_command
from app.utils.validators import _check_fields

voice_bp = Blueprint('voice', __name__, url_prefix='/api')


def _bad_request(message):
    return jsonify({'error': message, 'code': 'invalid_request'}), 400


def _respond(payload, status):
    """The page reads state / message_bn / message_en; the backend's own names are
    status / speak_bn / speak_en. Both are sent, always with identical values."""
    payload['state'] = payload['status']
    payload['message_bn'], payload['message_en'] = payload['speak_bn'], payload['speak_en']
    return jsonify(payload), status


@voice_bp.route('/voice-command', methods=['POST'])
@limiter.limit('30 per minute')
@login_required
def voice_command():
    """The user is always taken from the session. The body can never name a user or say
    "confirmed": unknown fields are rejected, and only a server-stored pending action can run."""
    data = request.get_json(silent=True)
    problem = _check_fields(data, allowed=['text', 'pending_id', 'task', 'lang', 'awaiting'],
                            required=['text'])
    if problem:
        return _bad_request(problem)
    text, pending_id, task = data['text'], data.get('pending_id'), data.get('task')
    awaiting = data.get('awaiting')

    # lang is the page's display language. The reply always carries both languages, so it
    # changes nothing here; it is validated so that junk is still rejected.
    if data.get('lang') not in (None, 'en', 'bn'):
        return _bad_request('lang must be "en" or "bn"')
    if awaiting not in (None, 'contact'):
        return _bad_request('awaiting must be "contact"')

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
    if sum(value is not None for value in (pending_id, task, awaiting)) > 1:
        return _bad_request('send only one of pending_id, task or awaiting')

    confirmations = ConfirmationService(MongoPendingStore(mongo.db.pending_actions))
    try:
        payload, status = handle_voice_command(confirmations, current_user.id, clean_text(text),
                                               pending_id=pending_id, task=task,
                                               awaiting=awaiting, directory=MongoDirectory())
    except PyMongoError:
        bn, en = MESSAGES['db_error']
        return _respond({'status': 'failed', 'code': 'db_error', 'speak_bn': bn, 'speak_en': en}, 503)
    return _respond(payload, status)
