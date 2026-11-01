import os
import bcrypt
from flask import Blueprint, request, jsonify, session
from flask_login import login_user, logout_user, login_required, current_user
from flask_wtf.csrf import generate_csrf
from pymongo.errors import DuplicateKeyError
from app.extensions import mongo, limiter
from app.models.user import User, LoginUser
from app.utils.validators import validate_register, validate_login

auth_bp = Blueprint('auth', __name__, url_prefix='/api/auth')

# Burns the same CPU time when the email doesn't exist (anti-enumeration)
_DUMMY_HASH = bcrypt.hashpw(b'not-a-real-password', bcrypt.gensalt(rounds=12))


def _invalid_credentials():
    return jsonify({'error': 'Invalid credentials'}), 401


@auth_bp.route('/csrf-token', methods=['GET'])
def csrf_token():
    return jsonify({'csrf_token': generate_csrf()}), 200


@auth_bp.route('/register', methods=['POST'])
@limiter.limit('10 per minute')
def register():
    data, error = validate_register(request.get_json(silent=True))
    if error:
        return jsonify({'error': error}), 400

    # Role is decided by the server, never by the request
    role = 'user'
    bootstrap = os.environ.get('ADMIN_BOOTSTRAP_EMAIL', '').strip().lower()
    if bootstrap and data['email'] == bootstrap \
            and mongo.db.users.count_documents({'role': 'admin'}) == 0:
        role = 'admin'

    try:
        user_id = User.create(data['name'], data['email'], data['password'], role)
    except DuplicateKeyError:
        return jsonify({'error': 'Could not register with these details'}), 409

    user = User.find_by_id(user_id)
    return jsonify({'user': User.to_json(user)}), 201


@auth_bp.route('/login', methods=['POST'])
@limiter.limit('5 per minute')
def login():
    data, error = validate_login(request.get_json(silent=True))
    if error:
        return jsonify({'error': error}), 400

    password = data['password']
    if len(password.encode('utf-8')) > 72:
        return _invalid_credentials()

    user = User.find_by_email(data['email'])
    if user is None:
        bcrypt.checkpw(password.encode('utf-8'), _DUMMY_HASH)
        return _invalid_credentials()
    if not User.verify_password(user, password):
        return _invalid_credentials()

    session.clear()
    login_user(LoginUser(user), remember=False)
    session.permanent = True
    return jsonify({'user': User.to_json(user),
                    'csrf_token': generate_csrf()}), 200


@auth_bp.route('/logout', methods=['POST'])
@login_required
def logout():
    logout_user()
    session.clear()
    return jsonify({'message': 'Logged out'}), 200


@auth_bp.route('/me', methods=['GET'])
@login_required
def me():
    return jsonify({'user': {'id': current_user.id, 'name': current_user.name,
                             'email': current_user.email, 'role': current_user.role}}), 200
