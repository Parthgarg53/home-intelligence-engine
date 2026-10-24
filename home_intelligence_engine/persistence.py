"""SQLite persistence for intents and their lifecycle transitions."""

import sqlite3
from datetime import datetime
from pathlib import Path

from home_intelligence_engine.intent import Condition, Intent, IntentStatus


class SQLiteIntentRepository:
    """Store intent snapshots and append-only status transition records."""

    SCHEMA_VERSION = 4

    def __init__(self, database_path: str | Path):
        self.database_path = str(database_path)
        self._initialize()

    def _connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(self.database_path)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA foreign_keys = ON")
        return connection

    def _initialize(self) -> None:
        with self._connect() as connection:
            version = connection.execute("PRAGMA user_version").fetchone()[0]
            if version > self.SCHEMA_VERSION:
                raise RuntimeError(
                    f"Database schema version {version} is newer than supported "
                    f"version {self.SCHEMA_VERSION}"
                )
            if version == 0:
                connection.executescript(
                    """
                    CREATE TABLE intents (
                        id TEXT PRIMARY KEY,
                        description TEXT NOT NULL,
                        kind TEXT NOT NULL,
                        status TEXT NOT NULL,
                        created_at TEXT NOT NULL,
                        expires_at TEXT
                    );
                    CREATE TABLE intent_transitions (
                        id INTEGER PRIMARY KEY AUTOINCREMENT,
                        intent_id TEXT NOT NULL REFERENCES intents(id),
                        from_status TEXT NOT NULL,
                        to_status TEXT NOT NULL,
                        changed_at TEXT NOT NULL
                    );
                    CREATE INDEX idx_intents_status ON intents(status);
                    """
                )
                version = 1
            if version == 1:
                connection.executescript(
                    """
                    CREATE TABLE execution_records (
                        id INTEGER PRIMARY KEY AUTOINCREMENT,
                        intent_id TEXT NOT NULL REFERENCES intents(id),
                        device_id TEXT NOT NULL,
                        action TEXT NOT NULL,
                        status TEXT NOT NULL,
                        requested_at TEXT NOT NULL,
                        finished_at TEXT,
                        result TEXT
                    );
                    CREATE INDEX idx_executions_intent
                        ON execution_records(intent_id, id);
                    PRAGMA user_version = 2;
                    """
                )
                version = 2
            if version == 2:
                connection.executescript(
                    """
                    ALTER TABLE intents ADD COLUMN condition_json TEXT;
                    ALTER TABLE intents ADD COLUMN target_device_id TEXT;
                    ALTER TABLE intents ADD COLUMN action TEXT;
                    PRAGMA user_version = 3;
                    """
                )
                version = 3
            if version == 3:
                connection.executescript(
                    """
                    CREATE TABLE automation_condition_state (
                        intent_id TEXT PRIMARY KEY REFERENCES intents(id),
                        matched INTEGER NOT NULL,
                        updated_at TEXT NOT NULL
                    );
                    PRAGMA user_version = 4;
                    """
                )

    @staticmethod
    def _values(intent: Intent) -> tuple:
        return (
            intent.id,
            intent.description,
            intent.kind.value,
            intent.status.value,
            intent.created_at.isoformat(),
            intent.expires_at.isoformat() if intent.expires_at else None,
            intent.condition.model_dump_json() if intent.condition else None,
            intent.target_device_id,
            intent.action,
        )

    @staticmethod
    def _from_row(row: sqlite3.Row) -> Intent:
        return Intent(
            id=row["id"],
            description=row["description"],
            kind=row["kind"],
            status=row["status"],
            created_at=datetime.fromisoformat(row["created_at"]),
            expires_at=(
                datetime.fromisoformat(row["expires_at"])
                if row["expires_at"]
                else None
            ),
            condition=(
                Condition.model_validate_json(row["condition_json"])
                if row["condition_json"]
                else None
            ),
            target_device_id=row["target_device_id"],
            action=row["action"],
        )

    def add(self, intent: Intent) -> None:
        with self._connect() as connection:
            connection.execute(
                """INSERT INTO intents
                   (id, description, kind, status, created_at, expires_at,
                    condition_json, target_device_id, action)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                self._values(intent),
            )

    def get(self, intent_id: str) -> Intent | None:
        with self._connect() as connection:
            row = connection.execute(
                "SELECT * FROM intents WHERE id = ?", (intent_id,)
            ).fetchone()
        return self._from_row(row) if row else None

    def list_active(self) -> list[Intent]:
        """Recover persisted active intents for runtime processing."""
        with self._connect() as connection:
            rows = connection.execute(
                "SELECT * FROM intents WHERE status = ? ORDER BY created_at, id",
                (IntentStatus.ACTIVE.value,),
            ).fetchall()
        return [self._from_row(row) for row in rows]

    def expire_due(self, now: datetime) -> list[str]:
        """Persist expiry transitions for every active intent past its deadline."""
        if now.utcoffset() is None:
            raise ValueError("now must include a timezone")
        expired_ids: list[str] = []
        for intent in self.list_active():
            previous_status = intent.status
            intent.refresh_expiry(now)
            if intent.status == IntentStatus.EXPIRED:
                self.save_transition(intent, previous_status, now)
                expired_ids.append(intent.id)
                if intent.kind.value == "temporary_override" and intent.target_device_id:
                    self.rearm_automations_for_device(intent.target_device_id, now)
        return expired_ids

    def rearm_automations_for_device(self, device_id: str, now: datetime) -> None:
        """Allow a still-true automation to restore its policy after an override."""
        if now.utcoffset() is None:
            raise ValueError("now must include a timezone")
        with self._connect() as connection:
            connection.execute(
                """UPDATE automation_condition_state SET matched = 0, updated_at = ?
                   WHERE intent_id IN (
                       SELECT id FROM intents
                       WHERE kind = 'persistent_automation'
                         AND status = 'active' AND target_device_id = ?
                   )""",
                (now.isoformat(), device_id),
            )

    def save_transition(
        self,
        intent: Intent,
        from_status: IntentStatus,
        changed_at: datetime,
    ) -> None:
        """Atomically persist a changed snapshot and its status history."""
        if from_status == intent.status:
            raise ValueError("A transition must change the intent status")
        if changed_at.utcoffset() is None:
            raise ValueError("changed_at must include a timezone")

        with self._connect() as connection:
            cursor = connection.execute(
                """UPDATE intents SET status = ?, description = ?, kind = ?,
                   created_at = ?, expires_at = ?, condition_json = ?,
                   target_device_id = ?, action = ? WHERE id = ? AND status = ?""",
                (
                    intent.status.value,
                    intent.description,
                    intent.kind.value,
                    intent.created_at.isoformat(),
                    intent.expires_at.isoformat() if intent.expires_at else None,
                    intent.condition.model_dump_json() if intent.condition else None,
                    intent.target_device_id,
                    intent.action,
                    intent.id,
                    from_status.value,
                ),
            )
            if cursor.rowcount != 1:
                raise ValueError(
                    f"Intent {intent.id} is missing or no longer has status "
                    f"{from_status.value}"
                )
            connection.execute(
                """INSERT INTO intent_transitions
                   (intent_id, from_status, to_status, changed_at)
                   VALUES (?, ?, ?, ?)""",
                (
                    intent.id,
                    from_status.value,
                    intent.status.value,
                    changed_at.isoformat(),
                ),
            )

    def list_transitions(self, intent_id: str) -> list[dict[str, str]]:
        with self._connect() as connection:
            rows = connection.execute(
                """SELECT from_status, to_status, changed_at
                   FROM intent_transitions WHERE intent_id = ? ORDER BY id""",
                (intent_id,),
            ).fetchall()
        return [dict(row) for row in rows]

    def start_execution(
        self, intent_id: str, device_id: str, action: str, requested_at: datetime
    ) -> int:
        if requested_at.utcoffset() is None:
            raise ValueError("requested_at must include a timezone")
        with self._connect() as connection:
            cursor = connection.execute(
                """INSERT INTO execution_records
                   (intent_id, device_id, action, status, requested_at)
                   VALUES (?, ?, ?, 'attempted', ?)""",
                (intent_id, device_id, action, requested_at.isoformat()),
            )
            return int(cursor.lastrowid)

    def finish_execution(
        self, execution_id: int, status: str, result: str, finished_at: datetime
    ) -> None:
        if status not in {"succeeded", "failed"}:
            raise ValueError("Execution status must be 'succeeded' or 'failed'")
        if finished_at.utcoffset() is None:
            raise ValueError("finished_at must include a timezone")
        with self._connect() as connection:
            cursor = connection.execute(
                """UPDATE execution_records SET status = ?, result = ?, finished_at = ?
                   WHERE id = ? AND status = 'attempted'""",
                (status, result, finished_at.isoformat(), execution_id),
            )
            if cursor.rowcount != 1:
                raise ValueError(f"Execution {execution_id} is missing or already finished")

    def list_executions(self, intent_id: str) -> list[dict]:
        with self._connect() as connection:
            rows = connection.execute(
                """SELECT id, intent_id, device_id, action, status,
                          requested_at, finished_at, result
                   FROM execution_records WHERE intent_id = ? ORDER BY id""",
                (intent_id,),
            ).fetchall()
        return [dict(row) for row in rows]

    def get_automation_condition_state(self, intent_id: str) -> bool | None:
        with self._connect() as connection:
            row = connection.execute(
                "SELECT matched FROM automation_condition_state WHERE intent_id = ?",
                (intent_id,),
            ).fetchone()
        return bool(row["matched"]) if row else None

    def set_automation_condition_state(
        self, intent_id: str, matched: bool, updated_at: datetime
    ) -> None:
        if updated_at.utcoffset() is None:
            raise ValueError("updated_at must include a timezone")
        with self._connect() as connection:
            connection.execute(
                """INSERT INTO automation_condition_state (intent_id, matched, updated_at)
                   VALUES (?, ?, ?)
                   ON CONFLICT(intent_id) DO UPDATE SET
                   matched = excluded.matched, updated_at = excluded.updated_at""",
                (intent_id, int(matched), updated_at.isoformat()),
            )

    def start_automation_execution(
        self,
        intent_id: str,
        device_id: str,
        action: str,
        requested_at: datetime,
    ) -> int:
        """Persist the rising-edge consumption and attempt before device I/O."""
        if requested_at.utcoffset() is None:
            raise ValueError("requested_at must include a timezone")
        with self._connect() as connection:
            cursor = connection.execute(
                """INSERT INTO execution_records
                   (intent_id, device_id, action, status, requested_at)
                   VALUES (?, ?, ?, 'attempted', ?)""",
                (intent_id, device_id, action, requested_at.isoformat()),
            )
            connection.execute(
                """INSERT INTO automation_condition_state (intent_id, matched, updated_at)
                   VALUES (?, 1, ?)
                   ON CONFLICT(intent_id) DO UPDATE SET
                   matched = 1, updated_at = excluded.updated_at""",
                (intent_id, requested_at.isoformat()),
            )
            return int(cursor.lastrowid)
