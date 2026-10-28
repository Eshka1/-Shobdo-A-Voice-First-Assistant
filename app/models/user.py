from datetime import datetime, timezone
from bson import ObjectId
import bcrypt
from flask_login import UserMixin
from app.extensions import mongo


class User:
    collection = 'users'

    @staticmethod
    def create(name, email, password, role='user'):
        """Create a new user with a bcrypt-hashed password (cost 12)."""
        password_hash = bcrypt.hashpw(
            password.encode('utf-8'),
            bcrypt.gensalt(rounds=12)
        ).decode('utf-8')

        user_data = {
            'name': name,
            'email': email.lower().strip(),
            'password_hash': password_hash,
            'role': role,
            'created_at': datetime.now(timezone.utc)
        }
        result = mongo.db[User.collection].insert_one(user_data)
        return str(result.inserted_id)

    @staticmethod
    def find_by_email(email):
        return mongo.db[User.collection].find_one({'email': email.lower().strip()})

    @staticmethod
    def find_by_id(user_id):
        try:
            return mongo.db[User.collection].find_one({'_id': ObjectId(user_id)})
        except Exception:
            return None

    @staticmethod
    def verify_password(user, password):
        return bcrypt.checkpw(
            password.encode('utf-8'),
            user['password_hash'].encode('utf-8')
        )

    @staticmethod
    def to_json(user):
        """Safe for API responses: never includes password_hash."""
        created = user.get('created_at')
        return {
            'id': str(user['_id']),
            'name': user['name'],
            'email': user['email'],
            'role': user['role'],
            'created_at': created.isoformat() if created else None
        }


class LoginUser(UserMixin):
    """Thin wrapper so Flask-Login can work with our MongoDB user documents."""

    def __init__(self, doc):
        self.id = str(doc['_id'])
        self.name = doc['name']
        self.email = doc['email']
        self.role = doc['role']

    @property
    def is_admin(self):
        return self.role == 'admin'
