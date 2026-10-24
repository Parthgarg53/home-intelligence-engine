from datetime import datetime, timezone
import sqlite3

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


def test_existing_schema_version_two_migrates_without_losing_intents(tmp_path):
    database = tmp_path / "intents.sqlite3"
    with sqlite3.connect(database) as connection:
        connection.executescript(
            """
            CREATE TABLE intents (
                id TEXT PRIMARY KEY, description TEXT NOT NULL, kind TEXT NOT NULL,
                status TEXT NOT NULL, created_at TEXT NOT NULL, expires_at TEXT
            );
            CREATE TABLE intent_transitions (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                intent_id TEXT NOT NULL REFERENCES intents(id),
                from_status TEXT NOT NULL, to_status TEXT NOT NULL, changed_at TEXT NOT NULL
            );
            CREATE INDEX idx_intents_status ON intents(status);
            CREATE TABLE execution_records (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                intent_id TEXT NOT NULL REFERENCES intents(id),
                device_id TEXT NOT NULL, action TEXT NOT NULL, status TEXT NOT NULL,
                requested_at TEXT NOT NULL, finished_at TEXT, result TEXT
            );
            CREATE INDEX idx_executions_intent ON execution_records(intent_id, id);
            PRAGMA user_version = 2;
            """
        )
        connection.execute(
            """INSERT INTO intents
               VALUES (?, ?, ?, ?, ?, ?)""",
            (
                "existing-intent", "Keep light active", "one_time", "active",
                "2030-01-01T00:00:00+00:00", None,
            ),
        )

    repository = SQLiteIntentRepository(database)

    assert repository.get("existing-intent").description == "Keep light active"
    with sqlite3.connect(database) as connection:
        assert connection.execute("PRAGMA user_version").fetchone()[0] == 4
