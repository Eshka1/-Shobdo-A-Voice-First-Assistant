import os
from datetime import timedelta
from dotenv import load_dotenv

load_dotenv()

class Config:
    """Base configuration"""
    SECRET_KEY = os.environ.get('SECRET_KEY')
    MONGO_URI = os.environ.get('MONGO_URI')
    FLASK_ENV = os.environ.get('FLASK_ENV', 'development')

    # Security settings
    SESSION_COOKIE_HTTPONLY = True
    SESSION_COOKIE_SAMESITE = 'Lax'
    PERMANENT_SESSION_LIFETIME = timedelta(hours=2)
    RATELIMIT_HEADERS_ENABLED = True
    # Demo sign-in (POST /api/auth/demo-login). OFF unless DEMO_LOGIN_ENABLED=true in .env.
    DEMO_LOGIN_ENABLED = os.environ.get('DEMO_LOGIN_ENABLED', '').strip().lower() == 'true'
    DEMO_USER_EMAIL = os.environ.get('DEMO_USER_EMAIL', 'demo@shobdo.local').strip().lower()
    # Set TRUST_PROXY=true only when the app runs behind ONE reverse proxy (Render, Railway...).
    TRUST_PROXY = os.environ.get('TRUST_PROXY', '').strip().lower() == 'true'
    MAX_CONTENT_LENGTH = 16 * 1024   # reject oversized request bodies (413) before they are read

    @classmethod
    def validate(cls):
        """Check required settings exist"""
        required = ['SECRET_KEY', 'MONGO_URI']
        missing = [var for var in required if not getattr(cls, var)]
        if missing:
            raise ValueError(f"Missing required environment variables: {', '.join(missing)}")

class DevelopmentConfig(Config):
    DEBUG = True

class ProductionConfig(Config):
    DEBUG = False
    SESSION_COOKIE_SECURE = True

config = {
    'development': DevelopmentConfig,
    'production': ProductionConfig,
    'default': DevelopmentConfig
}
