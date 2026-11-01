from datetime import datetime
import pytest
from app.services.bangla_datetime import TZ
from app.services.intent import interpret

NOW = datetime(2026, 10, 29, 10, 0, tzinfo=TZ)   # Thursday 10:00 Dhaka


def at(day, hour, minute=0):
    return datetime(2026, 10, day, hour, minute, tzinfo=TZ)


REMINDERS = [
    ('কাল সকাল ৯টায় ওষুধ খেতে মনে করিয়ে দিও', 'ওষুধ খেতে', at(30, 9)),
    ('আজ রাত ৯টায় ওষুধ খেতে মনে করিয়ে দাও।', 'ওষুধ খেতে', at(29, 21)),
    ('remind me to take medicine tomorrow at 9 pm', 'take medicine', at(30, 21)),
    ('Remind me tomorrow 9:30 am to call the doctor', 'call the doctor', at(30, 9, 30)),
    ('কাল সাড়ে ৮টায় সকাল ডাক্তারের কাছে যেতে মনে করিয়ে দিও', 'ডাক্তারের কাছে যেতে', at(30, 8, 30)),
    ('remind me to take 2 tablets at 9 pm today', 'take 2 tablets', at(29, 21)),
    ('কাল সকালে মাকে ফোন করতে মনে করিয়ে দিও ৯টায়', 'মাকে ফোন করতে', at(30, 9)),
    ('আজ রাত ৯টায় ওষুধ খেতে মনে করিয়ে দিও', 'ওষুধ খেতে', at(29, 21)),
    ('রিমাইন্ডার সেট করো কাল সকাল ১০টায় বাজার', 'বাজার', at(30, 10)),
    ('কাল রাতের খাবার খেতে মনে করিয়ে দিও ৮টায়', 'রাতের খাবার খেতে', at(30, 20)),
]


@pytest.mark.parametrize('text,task,when', REMINDERS)
def test_reminder_ok(text, task, when):
    r = interpret(text, now=NOW)
    assert r['intent'] == 'reminder' and r['status'] == 'ok', r
    assert r['task'] == task
    assert r['remind_at'] == when


def test_reminder_missing_time_keeps_task():
    r = interpret('ওষুধ খেতে মনে করিয়ে দিও', now=NOW)
    assert (r['intent'], r['status'], r['code']) == ('reminder', 'clarify', 'missing_time')
    assert r['task'] == 'ওষুধ খেতে'


def test_reminder_ambiguous_period_asks():
    r = interpret('৯টায় ওষুধ খেতে মনে করিয়ে দিও', now=NOW)
    assert r['status'] == 'clarify' and r['code'] == 'need_period'


def test_reminder_missing_task_asks():
    r = interpret('কাল সকাল ৯টায় মনে করিয়ে দিও', now=NOW)
    assert (r['intent'], r['status'], r['code']) == ('reminder', 'clarify', 'missing_task')


@pytest.mark.parametrize('text,intent', [
    ('মেসেজ পড়ে শোনাও', 'read_message'),
    ('এই মেসেজটা পড়ে শোনাও', 'read_message'),
    ('Read this message', 'read_message'),
    ('আবার বলো', 'repeat'),
    ('আবার বলুন', 'repeat'),
    ('repeat', 'repeat'),
    ('say that again', 'repeat'),
    ('আস্তে বলো', 'slow_down'),
    ('slow down', 'slow_down'),
    ('থামো', 'stop'),
    ('stop', 'stop'),
    ('স্টপ', 'stop'),
    ('বন্ধ করো', 'stop'),
])
def test_simple_intents(text, intent):
    r = interpret(text, now=NOW)
    assert r['intent'] == intent and r['status'] == 'ok', r


@pytest.mark.parametrize('text,target', [
    ('আম্মুকে ফোন করো', 'আম্মুকে'),
    ('call mom', 'mom'),
    ('বন্ধুকে ফোন করো', 'বন্ধুকে'),   # 'বন্ধু' must not be read as the word stop
])
def test_call(text, target):
    r = interpret(text, now=NOW)
    assert r['intent'] == 'call' and r['status'] == 'ok', r
    assert r['target'] == target


@pytest.mark.parametrize('text,code', [
    ('', 'no_speech'),
    ('   ', 'no_speech'),
    (None, 'no_speech'),
    ('আজ আবহাওয়া কেমন', 'unknown_command'),
    ('hello there', 'unknown_command'),
    ('মা ফোন', 'unknown_command'),
    ('আম্মুকে ফোন করো এবং মেসেজ পড়ে শোনাও', 'ambiguous_command'),
    ('ক' * 400, 'too_long'),
])
def test_never_guesses(text, code):
    r = interpret(text, now=NOW)
    assert r['intent'] == 'unknown' and r['status'] == 'clarify'
    assert r['code'] == code
    assert r['question_bn'] and r['question_en']


def test_reminder_wins_over_stop_word():
    r = interpret('remind me to stop the car at 9 pm today', now=NOW)
    assert r['intent'] == 'reminder' and r['task'] == 'stop the car'