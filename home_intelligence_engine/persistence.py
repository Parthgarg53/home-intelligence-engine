"""SQLite persistence for intents and their lifecycle transitions."""

import sqlite3
from datetime import datetime
from pathlib import Path

from home_intelligence_engine.intent import Intent, IntentStatus


class SQLiteIntentRepository:
    """Store intent snapshots and append-only status transition records."""

    SCHEMA_VERSION = 1

    def __init__(self, database_path: str | Path):
        self.database_path = str(database_path)
        self._initialize()

    def _connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(self.database_path)
        connection.row_factory = sqlite3.Row
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
                    PRAGMA user_version = 1;
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
        )

    def add(self, intent: Intent) -> None:
        with self._connect() as connection:
            connection.execute(
                """INSERT INTO intents
                   (id, description, kind, status, created_at, expires_at)
                   VALUES (?, ?, ?, ?, ?, ?)""",
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
                   created_at = ?, expires_at = ? WHERE id = ? AND status = ?""",
                (
                    intent.status.value,
                    intent.description,
                    intent.kind.value,
                    intent.created_at.isoformat(),
                    intent.expires_at.isoformat() if intent.expires_at else None,
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
