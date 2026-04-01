from __future__ import annotations

import sqlite3
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterator

from edraft.models import StateRecord


TERMINAL_ACTIONS = {"skipped", "drafted"}


class StateStore:
    def __init__(self, database_path: Path) -> None:
        self.database_path = database_path
        self.database_path.parent.mkdir(parents=True, exist_ok=True)
        self._initialize()

    @contextmanager
    def _connect(self) -> Iterator[sqlite3.Connection]:
        connection = sqlite3.connect(self.database_path)
        connection.row_factory = sqlite3.Row
        try:
            yield connection
            connection.commit()
        finally:
            connection.close()

    def _initialize(self) -> None:
        with self._connect() as connection:
            connection.execute(
                """
                CREATE TABLE IF NOT EXISTS message_actions (
                    source_message_id TEXT PRIMARY KEY,
                    conversation_id TEXT,
                    subject TEXT NOT NULL,
                    received_timestamp TEXT,
                    action TEXT NOT NULL,
                    reason TEXT NOT NULL,
                    created_draft_id TEXT,
                    updated_at TEXT NOT NULL
                )
                """
            )

    def get_record(self, source_message_id: str) -> StateRecord | None:
        with self._connect() as connection:
            row = connection.execute(
                """
                SELECT
                    source_message_id,
                    conversation_id,
                    subject,
                    received_timestamp,
                    action,
                    reason,
                    created_draft_id,
                    updated_at
                FROM message_actions
                WHERE source_message_id = ?
                """,
                (source_message_id,),
            ).fetchone()
        if not row:
            return None
        return StateRecord(**dict(row))

    def has_terminal_record(self, source_message_id: str) -> bool:
        record = self.get_record(source_message_id)
        return bool(record and record.action in TERMINAL_ACTIONS)

    def record_action(
        self,
        *,
        source_message_id: str,
        conversation_id: str | None,
        subject: str,
        received_timestamp: str | None,
        action: str,
        reason: str,
        created_draft_id: str | None = None,
    ) -> StateRecord:
        now = datetime.now(timezone.utc).isoformat()
        record = StateRecord(
            source_message_id=source_message_id,
            conversation_id=conversation_id,
            subject=subject,
            received_timestamp=received_timestamp,
            action=action,
            reason=reason,
            created_draft_id=created_draft_id,
            updated_at=now,
        )
        with self._connect() as connection:
            connection.execute(
                """
                INSERT INTO message_actions (
                    source_message_id,
                    conversation_id,
                    subject,
                    received_timestamp,
                    action,
                    reason,
                    created_draft_id,
                    updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(source_message_id) DO UPDATE SET
                    conversation_id = excluded.conversation_id,
                    subject = excluded.subject,
                    received_timestamp = excluded.received_timestamp,
                    action = excluded.action,
                    reason = excluded.reason,
                    created_draft_id = excluded.created_draft_id,
                    updated_at = excluded.updated_at
                """,
                (
                    record.source_message_id,
                    record.conversation_id,
                    record.subject,
                    record.received_timestamp,
                    record.action,
                    record.reason,
                    record.created_draft_id,
                    record.updated_at,
                ),
            )
        return record
