from app.extensions import mongo

def init_indexes():
    """Create database indexes for performance and uniqueness"""
    # Users: email must be unique (prevents duplicate accounts)
    mongo.db.users.create_index('email', unique=True)

    # Reminders: fast lookup by user + time
    mongo.db.reminders.create_index([('user_id', 1), ('remind_at', 1)])
    mongo.db.reminders.create_index([('user_id', 1), ('status', 1)])

    # Reminders: the same user cannot have two PENDING reminders with the same
    # idempotency key (same task + same time). Completed/cancelled ones don't count,
    # so a cancelled reminder can be created again.
    mongo.db.reminders.create_index(
        [('user_id', 1), ('idempotency_key', 1)],
        unique=True,
        partialFilterExpression={'idempotency_key': {'$type': 'string'}, 'status': 'pending'},
    )

    print("Database indexes created successfully")
