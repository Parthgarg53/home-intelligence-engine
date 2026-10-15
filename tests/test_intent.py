
from datetime import datetime, timedelta, timezone

import pytest

from home_intelligence_engine.intent import Intent, IntentKind, IntentStatus


def make_intent(**overrides):
    values = {
        "id": "intent-1",
        "description": "Turn off the living room light",
        "kind": IntentKind.ONE_TIME,
    }
    values.update(overrides)
    return Intent(**values)


def test_new_intent_starts_active():
    intent = make_intent()

    assert intent.status == IntentStatus.ACTIVE
    assert intent.created_at is not None


def test_intent_kind_is_preserved():
    intent = make_intent(kind=IntentKind.CONDITIONAL_ONCE)

    assert intent.kind == IntentKind.CONDITIONAL_ONCE


def test_active_intent_can_complete():
    intent = make_intent()

    intent.complete()

    assert intent.status == IntentStatus.COMPLETED


def test_active_intent_can_be_cancelled():
    intent = make_intent()

    intent.cancel()

    assert intent.status == IntentStatus.CANCELLED


def test_intent_expires_at_deadline():
    deadline = datetime(2030, 1, 1, tzinfo=timezone.utc)
    intent = make_intent(expires_at=deadline)

    intent.refresh_expiry(now=deadline)

    assert intent.status == IntentStatus.EXPIRED


def test_intent_does_not_expire_before_deadline():
    deadline = datetime(2030, 1, 1, tzinfo=timezone.utc)
    intent = make_intent(expires_at=deadline)

    intent.refresh_expiry(now=deadline - timedelta(seconds=1))

    assert intent.status == IntentStatus.ACTIVE


def test_completed_intent_cannot_be_cancelled():
    intent = make_intent()
    intent.complete()

    with pytest.raises(ValueError):
        intent.cancel()


def test_naive_expiry_timestamp_is_rejected():
    with pytest.raises(ValueError):
        make_intent(expires_at=datetime(2030, 1, 1))
