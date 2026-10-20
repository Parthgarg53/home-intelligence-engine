from datetime import datetime, timezone

import pytest

from home_intelligence_engine.intent import Intent, IntentKind, IntentStatus
from home_intelligence_engine.persistence import SQLiteIntentRepository


def make_intent(**overrides):
    values = {
        "id": "intent-1",
        "description": "Turn off the living room light",
        "kind": IntentKind.CONDITIONAL_ONCE,
        "created_at": datetime(2030, 1, 1, tzinfo=timezone.utc),
        "expires_at": datetime(2030, 1, 2, tzinfo=timezone.utc),
    }
    values.update(overrides)
    return Intent(**values)


def test_intent_round_trips_and_active_intents_recover(tmp_path):
    repository = SQLiteIntentRepository(tmp_path / "intents.sqlite3")
    original = make_intent()

    repository.add(original)
    loaded = repository.get(original.id)

    assert loaded == original
    assert repository.list_active() == [original]


def test_transition_and_history_persist_atomically(tmp_path):
    database = tmp_path / "intents.sqlite3"
    repository = SQLiteIntentRepository(database)
    intent = make_intent()
    repository.add(intent)
    intent.complete(now=datetime(2030, 1, 1, 12, tzinfo=timezone.utc))
    repository.save_transition(
        intent,
        from_status=IntentStatus.ACTIVE,
        changed_at=datetime(2030, 1, 1, 12, tzinfo=timezone.utc),
    )

    reopened = SQLiteIntentRepository(database)
    assert reopened.get(intent.id).status == IntentStatus.COMPLETED
    assert reopened.list_active() == []
    assert reopened.list_transitions(intent.id) == [
        {
            "from_status": "active",
            "to_status": "completed",
            "changed_at": "2030-01-01T12:00:00+00:00",
        }
    ]


def test_stale_transition_does_not_overwrite_persisted_state(tmp_path):
    repository = SQLiteIntentRepository(tmp_path / "intents.sqlite3")
    intent = make_intent()
    repository.add(intent)
    intent.cancel(now=datetime(2030, 1, 1, 12, tzinfo=timezone.utc))
    repository.save_transition(intent, IntentStatus.ACTIVE, datetime.now(timezone.utc))

    stale_copy = make_intent()
    stale_copy.complete(now=datetime(2030, 1, 1, 12, tzinfo=timezone.utc))
    with pytest.raises(ValueError, match="no longer has status active"):
        repository.save_transition(
            stale_copy, IntentStatus.ACTIVE, datetime.now(timezone.utc)
        )

    assert repository.get(intent.id).status == IntentStatus.CANCELLED
    assert len(repository.list_transitions(intent.id)) == 1
