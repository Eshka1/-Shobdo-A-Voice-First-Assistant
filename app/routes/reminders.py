from flask import Blueprint, request, jsonify
from flask_login import login_required, current_user
from app.extensions import mongo, limiter
from app.models.reminder import Reminder, STATUSES
from app.services.confirmation import ConfirmationService, MongoPendingStore
from app.services.reminder_service import execute_confirmed
from app.utils.validators import _check_fields

reminders_bp = Blueprint('reminders', __name__, url_prefix='/api/reminders')

# Confirmation result -> HTTP status
_CONFIRM_STATUS = {'not_found': 404, 'expired': 410, 'cancelled': 409,
                   'duplicate': 409, 'too_late': 409, 'error': 409}
# Execution result -> (HTTP status, message)
_EXECUTE_ERRORS = {
    'past': (400, 'That time has already passed'),
    'duplicate_reminder': (409, 'You already have this reminder'),
    'db_error': (503, 'Could not save the reminder. Nothing was saved.'),
    'invalid': (400, 'Invalid reminder details'),
    'unsupported': (400, 'Unsupported action'),
}


def _error(message, code, status, **extra):
    return jsonify({'error': message, 'code': code, **extra}), status


def _confirmations():
    return ConfirmationService(MongoPendingStore(mongo.db.pending_actions))


@reminders_bp.route('', methods=['GET'])
@login_required
def list_reminders():
    status = request.args.get('status')
    if status is not None and status not in STATUSES:
        return _error('Invalid status filter', 'invalid_status', 400)
    items = Reminder.find_by_user(current_user.id, status)   # owner comes from the session
    return jsonify({'reminders': [Reminder.to_json(r) for r in items]}), 200


@reminders_bp.route('', methods=['POST'])
@limiter.limit('20 per minute')
@login_required
def create_reminder():
    """Create a reminder from a CONFIRMED pending action. The body is only {"pending_id"}:
    the title and time come from what the server stored, never from the browser."""
    data = request.get_json(silent=True)
    problem = _check_fields(data, allowed=['pending_id'], required=['pending_id'])
    if problem:
        return _error(problem, 'invalid_request', 400)
    if not isinstance(data['pending_id'], str):
        return _error('pending_id must be a string', 'invalid_request', 400)

    confirmations = _confirmations()
    released = confirmations.confirm(current_user.id, data['pending_id'])
    if released['result'] != 'execute':
        return _error(released['question_en'], released['result'],
                      _CONFIRM_STATUS.get(released['result'], 400),
                      message_bn=released['question_bn'])

    outcome = execute_confirmed(confirmations, current_user.id, released)
    if outcome['result'] == 'created':
        return jsonify({'reminder': outcome['reminder'],
                        'pending_id': released['pending_id']}), 201
    status, message = _EXECUTE_ERRORS.get(outcome['result'], (500, 'Could not create the reminder'))
    return _error(message, outcome['result'], status)


def _change(reminder_id, new_status):
    outcome = Reminder.transition(reminder_id, current_user.id, new_status)
    if outcome == 'not_found':      # missing and "someone else's" look identical
        return _error('Not found', 'not_found', 404)
    if outcome == 'conflict':
        return _error('Only a pending reminder can be changed', 'not_pending', 409)
    doc = Reminder.find_by_id(reminder_id, current_user.id)
    return jsonify({'reminder': Reminder.to_json(doc)}), 200


@reminders_bp.route('/<reminder_id>/complete', methods=['POST'])
@login_required
def complete_reminder(reminder_id):
    return _change(reminder_id, 'completed')


@reminders_bp.route('/<reminder_id>/cancel', methods=['POST'])
@login_required
def cancel_reminder(reminder_id):
    return _change(reminder_id, 'cancelled')
