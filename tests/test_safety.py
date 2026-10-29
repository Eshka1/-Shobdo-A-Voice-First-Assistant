import threading
from datetime import datetime, timedelta
import pytest
from app.services.bangla_datetime import TZ
from app.services.confirmation import (
    ConfirmationService, InMemoryPendingStore, MongoPendingStore, classify_reply,
    AWAITING, EXECUTING, COMPLETED, CANCELLED, EXPIRED, ERROR,
)

T0 = datetime(2026, 10, 29, 10, 0, tzinfo=TZ)
ALICE, BOB = 'user-alice', 'user-bob'
PARAMS = {'task': 'ওষুধ খেতে', 'remind_at': '2026-10-30T09:00:00+06:00'}


@pytest.fixture(params=['memory', 'mongo'])
def svc(request):
    if request.param == 'memory':
        store = InMemoryPendingStore()
    else:
        mongomock = pytest.importorskip('mongomock')   # optional: pip install mongomock
        store = MongoPendingStore(mongomock.MongoClient().db.pending_actions)
    return ConfirmationService(store, ttl_seconds=120)


def propose(svc, user=ALICE, params=PARAMS, now=T0, **kw):
    return svc.propose(user, 'reminder', params, 'সারাংশ', 'Summary', now=now, **kw)


# ---- reply words -----------------------------------------------------------
@pytest.mark.parametrize('text,verdict', [
    ('হ্যাঁ', 'yes'), ('হ্যাঁ।', 'yes'), ('জি হ্যাঁ', 'yes'), ('ঠিক আছে', 'yes'),
    ('yes', 'yes'), ('Yes please', 'yes'),
    ('না', 'no'), ('জি না', 'no'), ('বাতিল', 'no'), ('cancel', 'no'),
    ('না, ভুল সময়', 'no'),              # extra words are fine when cancelling
    ('হ্যাঁ না', 'unclear'),              # conflict
    ('ঠিক আছে না', 'unclear'),
    ('yes but at 10', 'unclear'),         # extra words after a yes: do NOT execute
    ('হ্যাঁ কিন্তু রাত ৯টা', 'unclear'),
    ('ঠিক', 'unclear'), ('maybe', 'unclear'), ('', 'unclear'), (None, 'unclear'),
    ('এক দুই তিন চার পাঁচ ছয় সাত হ্যাঁ', 'unclear'),
])
def test_classify_reply(text, verdict):
    assert classify_reply(text) == verdict


# ---- core flow -------------------------------------------------------------
def test_propose_stores_pending_with_expiry(svc):
    p = propose(svc)
    assert p['result'] == 'proposed' and p['expires_in_seconds'] == 120
    assert p['reused'] is False and p['pending_id']


def test_confirm_returns_server_stored_params(svc):
    p = propose(svc)
    r = svc.confirm(ALICE, p['pending_id'], now=T0 + timedelta(seconds=5))
    assert r['result'] == 'execute'
    assert r['type'] == 'reminder' and r['params'] == PARAMS
    assert r['idempotency_key']


def test_yes_reply_executes_no_reply_cancels(svc):
    a = propose(svc)
    assert svc.reply(ALICE, a['pending_id'], 'হ্যাঁ', now=T0)['result'] == 'execute'
    b = propose(svc, params={'task': 'x', 'remind_at': 'y'})
    assert svc.reply(ALICE, b['pending_id'], 'না', now=T0)['result'] == 'cancelled'
    assert svc.confirm(ALICE, b['pending_id'], now=T0)['result'] == 'cancelled'


def test_double_confirm_executes_once(svc):
    p = propose(svc)
    assert svc.confirm(ALICE, p['pending_id'], now=T0)['result'] == 'execute'
    assert svc.confirm(ALICE, p['pending_id'], now=T0)['result'] == 'duplicate'
    assert svc.complete(ALICE, p['pending_id'], now=T0) is True
    assert svc.confirm(ALICE, p['pending_id'], now=T0)['result'] == 'duplicate'


def test_expired_pending_cannot_be_confirmed(svc):
    p = propose(svc)
    late = T0 + timedelta(seconds=121)
    assert svc.confirm(ALICE, p['pending_id'], now=late)['result'] == 'expired'
    assert svc.confirm(ALICE, p['pending_id'], now=T0)['result'] == 'expired'  # stays expired


def test_just_before_expiry_still_works(svc):
    p = propose(svc)
    assert svc.confirm(ALICE, p['pending_id'], now=T0 + timedelta(seconds=119))['result'] == 'execute'


def test_unclear_reply_keeps_waiting_and_executes_nothing(svc):
    p = propose(svc)
    r = svc.reply(ALICE, p['pending_id'], 'হ্যাঁ না', now=T0)
    assert r['result'] == 'unclear' and r['summary_bn'] and r['question_bn']
    assert svc.reply(ALICE, p['pending_id'], 'হ্যাঁ', now=T0)['result'] == 'execute'


def test_unclear_reply_after_expiry_reports_expired(svc):
    p = propose(svc)
    assert svc.reply(ALICE, p['pending_id'], 'hmm', now=T0 + timedelta(seconds=500))['result'] == 'expired'


# ---- security --------------------------------------------------------------
def test_other_user_cannot_confirm_or_cancel(svc):
    p = propose(svc)
    assert svc.confirm(BOB, p['pending_id'], now=T0)['result'] == 'not_found'
    assert svc.cancel(BOB, p['pending_id'], now=T0)['result'] == 'not_found'
    assert svc.confirm(ALICE, p['pending_id'], now=T0)['result'] == 'execute'   # untouched


def test_made_up_id_is_not_found(svc):
    assert svc.confirm(ALICE, 'made-up-by-browser', now=T0)['result'] == 'not_found'


@pytest.mark.parametrize('bad', [{'$ne': None}, ['a'], 123, None, ''])
def test_non_string_ids_never_reach_the_database(svc, bad):
    assert svc.confirm(ALICE, bad, now=T0)['result'] == 'not_found'
    assert svc.confirm(bad, 'x', now=T0)['result'] == 'not_found'
    assert svc.cancel(ALICE, bad, now=T0)['result'] == 'not_found'
    assert svc.reply(ALICE, bad, 'হ্যাঁ', now=T0)['result'] == 'not_found'


def test_cancel_after_execution_started_is_too_late(svc):
    p = propose(svc)
    svc.confirm(ALICE, p['pending_id'], now=T0)
    assert svc.cancel(ALICE, p['pending_id'], now=T0)['result'] == 'too_late'


# ---- duplicates, corrections, executor feedback ----------------------------
def test_same_proposal_twice_reuses_the_pending_action(svc):
    a, b = propose(svc), propose(svc, now=T0 + timedelta(seconds=3))
    assert a['pending_id'] == b['pending_id'] and b['reused'] is True


def test_different_users_get_different_pending_actions(svc):
    assert propose(svc)['pending_id'] != propose(svc, user=BOB)['pending_id']


def test_correction_replaces_the_old_proposal(svc):
    old = propose(svc)
    new = propose(svc, params={'task': 'ওষুধ খেতে', 'remind_at': '2026-10-30T21:00:00+06:00'},
                  replaces=old['pending_id'])
    assert svc.confirm(ALICE, old['pending_id'], now=T0)['result'] == 'cancelled'
    assert svc.confirm(ALICE, new['pending_id'], now=T0)['result'] == 'execute'


def test_complete_requires_executing_state(svc):
    p = propose(svc)
    assert svc.complete(ALICE, p['pending_id'], now=T0) is False


def test_executor_failure_is_recorded(svc):
    p = propose(svc)
    svc.confirm(ALICE, p['pending_id'], now=T0)
    assert svc.fail(ALICE, p['pending_id'], 'db_error', now=T0) is True
    assert svc.confirm(ALICE, p['pending_id'], now=T0)['result'] == 'error'


def test_invalid_proposal_input_is_rejected(svc):
    with pytest.raises(ValueError):
        svc.propose('', 'reminder', {}, 'a', 'b', now=T0)
    with pytest.raises(ValueError):
        svc.propose(ALICE, 'reminder', 'not-a-dict', 'a', 'b', now=T0)


def test_naive_clock_is_rejected(svc):
    with pytest.raises(ValueError):
        svc.propose(ALICE, 'reminder', {}, 'a', 'b', now=datetime(2026, 10, 29, 10, 0))


def test_many_simultaneous_confirms_execute_exactly_once():
    svc = ConfirmationService(InMemoryPendingStore())
    p = propose(svc)
    results = []

    def worker():
        results.append(svc.confirm(ALICE, p['pending_id'], now=T0)['result'])

    threads = [threading.Thread(target=worker) for _ in range(30)]
    [t.start() for t in threads]
    [t.join() for t in threads]
    assert results.count('execute') == 1
    assert results.count('duplicate') == 29