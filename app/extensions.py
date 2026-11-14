from flask_pymongo import PyMongo
from flask_login import LoginManager
from flask_wtf.csrf import CSRFProtect
from flask_limiter import Limiter
from flask_limiter.util import get_remote_address

# These are like empty power strips - we'll plug them into the app later
mongo = PyMongo()
login_manager = LoginManager()
csrf = CSRFProtect()
limiter = Limiter(
    key_func=get_remote_address,
    # Per client IP. Generous enough for one person using the page all day (the page polls
    # and reloads the reminder list), strict enough to stop a script. Sensitive routes
    # (login, register, voice-command, reminder creation) set their own tighter limits.
    default_limits=["3000 per day", "600 per hour"]
)
