"""Stage 10: voice-command logic (Eleen, backend).

One entry point, handle_voice_command(), runs the plan's pipeline:
    text -> interpret -> clarify -> propose (server-side pending action) -> confirm -> execute
Nothing is saved until the SERVER receives a clear "yes" for a pending action it stored itself.
Pure logic (no Flask), so it is easy to test. Returns (payload_dict, http_status).
"""
import unicodedata
from datetime import datetime, timezone

from app.services.bangla_datetime import TZ, parse_datetime
from app.services.confirmation import classify_reply
from app.services.contact_service import match_contact
from app.services.intent import interpret, normalize, tokenize
from app.services.message_service import describe_message
from app.services.reminder_service import MAX_TITLE_LENGTH, execute_confirmed

UTC = timezone.utc
CONTROLS = ('repeat', 'slow_down', 'stop')           # the browser handles these itself
_LEADING_NO = {'না', 'নাহ', 'ভুল', 'no', 'nope', 'wrong'}

_BN_DIGITS = str.maketrans('0123456789', '০১২৩৪৫৬৭৮৯')
MONTHS_BN = ['জানুয়ারি', 'ফেব্রুয়ারি', 'মার্চ', 'এপ্রিল', 'মে', 'জুন', 'জুলাই', 'আগস্ট',
             'সেপ্টেম্বর', 'অক্টোবর', 'নভেম্বর', 'ডিসেম্বর']
MONTHS_EN = ['January', 'February', 'March', 'April', 'May', 'June', 'July', 'August',
             'September', 'October', 'November', 'December']

MESSAGES = {
    'done': ('ঠিক আছে, রিমাইন্ডার সেট করা হয়েছে।', 'Done. Your reminder is set.'),
    'unsupported': ('এই সুবিধাটা এখনো চালু হয়নি।', 'That feature is not available yet.'),
    'too_long': ('কাজের কথাটা অনেক লম্বা। ছোট করে বলুন।', 'That task is too long. Please say it shorter.'),
    'past': ('এই সময় পার হয়ে গেছে। আবার বলুন।', 'That time has already passed. Please say it again.'),
    'duplicate_reminder': ('এই রিমাইন্ডার আপনার আগে থেকেই আছে।', 'You already have this reminder.'),
    'db_error': ('সেভ করা যায়নি। কিছুই সেভ হয়নি। আবার চেষ্টা করুন।',
                 'Could not save the reminder. Nothing was saved. Please try again.'),
    'invalid': ('রিমাইন্ডারটা সঠিক নয়। আবার বলুন।', 'That reminder was not valid. Please say it again.'),
    'error': ('কিছু একটা ভুল হয়েছে। আবার বলুন।', 'Something went wrong. Please say it again.'),
    'silent': ('', ''),
    'dialer': ('ডায়ালার খুলছি। কল করতে সবুজ বোতাম চাপুন।',
               'Opening the dialer. Press the call button to start the call.'),
    'missing_contact': ('কাকে ফোন করব?', 'Who should I call?'),
    'contact_not_found': ('এই নামে কাউকে পাইনি। নামটা আবার বলুন।',
                          "I couldn't find that name in your contacts. Please say the name again."),
    'contact_gone': ('এই নম্বরটা আর আপনার তালিকায় নেই।', 'That contact is no longer in your list.'),
}


# ------------------------------------------------------------ input cleaning
def clean_text(value):
    """Control characters become spaces. Bangla joiners (ZWJ/ZWNJ) are kept on purpose."""
    return ''.join(' ' if unicodedata.category(ch) == 'Cc' else ch for ch in value).strip()


def clean_task(value):
    """Same cleaning the intent parser applies to a spoken task: words and digits only,
    so <, >, quotes and slashes never reach the database."""
    return ' '.join(tokenize(normalize(clean_text(value))))


def _strip_leading_no(text):
    tokens = tokenize(normalize(text))
    while tokens and tokens[0] in _LEADING_NO:
        tokens.pop(0)
    return ' '.join(tokens)


# ------------------------------------------------------------- read-back text
def _bn(number):
    return str(number).translate(_BN_DIGITS)


def _period_bn(hour):
    if hour < 4:
        return 'রাত'
    if hour < 6:
        return 'ভোর'
    if hour < 12:
        return 'সকাল'
    if hour < 16:
        return 'দুপুর'
    if hour < 18:
        return 'বিকাল'
    if hour < 20:
        return 'সন্ধ্যা'
    return 'রাত'


def describe_time(remind_at, now):
    """Return (bangla, english) wording of a reminder time, e.g. 'কাল রাত ৯টা' / 'tomorrow at 9:00 PM'."""
    local = remind_at.astimezone(TZ)
    days = (local.date() - now.astimezone(TZ).date()).days
    if days == 0:
        day_bn, day_en = 'আজ', 'today'
    elif days == 1:
        day_bn, day_en = 'কাল', 'tomorrow'
    else:
        day_bn = f'{_bn(local.day)} {MONTHS_BN[local.month - 1]}'
        day_en = f'{MONTHS_EN[local.month - 1]} {local.day}'
    h12 = local.hour % 12 or 12
    minutes_bn = f' {_bn(local.minute)} মিনিট' if local.minute else ''
    time_bn = f'{_period_bn(local.hour)} {_bn(h12)}টা{minutes_bn}'
    time_en = f'{h12}:{local.minute:02d} {"AM" if local.hour < 12 else "PM"}'
    return f'{day_bn} {time_bn}', f'{day_en} at {time_en}'


# ----------------------------------------------------------------- responses
def _say(status, pair, **extra):
    bn, en = pair
    return {'status': status, 'speak_bn': bn, 'speak_en': en, **extra}


def _clarify_payload(parsed, task=None):
    out = _say('clarify', (parsed['question_bn'], parsed['question_en']),
               intent=parsed['intent'], code=parsed['code'])
    task = task or parsed.get('task')
    if parsed['intent'] == 'reminder' and task:
        out['task'] = task          # the browser sends this back with the answer
    return out


def _propose_reminder(confirmations, user_id, task, remind_at, assumed_today, now, replaces=None):
    if len(task) > MAX_TITLE_LENGTH:
        return _say('clarify', MESSAGES['too_long'], intent='reminder', code='too_long'), 200
    when_bn, when_en = describe_time(remind_at, now)
    proposed = confirmations.propose(
        user_id, 'reminder', {'task': task, 'remind_at': remind_at.isoformat()},
        f'{when_bn}, "{task}" মনে করিয়ে দেব। ঠিক আছে?',
        f'I will remind you to "{task}" {when_en}. Is that correct?',
        now=now, replaces=replaces)
    return _say('confirm', (proposed['summary_bn'], proposed['summary_en']),
                intent='reminder', pending_id=proposed['pending_id'],
                reminder={'task': task, 'remind_at': remind_at.isoformat()},
                assumed_today=assumed_today,
                expires_in_seconds=proposed['expires_in_seconds']), 200


def _execute(confirmations, user_id, released, now, directory=None):
    if released.get('type') == 'call':
        return _execute_call(confirmations, directory, user_id, released)
    outcome = execute_confirmed(confirmations, user_id, released, now=now)
    if outcome['result'] == 'created':
        return _say('done', MESSAGES['done'], intent='reminder', reminder=outcome['reminder']), 200
    code = outcome['result']
    http = 503 if code == 'db_error' else 200
    return _say('failed', MESSAGES.get(code, MESSAGES['error']), code=code), http


def _from_confirmation(confirmations, user_id, released, now, directory=None):
    result = released['result']
    if result == 'execute':
        return _execute(confirmations, user_id, released, now, directory)
    pair = (released.get('question_bn') or MESSAGES['error'][0],
            released.get('question_en') or MESSAGES['error'][1])
    if result == 'cancelled':
        return _say('cancelled', pair, pending_id=released.get('pending_id')), 200
    if result == 'unclear':          # still waiting; read the question and the details again
        bn = f"{pair[0]} {released.get('summary_bn', '')}".strip()
        en = f"{pair[1]} {released.get('summary_en', '')}".strip()
        return _say('waiting', (bn, en), code='unclear', pending_id=released['pending_id']), 200
    return _say('failed', pair, code=result), 200      # expired, not_found, duplicate, too_late, error


# ------------------------------------------------------------ calls and messages
def _ask_contact(code, pair, choices=None):
    """A question whose answer is a NAME. 'awaiting' tells the browser to send it back."""
    out = _say('clarify', pair, intent='call', code=code, awaiting='contact')
    if choices:
        out['choices'] = choices
    return out, 200


def _propose_call(confirmations, directory, user_id, target, now, replaces=None):
    """Find the contact and READ IT BACK. Nothing is dialled here, or anywhere on the server."""
    if not target:
        return _ask_contact('missing_contact', MESSAGES['missing_contact'])
    found = match_contact(directory.contacts(user_id), target)
    if found['result'] == 'none':
        return _ask_contact('contact_not_found', MESSAGES['contact_not_found'])
    if found['result'] == 'many':                 # never pick silently between similar names
        names = [c['name'] for c in found['matches']]
        return _ask_contact('ambiguous_contact',
                            (f"কাকে ফোন করব? {', নাকি '.join(names)}?",
                             f"Who should I call? {', or '.join(names)}?"), choices=names)
    contact = found['matches'][0]
    name = contact['name']
    proposed = confirmations.propose(
        user_id, 'call', {'contact_id': str(contact['_id']), 'name': name},
        f'"{name}"-কে ফোন করার জন্য ডায়ালার খুলব। ঠিক আছে?',
        f'I will open the dialer to call "{name}". Is that correct?',
        now=now, replaces=replaces)
    return _say('confirm', (proposed['summary_bn'], proposed['summary_en']),
                intent='call', pending_id=proposed['pending_id'], contact={'name': name},
                expires_in_seconds=proposed['expires_in_seconds']), 200


def _execute_call(confirmations, directory, user_id, released):
    """After a confirmed "yes": hand the number to the browser, which opens the phone's dialer.
    The USER still presses the call button. The server never places a call."""
    params = released.get('params') or {}
    contact = directory.contact(params.get('contact_id'), user_id) if directory else None
    if contact is None:               # deleted while the question was being asked
        confirmations.fail(user_id, released['pending_id'], 'contact_gone')
        return _say('failed', MESSAGES['contact_gone'], code='contact_gone'), 200
    confirmations.complete(user_id, released['pending_id'])
    return _say('done', MESSAGES['dialer'], intent='call', action='open_dialer',
                contact={'name': contact['name'], 'phone': contact['phone']},
                tel=f"tel:{contact['phone']}"), 200


def _read_next_message(directory, user_id):
    """Speak the user's oldest unread message. The message text is data only: it goes into
    the spoken sentence and is never interpreted as a command (see message_service)."""
    doc = directory.take_next_message(user_id)
    out = _say('done', describe_message(doc), intent='read_message')
    out['message'] = None if doc is None else {'id': str(doc['_id']), 'sender': doc['sender'],
                                               'text': doc['text']}
    return out, 200


# ------------------------------------------------------------------ the flows
def _handle_new_command(confirmations, user_id, text, now, directory=None):
    parsed = interpret(text, now=now)
    if parsed['status'] == 'clarify':
        return _clarify_payload(parsed), 200
    intent = parsed['intent']
    if intent == 'reminder':
        return _propose_reminder(confirmations, user_id, parsed['task'], parsed['remind_at'],
                                 parsed['assumed_today'], now)
    if intent in CONTROLS:
        return _say('control', MESSAGES['silent'], intent=intent, action=intent), 200
    if directory is not None:
        if intent == 'call':
            return _propose_call(confirmations, directory, user_id, parsed.get('target', ''), now)
        if intent == 'read_message':
            return _read_next_message(directory, user_id)
    return _say('unsupported', MESSAGES['unsupported'], intent=intent), 200


def _handle_contact_answer(confirmations, user_id, text, now, directory):
    """The previous reply asked "who should I call?". A full new command still wins;
    anything else is treated as the name."""
    parsed = interpret(text, now=now)
    if parsed['intent'] != 'unknown':
        return _handle_new_command(confirmations, user_id, text, now, directory)
    if parsed['code'] != 'unknown_command':            # silence or far too long
        return _clarify_payload(parsed), 200
    if directory is None:
        return _say('unsupported', MESSAGES['unsupported'], intent='call'), 200
    return _propose_call(confirmations, directory, user_id, clean_task(text), now)


def _handle_time_answer(confirmations, user_id, text, task, now):
    when = parse_datetime(text, now=now)
    if when['status'] == 'clarify':
        return _clarify_payload({'intent': 'reminder', **when}, task=task), 200
    return _propose_reminder(confirmations, user_id, task, when['remind_at'],
                             when['assumed_today'], now)


def _handle_reply(confirmations, user_id, text, pending_id, now, directory=None):
    verdict = classify_reply(text)
    if verdict != 'yes':
        # Maybe the user is correcting the read-back with a complete new command.
        parsed = interpret(_strip_leading_no(text), now=now)   # a long correction can start with "না" even when the reply is 'unclear'
        if parsed['intent'] == 'reminder' and parsed['status'] == 'ok':
            return _propose_reminder(confirmations, user_id, parsed['task'], parsed['remind_at'],
                                     parsed['assumed_today'], now, replaces=pending_id)
        if parsed['intent'] == 'call' and parsed.get('target') and directory is not None:
            # "না, মেয়েকে ফোন করো": the old read-back is void, a new one is read back.
            confirmations.cancel(user_id, pending_id, now=now)
            return _propose_call(confirmations, directory, user_id, parsed['target'], now)
        if verdict == 'unclear':
            if parsed['intent'] == 'reminder':       # incomplete correction: old read-back is void
                confirmations.cancel(user_id, pending_id, now=now)
                return _clarify_payload(parsed), 200
            if parsed['intent'] in ('repeat', 'slow_down'):
                return _say('control', MESSAGES['silent'], intent=parsed['intent'],
                            action=parsed['intent'], pending_id=pending_id), 200
    released = confirmations.reply(user_id, pending_id, text, now=now)
    return _from_confirmation(confirmations, user_id, released, now, directory)


def handle_voice_command(confirmations, user_id, text, pending_id=None, task=None, now=None,
                         awaiting=None, directory=None):
    """directory gives access to the user's contacts and messages (see services/directory.py).
    Without one, call and read-message commands answer "not available yet"."""
    now = now or datetime.now(UTC)
    if pending_id is not None:
        return _handle_reply(confirmations, user_id, text, pending_id, now, directory)
    if task is not None:
        return _handle_time_answer(confirmations, user_id, text, task, now)
    if awaiting == 'contact':
        return _handle_contact_answer(confirmations, user_id, text, now, directory)
    return _handle_new_command(confirmations, user_id, text, now, directory)
