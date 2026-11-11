from flask import Blueprint, render_template
from app.extensions import limiter

pages_bp = Blueprint("pages", __name__, template_folder="../../templates")

# Temporary sample data, replaced by real reminders from the database later
SAMPLE_REMINDERS = [
    {"task_en": "Take medicine", "task_bn": "ওষুধ খান",
     "time_en": "9:00 PM", "time_bn": "রাত ৯:০০"},
    {"task_en": "Call my son", "task_bn": "ছেলেকে ফোন করুন",
     "time_en": "6:00 PM", "time_bn": "সন্ধ্যা ৬:০০"},
    {"task_en": "Drink water", "task_bn": "পানি পান করুন",
     "time_en": "11:00 AM", "time_bn": "সকাল ১১:০০"},
]


@pages_bp.route("/")
@limiter.exempt
def home():
    return render_template("index.html", reminders=SAMPLE_REMINDERS)