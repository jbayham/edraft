from __future__ import annotations

import sqlite3
from pathlib import Path
from typing import Iterable


MIGRATIONS: tuple[tuple[str, tuple[str, ...]], ...] = (
    (
        "001_briefing_email_cache",
        (
            """
            CREATE TABLE IF NOT EXISTS email_messages (
                graph_message_id TEXT PRIMARY KEY,
                conversation_id TEXT,
                internet_message_id TEXT,
                subject TEXT NOT NULL,
                sender_name TEXT,
                sender_email TEXT,
                to_recipients_json TEXT NOT NULL DEFAULT '[]',
                cc_recipients_json TEXT NOT NULL DEFAULT '[]',
                received_at TEXT,
                sent_at TEXT,
                body_preview TEXT NOT NULL DEFAULT '',
                normalized_body_text TEXT NOT NULL DEFAULT '',
                importance TEXT,
                is_read INTEGER NOT NULL DEFAULT 0,
                has_attachments INTEGER NOT NULL DEFAULT 0,
                categories_json TEXT NOT NULL DEFAULT '[]',
                web_link TEXT,
                last_modified_at TEXT,
                last_synced_at TEXT NOT NULL,
                source_folder TEXT NOT NULL,
                direction TEXT NOT NULL,
                deleted_or_missing INTEGER NOT NULL DEFAULT 0
            )
            """,
            """
            CREATE INDEX IF NOT EXISTS idx_email_messages_conversation
            ON email_messages(conversation_id)
            """,
            """
            CREATE INDEX IF NOT EXISTS idx_email_messages_received
            ON email_messages(received_at DESC)
            """,
            """
            CREATE INDEX IF NOT EXISTS idx_email_messages_sent
            ON email_messages(sent_at DESC)
            """,
            """
            CREATE INDEX IF NOT EXISTS idx_email_messages_direction
            ON email_messages(direction, received_at DESC, sent_at DESC)
            """,
            """
            CREATE TABLE IF NOT EXISTS email_participants (
                graph_message_id TEXT NOT NULL,
                conversation_id TEXT,
                email TEXT NOT NULL,
                name TEXT,
                role TEXT NOT NULL,
                PRIMARY KEY (graph_message_id, email, role),
                FOREIGN KEY (graph_message_id) REFERENCES email_messages(graph_message_id)
                    ON DELETE CASCADE
            )
            """,
            """
            CREATE INDEX IF NOT EXISTS idx_email_participants_email
            ON email_participants(email, conversation_id)
            """,
            """
            CREATE TABLE IF NOT EXISTS email_threads (
                conversation_id TEXT PRIMARY KEY,
                subject TEXT NOT NULL,
                last_message_at TEXT,
                message_count INTEGER NOT NULL DEFAULT 0,
                inbound_count INTEGER NOT NULL DEFAULT 0,
                outbound_count INTEGER NOT NULL DEFAULT 0,
                last_inbound_at TEXT,
                last_outbound_at TEXT,
                participant_emails_json TEXT NOT NULL DEFAULT '[]',
                updated_at TEXT NOT NULL
            )
            """,
            """
            CREATE TABLE IF NOT EXISTS email_sync_state (
                sync_key TEXT PRIMARY KEY,
                folder TEXT NOT NULL,
                direction TEXT NOT NULL,
                lookback_days INTEGER NOT NULL,
                last_successful_sync_at TEXT,
                last_attempted_sync_at TEXT,
                last_error TEXT,
                delta_token TEXT
            )
            """,
            """
            CREATE TABLE IF NOT EXISTS briefing_runs (
                briefing_run_id TEXT PRIMARY KEY,
                briefing_date TEXT NOT NULL,
                timezone TEXT NOT NULL,
                model TEXT NOT NULL,
                status TEXT NOT NULL,
                request_json TEXT NOT NULL,
                result_json TEXT,
                markdown TEXT,
                email_sync_summary_json TEXT NOT NULL DEFAULT '{}',
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL
            )
            """,
            """
            CREATE INDEX IF NOT EXISTS idx_briefing_runs_date
            ON briefing_runs(briefing_date, updated_at DESC)
            """,
            """
            CREATE TABLE IF NOT EXISTS briefing_sources (
                briefing_run_id TEXT NOT NULL,
                source_type TEXT NOT NULL,
                source_id TEXT NOT NULL,
                web_link TEXT,
                title TEXT,
                metadata_json TEXT NOT NULL DEFAULT '{}',
                PRIMARY KEY (briefing_run_id, source_type, source_id),
                FOREIGN KEY (briefing_run_id) REFERENCES briefing_runs(briefing_run_id)
                    ON DELETE CASCADE
            )
            """,
            """
            CREATE TABLE IF NOT EXISTS email_event_links (
                briefing_run_id TEXT NOT NULL,
                event_id TEXT NOT NULL,
                graph_message_id TEXT NOT NULL,
                score REAL NOT NULL,
                match_reasons_json TEXT NOT NULL DEFAULT '[]',
                created_at TEXT NOT NULL,
                PRIMARY KEY (briefing_run_id, event_id, graph_message_id),
                FOREIGN KEY (briefing_run_id) REFERENCES briefing_runs(briefing_run_id)
                    ON DELETE CASCADE,
                FOREIGN KEY (graph_message_id) REFERENCES email_messages(graph_message_id)
                    ON DELETE CASCADE
            )
            """,
        ),
    ),
)


def apply_migrations(database_path: Path) -> None:
    database_path.parent.mkdir(parents=True, exist_ok=True)
    with sqlite3.connect(database_path) as connection:
        connection.execute("PRAGMA foreign_keys = ON")
        connection.execute(
            """
            CREATE TABLE IF NOT EXISTS schema_migrations (
                version TEXT PRIMARY KEY,
                applied_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
            )
            """
        )
        applied = {
            row[0]
            for row in connection.execute("SELECT version FROM schema_migrations").fetchall()
        }
        for version, statements in MIGRATIONS:
            if version in applied:
                continue
            _apply_one(connection, version, statements)
        connection.commit()


def _apply_one(
    connection: sqlite3.Connection,
    version: str,
    statements: Iterable[str],
) -> None:
    with connection:
        for statement in statements:
            connection.execute(statement)
        connection.execute(
            "INSERT INTO schema_migrations (version) VALUES (?)",
            (version,),
        )
