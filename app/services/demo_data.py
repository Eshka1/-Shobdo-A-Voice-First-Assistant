"""Sample contacts and messages for the demo user, so the call and read-aloud demos have
something to work with. Runs only from the demo sign-in and only when the user has none."""
from datetime import datetime, timedelta, timezone

from pymongo.errors import PyMongoError

from app.models.contact import Contact
from app.models.message import Message

# Fictional numbers: 01700-000000 style numbers are placeholders, not real people.
DEMO_CONTACTS = [
    ('ছেলে', '+8801700000001', ['son', 'রহিম', 'rahim']),
    ('মেয়ে', '+8801700000002', ['daughter', 'রহিমা', 'rahima']),
    ('ডাক্তার', '+8801700000003', ['doctor', 'ডাক্তার সাহেব']),
]
DEMO_MESSAGES = [
    ('ছেলে', 'মা, আমি শুক্রবার বাড়ি আসব। ওষুধ ঠিকমতো খেয়ো।'),
    ('ডাক্তার', 'আগামী সোমবার সকাল ১০টায় আপনার চেকআপ।'),
]


def seed_demo_data(user_id):
    """Idempotent: does nothing if the user already has contacts / messages."""
    try:
        if Contact.count_for_user(user_id) == 0:
            for name, phone, aliases in DEMO_CONTACTS:
                Contact.create(user_id, name, phone, aliases)
        if Message.count_for_user(user_id) == 0:
            now = datetime.now(timezone.utc)
            for minutes_ago, (sender, text) in zip((90, 30), DEMO_MESSAGES):
                Message.create(user_id, sender, text, received_at=now - timedelta(minutes=minutes_ago))
    except PyMongoError:
        pass     # sample data is a convenience; signing in must still work
