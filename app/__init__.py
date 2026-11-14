from flask import Flask, jsonify
from flask_wtf.csrf import CSRFError
from app.config import config
from app.extensions import mongo, login_manager, csrf, limiter
from app.models import init_indexes
from app.utils.security import init_security
import logging
import os


def create_app(config_name=None):
    """Application factory pattern. With no argument the config follows FLASK_ENV
    (FLASK_ENV=production -> ProductionConfig), so a deployed app is never left in debug mode."""
    if config_name is None:
        config_name = os.environ.get('FLASK_ENV', 'default')
        if config_name not in config:
            config_name = 'default'
    app = Flask(__name__, static_folder='../static')

    app.config.from_object(config[config_name])
    config[config_name].validate()

    logging.basicConfig(level=logging.INFO,
                        format='%(asctime)s %(levelname)s: %(message)s')

    if app.config.get('TRUST_PROXY'):
        # Behind a reverse proxy every request seems to come from the proxy's address, so all
        # users would share one rate limit. ProxyFix reads the real client IP from the ONE
        # trusted proxy hop. Never enable it without a proxy: the header could then be faked.
        from werkzeug.middleware.proxy_fix import ProxyFix
        app.wsgi_app = ProxyFix(app.wsgi_app, x_for=1, x_proto=1)

    mongo.init_app(app)
    login_manager.init_app(app)
    csrf.init_app(app)
    limiter.init_app(app)
    init_security(app)

    with app.app_context():
        init_indexes()

    from app.routes.auth import auth_bp
    app.register_blueprint(auth_bp)
    from app.routes.reminders import reminders_bp
    app.register_blueprint(reminders_bp)
    from app.routes.admin import admin_bp
    app.register_blueprint(admin_bp)
    from app.routes.voice import voice_bp
    app.register_blueprint(voice_bp)
    from app.routes.contacts import contacts_bp
    app.register_blueprint(contacts_bp)
    from app.routes.messages import messages_bp
    app.register_blueprint(messages_bp)
    from app.routes.pages import pages_bp
    app.register_blueprint(pages_bp)

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

    @app.errorhandler(413)
    def too_large(error):
        return jsonify({'error': 'Request too large'}), 413

    @app.errorhandler(429)
    def too_many(error):
        return jsonify({'error': 'Too many requests, slow down'}), 429

    @app.errorhandler(500)
    def internal_error(error):
        return jsonify({'error': 'Internal server error'}), 500

    return app
