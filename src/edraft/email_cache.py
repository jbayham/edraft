from __future__ import annotations

import json
import sqlite3
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Iterator, Sequence

from edraft.migrations import apply_migrations
from edraft.models import (
    BriefingEmailItem,
    BriefingResult,
    MailboxMessage,
    Recipient,
    SourceReference,
)
from edraft.text_utils import extract_authored_email_text, truncate_text


DEADLINE_TERMS = (
    "deadline",
    "due",
    "urgent",
    "asap",
    "today",
    "tomorrow",
    "grant",
    "manuscript",
    "class",
    "student",
    "admin",
    "university",
    "schedule",
)


class EmailCacheStore:
    def __init__(self, database_path: Path) -> None:
        self.database_path = database_path
        self.database_path.parent.mkdir(parents=True, exist_ok=True)
        apply_migrations(self.database_path)

    @contextmanager
    def _connect(self) -> Iterator[sqlite3.Connection]:
        connection = sqlite3.connect(self.database_path)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA foreign_keys = ON")
        try:
            yield connection
            connection.commit()
        finally:
            connection.close()

    def upsert_message(
        self,
        message: MailboxMessage,
        *,
        source_folder: str,
        direction: str,
    ) -> None:
        now = datetime.now(timezone.utc).isoformat()
        body_text = _message_text(message)
        with self._connect() as connection:
            connection.execute(
                """
                INSERT INTO email_messages (
                    graph_message_id,
                    conversation_id,
                    internet_message_id,
                    subject,
                    sender_name,
                    sender_email,
                    to_recipients_json,
                    cc_recipients_json,
                    received_at,
                    sent_at,
                    body_preview,
                    normalized_body_text,
                    importance,
                    is_read,
                    has_attachments,
                    categories_json,
                    web_link,
                    last_modified_at,
                    last_synced_at,
                    source_folder,
                    direction,
                    deleted_or_missing
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 0)
                ON CONFLICT(graph_message_id) DO UPDATE SET
                    conversation_id = excluded.conversation_id,
                    internet_message_id = excluded.internet_message_id,
                    subject = excluded.subject,
                    sender_name = excluded.sender_name,
                    sender_email = excluded.sender_email,
                    to_recipients_json = excluded.to_recipients_json,
                    cc_recipients_json = excluded.cc_recipients_json,
                    received_at = excluded.received_at,
                    sent_at = excluded.sent_at,
                    body_preview = excluded.body_preview,
                    normalized_body_text = excluded.normalized_body_text,
                    importance = excluded.importance,
                    is_read = excluded.is_read,
                    has_attachments = excluded.has_attachments,
                    categories_json = excluded.categories_json,
                    web_link = excluded.web_link,
                    last_modified_at = excluded.last_modified_at,
                    last_synced_at = excluded.last_synced_at,
                    source_folder = excluded.source_folder,
                    direction = excluded.direction,
                    deleted_or_missing = 0
                """,
                (
                    message.id,
                    message.conversation_id,
                    message.internet_message_id,
                    message.subject,
                    message.sender_name or None,
                    message.sender_address or None,
                    json.dumps([_recipient_dict(recipient) for recipient in message.to_recipients]),
                    json.dumps([_recipient_dict(recipient) for recipient in message.cc_recipients]),
                    message.received_at.isoformat() if message.received_at else None,
                    message.sent_at.isoformat() if message.sent_at else None,
                    message.body_preview or "",
                    body_text,
                    message.importance,
                    int(message.is_read),
                    int(message.has_attachments),
                    json.dumps(message.categories),
                    message.web_link,
                    message.last_modified_at.isoformat() if message.last_modified_at else None,
                    now,
                    source_folder,
                    direction,
                ),
            )
            connection.execute(
                "DELETE FROM email_participants WHERE graph_message_id = ?",
                (message.id,),
            )
            self._insert_participants(connection, message)
            if message.conversation_id:
                self._refresh_thread(connection, message.conversation_id)

    def get_message(self, graph_message_id: str) -> BriefingEmailItem | None:
        with self._connect() as connection:
            row = connection.execute(
                "SELECT * FROM email_messages WHERE graph_message_id = ?",
                (graph_message_id,),
            ).fetchone()
        return _row_to_email_item(row) if row else None

    def recent_messages(self, *, since: datetime, limit: int) -> list[BriefingEmailItem]:
        with self._connect() as connection:
            rows = connection.execute(
                """
                SELECT *
                FROM email_messages
                WHERE deleted_or_missing = 0
                  AND COALESCE(received_at, sent_at) >= ?
                ORDER BY COALESCE(received_at, sent_at) DESC
                LIMIT ?
                """,
                (since.isoformat(), limit),
            ).fetchall()
        return [_row_to_email_item(row) for row in rows]

    def triage_candidates(self, *, since: datetime, limit: int) -> list[BriefingEmailItem]:
        candidates = self.recent_messages(since=since, limit=limit * 3)
        ranked = sorted(
            candidates,
            key=lambda item: self._triage_score(item),
            reverse=True,
        )
        return [
            _with_rationale(item, self._triage_rationale(item), score=self._triage_score(item))
            for item in ranked[:limit]
            if self._triage_score(item) > 0
        ]

    def follow_up_candidates(self, *, stale_after: datetime, limit: int) -> list[BriefingEmailItem]:
        with self._connect() as connection:
            rows = connection.execute(
                """
                SELECT m.*
                FROM email_threads AS t
                JOIN email_messages AS m
                  ON m.conversation_id = t.conversation_id
                WHERE t.last_message_at <= ?
                  AND m.deleted_or_missing = 0
                  AND COALESCE(m.received_at, m.sent_at) = t.last_message_at
                ORDER BY t.last_message_at ASC
                LIMIT ?
                """,
                (stale_after.isoformat(), limit),
            ).fetchall()
        return [
            _with_rationale(
                _row_to_email_item(row),
                "stale conversation worth revisiting",
                score=1.0,
            )
            for row in rows
        ]

    def search_related_messages(
        self,
        *,
        keywords: Sequence[str],
        participant_emails: Sequence[str],
        before: datetime | None,
        limit: int,
    ) -> list[BriefingEmailItem]:
        clauses = ["m.deleted_or_missing = 0"]
        params: list[object] = []
        keyword_clauses = []
        for keyword in keywords[:8]:
            pattern = f"%{keyword}%"
            keyword_clauses.append("(m.subject LIKE ? OR m.body_preview LIKE ? OR m.normalized_body_text LIKE ?)")
            params.extend([pattern, pattern, pattern])
        participant_clause = ""
        normalized_participants = [email.casefold() for email in participant_emails if email]
        if normalized_participants:
            placeholders = ", ".join("?" for _ in normalized_participants)
            participant_clause = (
                "m.graph_message_id IN ("
                "SELECT graph_message_id FROM email_participants "
                f"WHERE email IN ({placeholders})"
                ")"
            )
        if keyword_clauses and participant_clause:
            clauses.append(f"(({' OR '.join(keyword_clauses)}) OR {participant_clause})")
            params.extend(normalized_participants)
        elif keyword_clauses:
            clauses.append(f"({' OR '.join(keyword_clauses)})")
        elif participant_clause:
            clauses.append(participant_clause)
            params.extend(normalized_participants)
        if before is not None:
            clauses.append("COALESCE(m.received_at, m.sent_at) <= ?")
            params.append(before.isoformat())
        params.append(limit)
        with self._connect() as connection:
            rows = connection.execute(
                f"""
                SELECT m.*
                FROM email_messages AS m
                WHERE {" AND ".join(clauses)}
                ORDER BY COALESCE(m.received_at, m.sent_at) DESC
                LIMIT ?
                """,
                params,
            ).fetchall()
        return [_row_to_email_item(row) for row in rows]

    def is_sync_fresh(
        self,
        *,
        folder: str,
        direction: str,
        lookback_days: int,
        freshness_minutes: int,
    ) -> bool:
        if freshness_minutes <= 0:
            return False
        sync_key = _sync_key(folder, direction, lookback_days)
        with self._connect() as connection:
            row = connection.execute(
                "SELECT last_successful_sync_at FROM email_sync_state WHERE sync_key = ?",
                (sync_key,),
            ).fetchone()
        if not row or not row["last_successful_sync_at"]:
            return False
        last_success = datetime.fromisoformat(row["last_successful_sync_at"])
        return datetime.now(timezone.utc) - last_success < timedelta(minutes=freshness_minutes)

    def record_sync(
        self,
        *,
        folder: str,
        direction: str,
        lookback_days: int,
        success: bool,
        error: str | None = None,
    ) -> None:
        now = datetime.now(timezone.utc).isoformat()
        sync_key = _sync_key(folder, direction, lookback_days)
        with self._connect() as connection:
            connection.execute(
                """
                INSERT INTO email_sync_state (
                    sync_key,
                    folder,
                    direction,
                    lookback_days,
                    last_successful_sync_at,
                    last_attempted_sync_at,
                    last_error,
                    delta_token
                ) VALUES (?, ?, ?, ?, ?, ?, ?, NULL)
                ON CONFLICT(sync_key) DO UPDATE SET
                    last_successful_sync_at = COALESCE(excluded.last_successful_sync_at, email_sync_state.last_successful_sync_at),
                    last_attempted_sync_at = excluded.last_attempted_sync_at,
                    last_error = excluded.last_error
                """,
                (
                    sync_key,
                    folder,
                    direction,
                    lookback_days,
                    now if success else None,
                    now,
                    None if success else truncate_text(error or "sync failed", 500),
                ),
            )

    def save_briefing_run(
        self,
        result: BriefingResult,
        *,
        request_json: dict[str, Any],
        email_sync_summary: dict[str, Any],
        status: str = "completed",
    ) -> None:
        now = datetime.now(timezone.utc).isoformat()
        result_payload = result.to_dict()
        with self._connect() as connection:
            connection.execute(
                """
                INSERT INTO briefing_runs (
                    briefing_run_id,
                    briefing_date,
                    timezone,
                    model,
                    status,
                    request_json,
                    result_json,
                    markdown,
                    email_sync_summary_json,
                    created_at,
                    updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(briefing_run_id) DO UPDATE SET
                    status = excluded.status,
                    result_json = excluded.result_json,
                    markdown = excluded.markdown,
                    email_sync_summary_json = excluded.email_sync_summary_json,
                    updated_at = excluded.updated_at
                """,
                (
                    result.briefing_run_id,
                    result.briefing_date,
                    result.timezone,
                    result.model,
                    status,
                    json.dumps(request_json),
                    json.dumps(result_payload),
                    result.markdown,
                    json.dumps(email_sync_summary),
                    now,
                    now,
                ),
            )
            connection.execute(
                "DELETE FROM briefing_sources WHERE briefing_run_id = ?",
                (result.briefing_run_id,),
            )
            for source in result.source_references:
                connection.execute(
                    """
                    INSERT INTO briefing_sources (
                        briefing_run_id,
                        source_type,
                        source_id,
                        web_link,
                        title,
                        metadata_json
                    ) VALUES (?, ?, ?, ?, ?, '{}')
                    """,
                    (
                        result.briefing_run_id,
                        source.source_type,
                        source.source_id,
                        source.web_link,
                        source.title,
                    ),
                )

    def latest_briefing(self, briefing_date: str) -> dict[str, Any] | None:
        with self._connect() as connection:
            row = connection.execute(
                """
                SELECT *
                FROM briefing_runs
                WHERE briefing_date = ? AND status = 'completed'
                ORDER BY updated_at DESC
                LIMIT 1
                """,
                (briefing_date,),
            ).fetchone()
        if not row:
            return None
        payload = dict(row)
        payload["request"] = json.loads(payload.pop("request_json"))
        payload["result"] = json.loads(payload.pop("result_json") or "{}")
        payload["email_sync_summary"] = json.loads(payload.pop("email_sync_summary_json") or "{}")
        return payload

    def record_event_links(
        self,
        *,
        briefing_run_id: str,
        event_id: str,
        links: Sequence[tuple[BriefingEmailItem, float, list[str]]],
    ) -> None:
        now = datetime.now(timezone.utc).isoformat()
        with self._connect() as connection:
            for item, score, reasons in links:
                connection.execute(
                    """
                    INSERT INTO email_event_links (
                        briefing_run_id,
                        event_id,
                        graph_message_id,
                        score,
                        match_reasons_json,
                        created_at
                    ) VALUES (?, ?, ?, ?, ?, ?)
                    ON CONFLICT(briefing_run_id, event_id, graph_message_id) DO UPDATE SET
                        score = excluded.score,
                        match_reasons_json = excluded.match_reasons_json,
                        created_at = excluded.created_at
                    """,
                    (
                        briefing_run_id,
                        event_id,
                        item.graph_message_id,
                        score,
                        json.dumps(reasons),
                        now,
                    ),
                )

    def _insert_participants(self, connection: sqlite3.Connection, message: MailboxMessage) -> None:
        participants: list[tuple[Recipient, str]] = []
        if message.from_recipient:
            participants.append((message.from_recipient, "sender"))
        participants.extend((recipient, "to") for recipient in message.to_recipients)
        participants.extend((recipient, "cc") for recipient in message.cc_recipients)
        for recipient, role in participants:
            connection.execute(
                """
                INSERT OR IGNORE INTO email_participants (
                    graph_message_id,
                    conversation_id,
                    email,
                    name,
                    role
                ) VALUES (?, ?, ?, ?, ?)
                """,
                (
                    message.id,
                    message.conversation_id,
                    recipient.address.casefold(),
                    recipient.name or None,
                    role,
                ),
            )

    def _refresh_thread(self, connection: sqlite3.Connection, conversation_id: str) -> None:
        rows = connection.execute(
            """
            SELECT
                subject,
                direction,
                sender_email,
                to_recipients_json,
                cc_recipients_json,
                COALESCE(received_at, sent_at) AS message_at
            FROM email_messages
            WHERE conversation_id = ? AND deleted_or_missing = 0
            """,
            (conversation_id,),
        ).fetchall()
        if not rows:
            return
        message_times = [row["message_at"] for row in rows if row["message_at"]]
        last_message_at = max(message_times) if message_times else None
        inbound_times = [row["message_at"] for row in rows if row["direction"] == "inbound" and row["message_at"]]
        outbound_times = [row["message_at"] for row in rows if row["direction"] == "outbound" and row["message_at"]]
        participants = set()
        for row in rows:
            if row["sender_email"]:
                participants.add(row["sender_email"].casefold())
            for field in ("to_recipients_json", "cc_recipients_json"):
                for recipient in json.loads(row[field] or "[]"):
                    if recipient.get("address"):
                        participants.add(str(recipient["address"]).casefold())
        connection.execute(
            """
            INSERT INTO email_threads (
                conversation_id,
                subject,
                last_message_at,
                message_count,
                inbound_count,
                outbound_count,
                last_inbound_at,
                last_outbound_at,
                participant_emails_json,
                updated_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(conversation_id) DO UPDATE SET
                subject = excluded.subject,
                last_message_at = excluded.last_message_at,
                message_count = excluded.message_count,
                inbound_count = excluded.inbound_count,
                outbound_count = excluded.outbound_count,
                last_inbound_at = excluded.last_inbound_at,
                last_outbound_at = excluded.last_outbound_at,
                participant_emails_json = excluded.participant_emails_json,
                updated_at = excluded.updated_at
            """,
            (
                conversation_id,
                rows[0]["subject"] or "",
                last_message_at,
                len(rows),
                sum(1 for row in rows if row["direction"] == "inbound"),
                sum(1 for row in rows if row["direction"] == "outbound"),
                max(inbound_times) if inbound_times else None,
                max(outbound_times) if outbound_times else None,
                json.dumps(sorted(participants)),
                datetime.now(timezone.utc).isoformat(),
            ),
        )

    @staticmethod
    def _triage_score(item: BriefingEmailItem) -> float:
        text = f"{item.subject}\n{item.body_preview}\n{item.normalized_body_text}".casefold()
        score = 0.0
        if item.direction == "inbound":
            score += 1.0
        if not item.is_read:
            score += 1.0
        if (item.importance or "").casefold() == "high":
            score += 2.0
        score += min(sum(1 for term in DEADLINE_TERMS if term in text), 4) * 0.5
        if item.has_attachments:
            score += 0.25
        return score

    @staticmethod
    def _triage_rationale(item: BriefingEmailItem) -> str:
        reasons = []
        if item.direction == "inbound":
            reasons.append("inbound")
        if not item.is_read:
            reasons.append("unread")
        if (item.importance or "").casefold() == "high":
            reasons.append("marked high importance")
        text = f"{item.subject}\n{item.body_preview}\n{item.normalized_body_text}".casefold()
        matched_terms = [term for term in DEADLINE_TERMS if term in text][:3]
        if matched_terms:
            reasons.append("mentions " + ", ".join(matched_terms))
        return "; ".join(reasons) if reasons else "recent message"


def _recipient_dict(recipient: Recipient) -> dict[str, str]:
    return {"name": recipient.name, "address": recipient.address}


def _message_text(message: MailboxMessage) -> str:
    extracted = extract_authored_email_text(message.body_content, message.body_content_type).strip()
    if extracted:
        return truncate_text(extracted, 4000)
    return truncate_text(message.body_preview or "", 1000)


def _row_to_email_item(row: sqlite3.Row) -> BriefingEmailItem:
    return BriefingEmailItem(
        graph_message_id=row["graph_message_id"],
        conversation_id=row["conversation_id"],
        subject=row["subject"],
        sender_name=row["sender_name"],
        sender_email=row["sender_email"],
        received_at=row["received_at"],
        sent_at=row["sent_at"],
        body_preview=row["body_preview"],
        normalized_body_text=row["normalized_body_text"],
        importance=row["importance"],
        is_read=bool(row["is_read"]),
        has_attachments=bool(row["has_attachments"]),
        web_link=row["web_link"],
        direction=row["direction"],
        source_folder=row["source_folder"],
    )


def _with_rationale(
    item: BriefingEmailItem,
    rationale: str,
    *,
    score: float,
) -> BriefingEmailItem:
    return BriefingEmailItem(
        graph_message_id=item.graph_message_id,
        conversation_id=item.conversation_id,
        subject=item.subject,
        sender_name=item.sender_name,
        sender_email=item.sender_email,
        received_at=item.received_at,
        sent_at=item.sent_at,
        body_preview=item.body_preview,
        normalized_body_text=item.normalized_body_text,
        importance=item.importance,
        is_read=item.is_read,
        has_attachments=item.has_attachments,
        web_link=item.web_link,
        direction=item.direction,
        source_folder=item.source_folder,
        rationale=rationale,
        score=score,
    )


def _sync_key(folder: str, direction: str, lookback_days: int) -> str:
    return f"{folder.casefold()}:{direction}:{lookback_days}"
