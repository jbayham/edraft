from __future__ import annotations

import sqlite3
from contextlib import contextmanager
from pathlib import Path
from typing import Any, Iterator


USER_TABLES = [
    "message_actions",
    "meeting_suggestions",
    "style_messages",
    "style_reply_pairs",
    "style_eval_examples",
    "style_eval_results",
    "style_examples_fts",
    "schema_migrations",
    "email_messages",
    "email_participants",
    "email_threads",
    "email_sync_state",
    "briefing_runs",
    "briefing_sources",
    "email_event_links",
]


class DatabaseInspector:
    def __init__(self, database_path: Path) -> None:
        self.database_path = database_path

    @contextmanager
    def _connect(self) -> Iterator[sqlite3.Connection]:
        connection = sqlite3.connect(self.database_path)
        connection.row_factory = sqlite3.Row
        try:
            yield connection
        finally:
            connection.close()

    def summary(self) -> dict[str, Any]:
        available_tables = self.available_tables()
        table_summaries = {}
        with self._connect() as connection:
            for table in available_tables:
                count = connection.execute(f"SELECT COUNT(*) AS n FROM {table}").fetchone()["n"]
                table_summaries[table] = {"row_count": count}
        return {
            "database_path": str(self.database_path),
            "available_tables": available_tables,
            "tables": table_summaries,
        }

    def inspect_table(self, table: str, *, limit: int) -> dict[str, Any]:
        available_tables = self.available_tables()
        if table not in available_tables:
            raise ValueError(
                f"Unsupported table '{table}'. Available tables: {', '.join(available_tables)}"
            )
        with self._connect() as connection:
            columns = [
                row["name"]
                for row in connection.execute(f"PRAGMA table_info({table})").fetchall()
            ]
            count = connection.execute(f"SELECT COUNT(*) AS n FROM {table}").fetchone()["n"]
            order_clause = self._order_clause(columns)
            rows = connection.execute(
                f"SELECT * FROM {table} {order_clause} LIMIT ?",
                (limit,),
            ).fetchall()
        return {
            "database_path": str(self.database_path),
            "table": table,
            "row_count": count,
            "columns": columns,
            "rows": [dict(row) for row in rows],
        }

    def corpus_stats(self) -> dict[str, Any]:
        available_tables = set(self.available_tables())
        payload: dict[str, Any] = {
            "database_path": str(self.database_path),
            "style_corpus_available": "style_reply_pairs" in available_tables,
        }
        if "style_reply_pairs" not in available_tables:
            payload["style_corpus"] = None
            return payload

        with self._connect() as connection:
            pairs_row = connection.execute(
                """
                SELECT
                    COUNT(*) AS pair_count,
                    COUNT(DISTINCT correspondent_email) AS correspondent_count,
                    MIN(reply_received_timestamp) AS oldest_reply_timestamp,
                    MAX(reply_received_timestamp) AS newest_reply_timestamp,
                    MIN(inbound_received_timestamp) AS oldest_inbound_timestamp,
                    MAX(inbound_received_timestamp) AS newest_inbound_timestamp,
                    ROUND(AVG(LENGTH(reply_text)), 1) AS average_reply_chars,
                    ROUND(AVG(LENGTH(inbound_text)), 1) AS average_inbound_chars,
                    SUM(LENGTH(reply_text) + LENGTH(inbound_text)) AS total_text_chars
                FROM style_reply_pairs
                """
            ).fetchone()

            messages_row = (
                connection.execute(
                    """
                    SELECT
                        COUNT(*) AS message_count,
                        MIN(received_timestamp) AS oldest_message_timestamp,
                        MAX(received_timestamp) AS newest_message_timestamp
                    FROM style_messages
                    """
                ).fetchone()
                if "style_messages" in available_tables
                else None
            )
            holdout_row = (
                connection.execute(
                    "SELECT COUNT(*) AS holdout_case_count FROM style_eval_examples WHERE split = 'holdout'"
                ).fetchone()
                if "style_eval_examples" in available_tables
                else None
            )
            top_correspondents = connection.execute(
                """
                SELECT correspondent_email, COUNT(*) AS pair_count
                FROM style_reply_pairs
                GROUP BY correspondent_email
                ORDER BY pair_count DESC, correspondent_email ASC
                LIMIT 10
                """
            ).fetchall()

        total_text_chars = pairs_row["total_text_chars"] or 0
        payload["style_corpus"] = {
            "pair_count": pairs_row["pair_count"],
            "correspondent_count": pairs_row["correspondent_count"],
            "date_coverage": {
                "oldest_reply_timestamp": pairs_row["oldest_reply_timestamp"],
                "newest_reply_timestamp": pairs_row["newest_reply_timestamp"],
                "oldest_inbound_timestamp": pairs_row["oldest_inbound_timestamp"],
                "newest_inbound_timestamp": pairs_row["newest_inbound_timestamp"],
            },
            "message_archive": {
                "message_count": messages_row["message_count"] if messages_row is not None else 0,
                "oldest_message_timestamp": (
                    messages_row["oldest_message_timestamp"] if messages_row is not None else None
                ),
                "newest_message_timestamp": (
                    messages_row["newest_message_timestamp"] if messages_row is not None else None
                ),
            },
            "eval_holdout": {
                "holdout_case_count": holdout_row["holdout_case_count"] if holdout_row is not None else 0,
            },
            "text_volume": {
                "total_text_chars": total_text_chars,
                "total_text_kb": round(total_text_chars / 1024, 1),
                "average_reply_chars": pairs_row["average_reply_chars"] or 0.0,
                "average_inbound_chars": pairs_row["average_inbound_chars"] or 0.0,
            },
            "top_correspondents": [dict(row) for row in top_correspondents],
        }
        return payload

    def available_tables(self) -> list[str]:
        with self._connect() as connection:
            rows = connection.execute(
                """
                SELECT name
                FROM sqlite_master
                WHERE type IN ('table', 'view')
                ORDER BY name
                """
            ).fetchall()
        existing = {row["name"] for row in rows}
        return [table for table in USER_TABLES if table in existing]

    @staticmethod
    def _order_clause(columns: list[str]) -> str:
        for column in [
            "updated_at",
            "evaluated_at",
            "reply_received_timestamp",
            "received_timestamp",
            "received_at",
            "sent_at",
            "receivedDateTime",
            "last_message_at",
            "last_successful_sync_at",
            "created_at",
        ]:
            if column in columns:
                return f"ORDER BY {column} DESC"
        return "ORDER BY rowid DESC"
