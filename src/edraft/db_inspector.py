from __future__ import annotations

import sqlite3
from contextlib import contextmanager
from pathlib import Path
from typing import Any, Iterator


USER_TABLES = [
    "message_actions",
    "style_messages",
    "style_reply_pairs",
    "style_eval_examples",
    "style_examples_fts",
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
            "reply_received_timestamp",
            "received_timestamp",
            "receivedDateTime",
        ]:
            if column in columns:
                return f"ORDER BY {column} DESC"
        return "ORDER BY rowid DESC"
