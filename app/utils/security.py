from functools import wraps
from flask import jsonify, abort
from flask_login import current_user
from app.extensions import login_manager
from app.models.user import User, LoginUser


def admin_required(fn):
    """Use below @login_required. Non-admins get 403."""
    @wraps(fn)
    def wrapper(*args, **kwargs):
        if not current_user.is_authenticated or getattr(current_user, 'role', None) != 'admin':
            abort(403)
        return fn(*args, **kwargs)
    return wrapper


def init_security(app):
    @login_manager.user_loader
    def load_user(user_id):
        doc = User.find_by_id(user_id)
        return LoginUser(doc) if doc else None

    @login_manager.unauthorized_handler
    def unauthorized():
        return jsonify({'error': 'Authentication required'}), 401

    @app.after_request
    def set_security_headers(response):
        response.headers['Content-Security-Policy'] = \
            "default-src 'self'; script-src 'self'; object-src 'none'"
        response.headers['X-Content-Type-Options'] = 'nosniff'
        response.headers['X-Frame-Options'] = 'DENY'
        response.headers['Referrer-Policy'] = 'no-referrer'
        if app.config.get('FLASK_ENV') == 'production':
            response.headers['Strict-Transport-Security'] = 'max-age=31536000'
        return response
