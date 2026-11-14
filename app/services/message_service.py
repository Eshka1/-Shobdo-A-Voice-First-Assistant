"""Read-aloud wording for a stored message. Pure logic: no Flask, no database.

SAFETY: message text is DATA. It is only ever placed inside the sentence that is spoken.
It is never passed to interpret() and can never become a command or a confirmation.
"""

NO_MESSAGES = ('কোনো নতুন মেসেজ নেই।', 'You have no new messages.')


def describe_message(doc):
    """Return (bangla, english) sentences to speak for one message, or NO_MESSAGES for None."""
    if doc is None:
        return NO_MESSAGES
    return (f"{doc['sender']} লিখেছেন: {doc['text']}",
            f"Message from {doc['sender']}: {doc['text']}")
