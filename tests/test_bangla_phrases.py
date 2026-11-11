import pytest
from app.services.intent import interpret

# Phrases exactly as the browser's Bangla/English speech recognition heard them.
@pytest.mark.parametrize("text, expected_intent", [
    ("রাত নয়টায় ওষুধ খেতে মনে করিয়ে দিন।", "reminder"),
    ("ছেলেকে ফোন করো", "call"),
    ("ছেলেকে ফোন দিন।", "call"),
    ("আবার বলো", "repeat"),
    ("Remind me to take medicine at nine PM", "reminder"),
])
def test_intent_detected(text, expected_intent):
    assert interpret(text)["intent"] == expected_intent


@pytest.mark.xfail(reason="Browser hears 'আবারও'; the rule only matches 'আবার'")
def test_repeat_with_extra_o():
    assert interpret("আবারও বলো।")["intent"] == "repeat"


@pytest.mark.xfail(reason="Time spoken as a word (নয়টায় / nine) is not parsed, only digits")
@pytest.mark.parametrize("text", [
    "রাত নয়টায় ওষুধ খেতে মনে করিয়ে দিন।",
    "Remind me to take medicine at nine PM",
])
def test_reminder_with_spelled_out_time(text):
    assert interpret(text)["status"] == "ok"