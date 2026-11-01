from flask import Blueprint, render_template
from app.extensions import limiter

pages_bp = Blueprint("pages", __name__, template_folder="../../templates")


@pages_bp.route("/")
@limiter.exempt
def home():
    return render_template("index.html")