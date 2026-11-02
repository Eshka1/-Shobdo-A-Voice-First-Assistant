from flask import Blueprint, render_template
from app.extensions import limiter

pages_bp = Blueprint("pages", __name__, template_folder="../../templates")

# Temporary sample data, replaced by real reminders from the database later
SAMPLE_REMINDERS = [
    {"task": "Take medicine", "time": "9:00 PM"},
    {"task": "Call my son", "time": "6:00 PM"},
    {"task": "Drink water", "time": "11:00 AM"},
]


@pages_bp.route("/")
@limiter.exempt
def home():
    return render_template("index.html", reminders=SAMPLE_REMINDERS)