"""Rule-based intent detection (Algorithm A).

Pipeline position:  text -> normalize -> detect intent -> extract fields -> (clarify | ok)
Unknown or ambiguous input NEVER becomes an action: it returns a clarification.
"""
import re
import unicodedata

from app.services.bangla_datetime import (
    parse_datetime, TODAY, TOMORROW, DAY_AFTER, PERIODS,
)

MAX_TEXT_LENGTH = 300

_BN_DIGITS = str.maketrans('০১২৩৪৫৬৭৮৯', '0123456789')
_TOKEN_RE = re.compile(r'[ঀ-৿a-z0-9:.]+')
_Y = unicodedata.normalize('NFC', 'য়')   # NFC splits য় into two codepoints

# A stem ending in '*' matches by prefix (Bangla suffixes: মেসেজটা, ফোনে).
# Any other stem must match the whole word (so 'বন্ধ' never matches 'বন্ধু').
# An intent matches if ANY rule matches; a rule matches if EVERY group has a hit.
RULES = {
    'reminder': [
        [['মনে'], ['করিয়ে', 'করাও', 'করাবে', 'করাইও']],
        [['রিমাইন্ডার*', 'reminder', 'remind']],
    ],
    'read_message': [
        [['মেসেজ*', 'বার্তা*', 'এসএমএস*', 'message', 'messages', 'sms'],
         ['পড়*', 'শোনা*', 'read']],
    ],
    'call': [
        [['ফোন*', 'কল', 'call', 'phone', 'dial'],
         ['কর*', 'লাগা*', 'দাও', 'দিন', 'call', 'phone', 'dial']],
    ],
    'repeat': [
        [['আবার', 'পুনরায়'], ['বল*', 'শোনা*']],
        [['repeat']],
        [['say', 'speak'], ['again']],
    ],
    'slow_down': [
        [['আস্তে', 'ধীরে'], ['বল*', 'পড়*']],
        [['slow', 'slower', 'slowly']],
    ],
    'stop': [
        [['থামো', 'থামুন', 'থাম', 'থামাও', 'বন্ধ', 'stop', 'স্টপ']],
    ],
}

# Words that only carry the command itself (removed when extracting the task / target)
_REMINDER_WORDS = {'মনে', 'করিয়ে', 'করাও', 'করাবে', 'করাইও', 'দিও', 'দাও', 'দিন',
                   'দিবে', 'দেবে', 'রিমাইন্ডার', 'reminder', 'remind', 'set', 'সেট'}
_FILLER = {'me', 'to', 'that', 'at', 'on', 'please', 'a', 'my', 'the',
           'আমাকে', 'আমায়', 'প্লিজ', 'করো', 'করুন', 'কর'}
_CALL_WORDS = {'কল', 'call', 'phone', 'dial', 'দাও', 'দিন'}

_TIME_TOKEN = re.compile(r'^\d{1,2}([:.]\d{2})?(টায়|টা)$|^\d{1,2}[:.]\d{2}$')
_NUMBER = re.compile(r'^\d{1,2}$')
_MARKERS = {'টা', 'টায়', 'am', 'pm', 'a.m', 'p.m'}
_PERIOD_SUFFIXES = {'', 'ে', _Y}

MESSAGES = {
    'no_speech': ('আমি কিছু শুনতে পাইনি। আবার বলুন।',
                  "I didn't hear anything. Please try again."),
    'too_long': ('কথাটা অনেক লম্বা। ছোট করে বলুন।',
                 'That was too long. Please say it shorter.'),
    'unknown_command': ('আমি বুঝতে পারিনি। রিমাইন্ডার, মেসেজ শোনা, নাকি ফোন করা?',
                        "I didn't understand. Do you want a reminder, to hear a message, or to make a call?"),
    'ambiguous_command': ('একসাথে একাধিক কথা শুনলাম। একটা একটা করে বলুন।',
                          'I heard more than one request. Please say one at a time.'),
    'missing_task': ('কী মনে করিয়ে দেব?', 'What should I remind you about?'),
}


def _clarify(intent, code):
    bn, en = MESSAGES[code]
    return {'intent': intent, 'status': 'clarify', 'code': code,
            'question_bn': bn, 'question_en': en}


def normalize(text):
    """NFC, Bangla digits -> ASCII, lowercase, danda and punctuation -> spaces."""
    t = unicodedata.normalize('NFC', text or '').translate(_BN_DIGITS).lower()
    return re.sub(r'[।?!,;"\'()]', ' ', t)


def tokenize(text):
    return [tok.strip('.:') for tok in _TOKEN_RE.findall(text) if tok.strip('.:')]


def _hit(tokens, stems):
    for stem in stems:
        for tok in tokens:
            if (stem.endswith('*') and tok.startswith(stem[:-1])) or tok == stem:
                return True
    return False


def matching_intents(tokens):
    return [name for name, rules in RULES.items()
            if any(all(_hit(tokens, group) for group in rule) for rule in rules)]


def _is_day_or_period(tok):
    if tok in TODAY or tok in TOMORROW or tok in DAY_AFTER:
        return True
    for prefix, _ in PERIODS:
        if tok.startswith(prefix) and tok[len(prefix):] in _PERIOD_SUFFIXES:
            return True
    return False


def _extract_task(tokens):
    kept, half = [], False
    for tok in tokens:
        if tok == 'সাড়ে':
            half = True
        elif _TIME_TOKEN.match(tok):
            half = False
        elif half and _NUMBER.match(tok):
            half = False
        elif tok in _MARKERS:
            if kept and _NUMBER.match(kept[-1]):
                kept.pop()
        elif tok in _REMINDER_WORDS or _is_day_or_period(tok):
            pass
        else:
            kept.append(tok)
    while kept and kept[0] in _FILLER:
        kept.pop(0)
    while kept and kept[-1] in _FILLER:
        kept.pop()
    return ' '.join(kept)


def _extract_call_target(tokens):
    return ' '.join(t for t in tokens
                    if t not in _CALL_WORDS and not t.startswith('ফোন')
                    and not t.startswith('কর') and t not in _FILLER)


def interpret(text, now=None):
    """Return a dict. 'status' is 'ok' or 'clarify'; 'intent' is always present."""
    if not isinstance(text, str) or not text.strip():
        return _clarify('unknown', 'no_speech')
    if len(text) > MAX_TEXT_LENGTH:
        return _clarify('unknown', 'too_long')

    clean = normalize(text)
    tokens = tokenize(clean)
    found = matching_intents(tokens)

    # "remind me to call mum" is a reminder whose task mentions a call.
    if 'reminder' in found:
        found = ['reminder']
    if len(found) == 0:
        return _clarify('unknown', 'unknown_command')
    if len(found) > 1:
        return _clarify('unknown', 'ambiguous_command')

    intent = found[0]
    if intent == 'reminder':
        task = _extract_task(tokens)
        if not task:
            return _clarify('reminder', 'missing_task')
        when = parse_datetime(clean, now=now)
        if when['status'] == 'clarify':
            return {'intent': 'reminder', 'status': 'clarify', 'task': task,
                    'code': when['code'], 'question_bn': when['question_bn'],
                    'question_en': when['question_en']}
        return {'intent': 'reminder', 'status': 'ok', 'task': task,
                'remind_at': when['remind_at'], 'assumed_today': when['assumed_today']}
    if intent == 'call':
        return {'intent': 'call', 'status': 'ok', 'target': _extract_call_target(tokens)}
    return {'intent': intent, 'status': 'ok'}