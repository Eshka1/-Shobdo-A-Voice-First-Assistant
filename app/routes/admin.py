from flask import Blueprint, request, jsonify
from flask_login import login_required
from app.extensions import mongo
from app.models.reminder import STATUSES
from app.models.user import User
from app.utils.security import admin_required

admin_bp = Blueprint('admin', __name__, url_prefix='/api/admin')

MAX_PAGE_SIZE = 100


def _int_arg(name, default, minimum, maximum):
    """Read an integer query argument. Returns None if it is not a valid integer in range."""
    raw = request.args.get(name)
    if raw is None:
        return default
    try:
        value = int(raw)
    except ValueError:
        return None
    return value if minimum <= value <= maximum else None


# Order matters: @login_required runs first (anonymous -> 401),
# then @admin_required (logged in but not admin -> 403).
@admin_bp.route('/users', methods=['GET'])
@login_required
@admin_required
def list_users():
    limit = _int_arg('limit', 50, 1, MAX_PAGE_SIZE)
    skip = _int_arg('skip', 0, 0, 1_000_000)
    if limit is None or skip is None:
        return jsonify({'error': f'limit must be 1-{MAX_PAGE_SIZE} and skip must be 0 or more'}), 400
    cursor = (mongo.db.users.find({}, {'password_hash': 0})   # hash never leaves the database
              .sort('created_at', 1).skip(skip).limit(limit))
    return jsonify({'users': [User.to_json(u) for u in cursor],
                    'total': mongo.db.users.count_documents({}),
                    'limit': limit, 'skip': skip}), 200


@admin_bp.route('/stats', methods=['GET'])
@login_required
@admin_required
def stats():
    """Counts only. Admins never see other people's reminder text."""
    return jsonify({
        'users': mongo.db.users.count_documents({}),
        'admins': mongo.db.users.count_documents({'role': 'admin'}),
        'reminders': {s: mongo.db.reminders.count_documents({'status': s}) for s in STATUSES},
    }), 200
