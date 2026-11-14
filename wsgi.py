"""Production entry point (Eleen, backend). Development still uses:  python run.py

Run with ONE process:
    waitress-serve --listen=0.0.0.0:8000 --threads=8 wsgi:app          (Windows / Linux / Mac)
    gunicorn --workers 1 --threads 8 --bind 0.0.0.0:8000 wsgi:app      (Linux hosts, if installed)

Why one process: the reminder scheduler and the rate-limit counters live in this process's
memory. Two processes would mean two schedulers and two separate sets of counters. Reminders
would still not fire twice (each one is claimed atomically in MongoDB), but one process is
the simple, correct setup for this MVP. Use threads, not extra workers, for more capacity.
"""
import os

from app import create_app
from app.services.scheduler_service import start_scheduler

# FLASK_ENV=production must be set on the server: it turns debug off and makes the session
# cookie HTTPS-only. Defaulting to production here means forgetting the variable is safe.
app = create_app(os.environ.get('FLASK_ENV', 'production'))

# RUN_SCHEDULER=false lets you start a second copy (for example a one-off shell) without a
# second scheduler.
if os.environ.get('RUN_SCHEDULER', 'true').strip().lower() != 'false':
    start_scheduler(app)
