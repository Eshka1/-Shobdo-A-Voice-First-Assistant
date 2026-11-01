from datetime import datetime
import pytest
from app.services.bangla_datetime import parse_datetime, TZ

NOW = datetime(2026, 10, 29, 10, 0, tzinfo=TZ)   # Thursday 10:00 Dhaka


def at(day, hour, minute=0):
    return datetime(2026, 10, day, hour, minute, tzinfo=TZ)


OK_CASES = [
    ('কাল সকাল ৯টায় ওষুধ খেতে মনে করিয়ে দিও', at(30, 9)),
    ('আজ রাত ৯টায় ওষুধ', at(29, 21)),
    ('remind me tomorrow at 9 pm', at(30, 21)),
    ('tomorrow 9:30 am', at(30, 9, 30)),
    ('কাল সাড়ে ৮টায় সকাল', at(30, 8, 30)),
    ('আজ বিকাল ৫টায়', at(29, 17)),
    ('আগামীকাল দুপুর ২টায়', at(30, 14)),
    ('পরশু দুপুর ২টায়', at(31, 14)),
    ('সকাল ১১টায় ওষুধ', at(29, 11)),   # proves "সকাল" is not read as "কাল"
]

CLARIFY_CASES = [
    ('৯টায় ওষুধ', 'need_period'),
    ('কাল ওষুধ খেতে হবে', 'missing_time'),
    ('আজ সকাল ৯টা', 'past'),
    ('সকাল ৯টায়', 'past_today'),
    ('কাল রাত ১২টায়', 'unclear_time'),
    ('কাল ২৫টায়', 'invalid_time'),
    ('কাল সকাল ৯:৬০', 'invalid_time'),
]


@pytest.mark.parametrize('text,expected', OK_CASES)
def test_ok(text, expected):
    result = parse_datetime(text, now=NOW)
    assert result['status'] == 'ok', result
    assert result['remind_at'] == expected


@pytest.mark.parametrize('text,code', CLARIFY_CASES)
def test_clarify(text, code):
    result = parse_datetime(text, now=NOW)
    assert result['status'] == 'clarify'
    assert result['code'] == code
    assert result['question_bn'] and result['question_en']


def test_assumed_today_flag():
    assert parse_datetime('সকাল ১১টায়', now=NOW)['assumed_today'] is True
    assert parse_datetime('আজ সকাল ১১টায়', now=NOW)['assumed_today'] is False