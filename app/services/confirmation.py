"""Server-side pending actions and confirmation (Algorithm C, Core 5).

The browser never says "I confirmed". It says "pending_id X, the user replied Y",
and the SERVER decides, using the exact action it stored earlier.

States:  awaiting_confirmation -> executing -> completed | error
         awaiting_confirmation -> cancelled | expired
(Listening / Processing / Clarification live in the browser and in interpret();
a pending action only exists once the details are complete.)
"""
import copy
import hashlib
import json
import secrets
import threading
from datetime import datetime, timedelta, timezone

from app.services.intent import normalize, tokenize

UTC = timezone.utc
DEFAULT_TTL_SECONDS = 120

AWAITING = 'awaiting_confirmation'
EXECUTING = 'executing'
COMPLETED = 'completed'
CANCELLED = 'cancelled'
EXPIRED = 'expired'
ERROR = 'error'

# ---------------------------------------------------------------- reply words
# YES is strict: every word must be a yes-word. NO is lenient: extra words are OK,
# because cancelling by mistake is safe and executing by mistake is not.
_YES = [['হ্যাঁ'], ['হ্যা'], ['হাঁ'], ['ঠিক', 'আছে'], ['yes'], ['yeah'], ['yep'], ['confirm']]
_NO = [['না'], ['নাহ'], ['বাতিল'], ['থামো'], ['থামুন'], ['ভুল'],
       ['no'], ['nope'], ['cancel'], ['stop'], ['wrong']]
_NEUTRAL = {'জি', 'please', 'প্লিজ', 'ধন্যবাদ', 'thanks'}
_MAX_REPLY_TOKENS = 6

MESSAGES = {
    'unclear': ('বুঝতে পারিনি। হ্যাঁ নাকি না?', "I didn't understand. Yes or no?"),
    'expired': ('সময় শেষ হয়ে গেছে। আবার বলুন।', 'That request timed out. Please say it again.'),
    'cancelled': ('বাতিল করা হয়েছে।', 'Cancelled.'),
    'duplicate': ('এটা আগেই করা হয়েছে।', 'That was already done.'),
    'too_late': ('এটা আর বাতিল করা যাবে না।', 'That can no longer be cancelled.'),
    'not_found': ('এই অনুরোধটা পাওয়া যায়নি। আবার বলুন।', "I couldn't find that request. Please say it again."),
}


def _find(tokens, phrase):
    n = len(phrase)
    for i in range(len(tokens) - n + 1):
        if tokens[i:i + n] == phrase:
            return i
    return -1


def classify_reply(text):
    """Return 'yes', 'no' or 'unclear'. Conflicts and extra words after a yes are 'unclear'."""
    if not isinstance(text, str):
        return 'unclear'
    tokens = [t for t in tokenize(normalize(text)) if t not in _NEUTRAL]
    if not tokens or len(tokens) > _MAX_REPLY_TOKENS:
        return 'unclear'
    used = [False] * len(tokens)
    yes = no = False
    for phrase in _YES:
        i = _find(tokens, phrase)
        if i >= 0:
            yes = True
            for k in range(i, i + len(phrase)):
                used[k] = True
    for phrase in _NO:
        if _find(tokens, phrase) >= 0:
            no = True
    if yes and no:
        return 'unclear'
    if no:
        return 'no'
    if yes and all(used):
        return 'yes'
    return 'unclear'


# ------------------------------------------------------------------- helpers
def _now(now):
    if now is None:
        return datetime.now(UTC)
    if now.tzinfo is None:
        raise ValueError('now must be timezone-aware')
    return now.astimezone(UTC)


def _aware(dt):
    """MongoDB hands datetimes back naive (UTC). Make them aware again."""
    return dt if dt.tzinfo else dt.replace(tzinfo=UTC)


def idempotency_key(user_id, action_type, params):
    """Same user + same action + same details => same key (used to stop duplicates)."""
    canonical = json.dumps(params, sort_keys=True, ensure_ascii=False, default=str)
    return hashlib.sha256(f'{user_id}|{action_type}|{canonical}'.encode('utf-8')).hexdigest()


def _valid_id(value):
    # Must be a plain string: a dict like {"$ne": null} must never reach a database query.
    return isinstance(value, str) and 0 < len(value) <= 128


# -------------------------------------------------------------------- stores
class InMemoryPendingStore:
    """For tests and local experiments. Same behaviour as the Mongo store."""

    def __init__(self):
        self._docs = {}
        self._lock = threading.Lock()

    def insert(self, doc):
        with self._lock:
            self._docs[doc['_id']] = copy.deepcopy(doc)

    def get(self, pending_id, user_id):
        with self._lock:
            doc = self._docs.get(pending_id)
            return copy.deepcopy(doc) if doc and doc['user_id'] == user_id else None

    def find_awaiting_by_key(self, user_id, key, now):
        with self._lock:
            for doc in self._docs.values():
                if (doc['user_id'] == user_id and doc['idempotency_key'] == key
                        and doc['state'] == AWAITING and _aware(doc['expires_at']) > now):
                    return copy.deepcopy(doc)
        return None

    def transition(self, pending_id, user_id, from_states, new_state, now,
                   expiry='any', set_fields=None):
        """Atomic: change state only if it is currently one of from_states."""
        with self._lock:
            doc = self._docs.get(pending_id)
            if not doc or doc['user_id'] != user_id or doc['state'] not in from_states:
                return None
            expires = _aware(doc['expires_at'])
            if expiry == 'unexpired' and not expires > now:
                return None
            if expiry == 'expired' and not expires <= now:
                return None
            doc['state'] = new_state
            doc['updated_at'] = now
            doc.update(set_fields or {})
            return copy.deepcopy(doc)


class MongoPendingStore:
    """Same interface on a MongoDB collection, e.g. MongoPendingStore(mongo.db.pending_actions)."""

    def __init__(self, collection):
        self.col = collection

    def insert(self, doc):
        self.col.insert_one(dict(doc))

    def get(self, pending_id, user_id):
        return self.col.find_one({'_id': pending_id, 'user_id': user_id})

    def find_awaiting_by_key(self, user_id, key, now):
        return self.col.find_one({'user_id': user_id, 'idempotency_key': key,
                                  'state': AWAITING, 'expires_at': {'$gt': now}})

    def transition(self, pending_id, user_id, from_states, new_state, now,
                   expiry='any', set_fields=None):
        from pymongo import ReturnDocument
        query = {'_id': pending_id, 'user_id': user_id, 'state': {'$in': list(from_states)}}
        if expiry == 'unexpired':
            query['expires_at'] = {'$gt': now}
        elif expiry == 'expired':
            query['expires_at'] = {'$lte': now}
        update = {'$set': {'state': new_state, 'updated_at': now, **(set_fields or {})}}
        # find_one_and_update is atomic: two simultaneous "yes" cannot both win.
        return self.col.find_one_and_update(query, update, return_document=ReturnDocument.AFTER)


# ------------------------------------------------------------------- service
class ConfirmationService:
    def __init__(self, store, ttl_seconds=DEFAULT_TTL_SECONDS):
        self.store = store
        self.ttl = timedelta(seconds=ttl_seconds)

    # ---- results (one consistent shape) ----
    @staticmethod
    def _msg(result, pending_id=None):
        bn, en = MESSAGES[result]
        out = {'result': result, 'question_bn': bn, 'question_en': en}
        if pending_id:
            out['pending_id'] = pending_id
        return out

    @staticmethod
    def _action(doc):
        return {'result': 'execute', 'pending_id': doc['_id'], 'type': doc['type'],
                'params': doc['params'], 'idempotency_key': doc['idempotency_key']}

    # ---- propose ----
    def propose(self, user_id, action_type, params, summary_bn, summary_en,
                now=None, replaces=None):
        """Store a pending action and return what must be read back to the user."""
        if not _valid_id(user_id) or not _valid_id(action_type):
            raise ValueError('user_id and action_type must be non-empty strings')
        if not isinstance(params, dict):
            raise ValueError('params must be a dict')
        now = _now(now)

        if _valid_id(replaces):          # a correction replaces the earlier proposal
            self.store.transition(replaces, user_id, [AWAITING], CANCELLED, now)

        key = idempotency_key(user_id, action_type, params)
        doc = self.store.find_awaiting_by_key(user_id, key, now)   # double-tap protection
        reused = doc is not None
        if not reused:
            doc = {'_id': secrets.token_urlsafe(16), 'user_id': user_id, 'type': action_type,
                   'params': params, 'summary_bn': summary_bn, 'summary_en': summary_en,
                   'idempotency_key': key, 'state': AWAITING,
                   'created_at': now, 'updated_at': now, 'expires_at': now + self.ttl}
            self.store.insert(doc)
        return {'result': 'proposed', 'pending_id': doc['_id'], 'type': doc['type'],
                'params': doc['params'], 'summary_bn': doc['summary_bn'],
                'summary_en': doc['summary_en'], 'reused': reused,
                'expires_at': _aware(doc['expires_at']).isoformat(),
                'expires_in_seconds': int((_aware(doc['expires_at']) - now).total_seconds())}

    # ---- confirm / reply / cancel ----
    def confirm(self, user_id, pending_id, now=None):
        now = _now(now)
        if not (_valid_id(user_id) and _valid_id(pending_id)):
            return self._msg('not_found')
        doc = self.store.transition(pending_id, user_id, [AWAITING], EXECUTING, now,
                                    expiry='unexpired')
        if doc:
            return self._action(doc)
        return self._explain_failure(user_id, pending_id, now, for_cancel=False)

    def cancel(self, user_id, pending_id, now=None):
        now = _now(now)
        if not (_valid_id(user_id) and _valid_id(pending_id)):
            return self._msg('not_found')
        doc = self.store.transition(pending_id, user_id, [AWAITING], CANCELLED, now)
        if doc:
            return self._msg('cancelled', pending_id)
        return self._explain_failure(user_id, pending_id, now, for_cancel=True)

    def reply(self, user_id, pending_id, text, now=None):
        """Handle a spoken or typed reply to the read-back."""
        verdict = classify_reply(text)
        if verdict == 'yes':
            return self.confirm(user_id, pending_id, now)
        if verdict == 'no':
            return self.cancel(user_id, pending_id, now)
        now = _now(now)
        if not (_valid_id(user_id) and _valid_id(pending_id)):
            return self._msg('not_found')
        doc = self.store.get(pending_id, user_id)
        if doc and doc['state'] == AWAITING and _aware(doc['expires_at']) > now:
            out = self._msg('unclear', pending_id)       # still waiting; nothing executed
            out['summary_bn'], out['summary_en'] = doc['summary_bn'], doc['summary_en']
            return out
        return self._explain_failure(user_id, pending_id, now, for_cancel=False)

    # ---- executor reports back ----
    def complete(self, user_id, pending_id, now=None):
        if not (_valid_id(user_id) and _valid_id(pending_id)):
            return False
        return self.store.transition(pending_id, user_id, [EXECUTING], COMPLETED, _now(now)) is not None

    def fail(self, user_id, pending_id, reason_code, now=None):
        """reason_code is a short code like 'db_error' (never user content)."""
        if not (_valid_id(user_id) and _valid_id(pending_id)):
            return False
        return self.store.transition(pending_id, user_id, [EXECUTING], ERROR, _now(now),
                                     set_fields={'error_code': str(reason_code)[:64]}) is not None

    # ---- why did a transition not happen? ----
    def _explain_failure(self, user_id, pending_id, now, for_cancel):
        doc = self.store.get(pending_id, user_id)    # scoped by owner: others see not_found
        if doc is None:
            return self._msg('not_found')
        state = doc['state']
        if state == AWAITING:
            if _aware(doc['expires_at']) <= now:
                self.store.transition(pending_id, user_id, [AWAITING], EXPIRED, now,
                                      expiry='expired')
                return self._msg('expired', pending_id)
            return self._msg('duplicate', pending_id)   # lost a race; someone else moved it
        if state == EXPIRED:
            return self._msg('expired', pending_id)
        if state == CANCELLED:
            return self._msg('cancelled', pending_id)
        if state in (EXECUTING, COMPLETED):
            return self._msg('too_late' if for_cancel else 'duplicate', pending_id)
        return {'result': 'error', 'pending_id': pending_id}   # state == ERROR