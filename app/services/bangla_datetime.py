import re
import unicodedata
from datetime import datetime, time, timedelta
from zoneinfo import ZoneInfo

TZ = ZoneInfo('Asia/Dhaka')   # explicit timezone, never the server default

_BN_DIGITS = str.maketrans('০১২৩৪৫৬৭৮৯', '0123456789')

# Whole-token matches (so "সকাল" is never mistaken for "কাল")
TODAY = {'আজ', 'আজকে', 'today'}
TOMORROW = {'কাল', 'কালকে', 'আগামীকাল', 'আগামীকালকে', 'tomorrow'}
DAY_AFTER = {'পরশু', 'পরশুদিন'}

# Prefix matches, so "সকালে", "রাতে", "সন্ধ্যায়" also work
PERIODS = [
    ('সকাল', 'morning'), ('ভোর', 'morning'), ('morning', 'morning'),
    ('দুপুর', 'noon'),
    ('বিকাল', 'afternoon'), ('বিকেল', 'afternoon'), ('afternoon', 'afternoon'),
    ('সন্ধ্যা', 'evening'), ('evening', 'evening'),
    ('রাত', 'night'), ('night', 'night'),
]

# A number only counts as a time if it has a marker (টা / am / pm), minutes (9:30) or সাড়ে
_TIME_RE = re.compile(
    r'(?P<half>সাড়ে\s*)?(?P<h>\d{1,2})(?:[:.](?P<min>\d{2}))?'
    r'\s*(?P<mark>টা|a\.m\.|p\.m\.|(?:am|pm)(?![a-z]))?'
)

MESSAGES = {
    'missing_time': ('কোন সময়ে মনে করিয়ে দেব?',
                     'What time should I remind you?'),
    'need_period': ('সকাল, দুপুর, বিকাল নাকি রাত?',
                    'Is that morning, afternoon or night?'),
    'unclear_time': ('সময়টা বুঝতে পারিনি। আবার বলুন, যেমন সকাল ৯টা বা রাত ৯টা।',
                     "I couldn't pin down the time. Please say it again, for example 9 in the morning or 9 at night."),
    'invalid_time': ('এই সময়টা সঠিক নয়।', 'That is not a valid time.'),
    'past': ('এই সময় পার হয়ে গেছে। অন্য সময় বলুন।',
             'That time has already passed. Please give another time.'),
    'past_today': ('আজকের এই সময় পার হয়ে গেছে। কাল কি?',
                   'That time has passed today. Did you mean tomorrow?'),
}


def _clarify(code):
    bn, en = MESSAGES[code]
    return {'status': 'clarify', 'code': code, 'question_bn': bn, 'question_en': en}


def _resolve_hour(h, period, mark):
    """Return (hour_24, None) or (None, error_code)."""
    if h > 23 or h == 0:
        return None, 'invalid_time'
    if mark in ('am', 'pm'):
        if h > 12:
            return None, 'invalid_time'
        if mark == 'am':
            return (0 if h == 12 else h), None
        return (12 if h == 12 else h + 12), None
    if h > 12:                      # 13-23 is already 24-hour time
        return h, None
    if period is None:
        return None, 'need_period'
    if period == 'morning' and h <= 11:
        return h, None
    if period == 'noon':
        if h in (11, 12):
            return h, None
        if 1 <= h <= 3:
            return h + 12, None
    if period == 'afternoon':
        if h == 12:
            return 12, None
        if 1 <= h <= 6:
            return h + 12, None
    if period == 'evening' and 4 <= h <= 11:
        return h + 12, None
    if period == 'night' and 6 <= h <= 11:   # 12 and 1-5 are ambiguous (midnight)
        return h + 12, None
    return None, 'unclear_time'


def parse_datetime(text, now=None):
    """
    Returns one of:
      {'status': 'ok', 'remind_at': <aware datetime>, 'assumed_today': bool}
      {'status': 'clarify', 'code': ..., 'question_bn': ..., 'question_en': ...}
    """
    now = now.astimezone(TZ) if now else datetime.now(TZ)
    t = unicodedata.normalize('NFC', text or '').translate(_BN_DIGITS).lower()
    tokens = re.findall(r'[ঀ-৿a-z0-9:.]+', t)

    day_offset = None
    for tok in tokens:
        if tok in TODAY:
            day_offset = 0
        elif tok in TOMORROW:
            day_offset = 1
        elif tok in DAY_AFTER:
            day_offset = 2
        if day_offset is not None:
            break

    period = None
    for tok in tokens:
        for prefix, name in PERIODS:
            if tok.startswith(prefix):
                period = name
                break
        if period:
            break

    match = None
    for cand in _TIME_RE.finditer(t):
        if cand.group('half') or cand.group('min') or cand.group('mark'):
            match = cand
            break
    if match is None:
        return _clarify('missing_time')

    mark = match.group('mark')
    mark = mark.replace('.', '') if mark in ('a.m.', 'p.m.') else mark
    minute = 30 if match.group('half') else int(match.group('min') or 0)
    if minute > 59:
        return _clarify('invalid_time')

    hour, err = _resolve_hour(int(match.group('h')), period, mark)
    if err:
        return _clarify(err)

    offset = day_offset if day_offset is not None else 0
    day = (now + timedelta(days=offset)).date()
    remind_at = datetime.combine(day, time(hour, minute), tzinfo=TZ)

    if remind_at <= now:
        return _clarify('past_today' if day_offset is None else 'past')

    return {'status': 'ok', 'remind_at': remind_at,
            'assumed_today': day_offset is None}