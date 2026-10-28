from flask import Flask, jsonify
from flask_wtf.csrf import CSRFError
from app.config import config
from app.extensions import mongo, login_manager, csrf, limiter
from app.models import init_indexes
from app.utils.security import init_security
import logging


def create_app(config_name='default'):
    """Application factory pattern"""
    app = Flask(__name__)

    app.config.from_object(config[config_name])
    config[config_name].validate()

    logging.basicConfig(level=logging.INFO,
                        format='%(asctime)s %(levelname)s: %(message)s')

    mongo.init_app(app)
    login_manager.init_app(app)
    csrf.init_app(app)
    limiter.init_app(app)
    init_security(app)

    with app.app_context():
        init_indexes()

    from app.routes.auth import auth_bp
    app.register_blueprint(auth_bp)

    @app.route('/health')
    def health():
        try:
            mongo.cx.admin.command('ping')
            db_status = 'connected'
        except Exception:
            db_status = 'disconnected'
        return jsonify({'status': 'healthy', 'database': db_status}), 200

    @app.errorhandler(CSRFError)
    def csrf_error(error):
        return jsonify({'error': 'CSRF validation failed'}), 400

    @app.errorhandler(403)
    def forbidden(error):
        return jsonify({'error': 'Forbidden'}), 403

    @app.errorhandler(404)
    def not_found(error):
        return jsonify({'error': 'Not found'}), 404

    @app.errorhandler(405)
    def method_not_allowed(error):
        return jsonify({'error': 'Method not allowed'}), 405

    @app.errorhandler(429)
    def too_many(error):
        return jsonify({'error': 'Too many requests, slow down'}), 429

    @app.errorhandler(500)
    def internal_error(error):
        return jsonify({'error': 'Internal server error'}), 500

    return app
