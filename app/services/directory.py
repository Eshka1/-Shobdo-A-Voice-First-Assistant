"""The voice pipeline's only door to contacts and messages.

voice_service stays pure logic (easy to test with a fake directory); this class is the
MongoDB-backed version the route uses. Every method takes the user id from the session,
so one user can never reach another user's contacts or messages.
"""
from app.models.contact import Contact
from app.models.message import Message


class MongoDirectory:
    def contacts(self, user_id):
        return Contact.find_by_user(user_id)

    def contact(self, contact_id, user_id):
        return Contact.find_by_id(contact_id, user_id)

    def take_next_message(self, user_id):
        """Oldest unread message, marked as read. None when there is nothing new."""
        doc = Message.oldest_unread(user_id)
        if doc is not None:
            Message.mark_read(str(doc['_id']), user_id)
        return doc
