from __future__ import annotations

import json
import logging
import re
import sqlite3
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Iterator, Sequence

from edraft.config import IdentityConfig, StyleCorpusConfig
from edraft.graph_client import GraphClient
from edraft.models import MailboxMessage, StyleExample, ThreadContext, parse_datetime
from edraft.text_utils import extract_authored_email_text, truncate_text


STOPWORDS = {
    "about",
    "after",
    "also",
    "because",
    "from",
    "have",
    "into",
    "just",
    "more",
    "that",
    "them",
    "they",
    "this",
    "were",
    "will",
    "with",
    "your",
}

REPLY_SUBJECT_PREFIXES = ("re:", "aw:", "sv:")
REPLY_HEADER_NAMES = {"in-reply-to", "references"}


@dataclass(slots=True)
class StylePairRecord:
    reply_message_id: str
    inbound_message_id: str
    conversation_id: str | None
    correspondent_email: str
    subject: str
    inbound_received_timestamp: str | None
    reply_received_timestamp: str | None
    inbound_text: str
    reply_text: str
    pairing_source: str
    pairing_confidence: float
    updated_at: str


@dataclass(slots=True)
class CorpusSyncReport:
    scanned: int = 0
    paired: int = 0
    skipped: int = 0
    errors: int = 0


@dataclass(slots=True)
class StyleEvalCase:
    reply_message_id: str
    correspondent_email: str
    subject: str
    inbound_text: str
    actual_reply_text: str
    reply_received_timestamp: str | None


@dataclass(slots=True)
class RetrievalCandidate:
    pair: StylePairRecord
    source: str
    source_priority: int
    query_rank: int | None = None


class StyleCorpusStore:
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
                CREATE TABLE IF NOT EXISTS style_messages (
                    message_id TEXT PRIMARY KEY,
                    conversation_id TEXT,
                    folder TEXT NOT NULL,
                    direction TEXT NOT NULL,
                    sender_email TEXT,
                    recipient_emails TEXT NOT NULL,
                    subject TEXT NOT NULL,
                    received_timestamp TEXT,
                    body_text TEXT NOT NULL,
                    is_draft INTEGER NOT NULL DEFAULT 0,
                    web_link TEXT,
                    updated_at TEXT NOT NULL
                )
                """
            )
            connection.execute(
                """
                CREATE TABLE IF NOT EXISTS style_reply_pairs (
                    reply_message_id TEXT PRIMARY KEY,
                    inbound_message_id TEXT NOT NULL,
                    conversation_id TEXT,
                    correspondent_email TEXT NOT NULL,
                    subject TEXT NOT NULL,
                    inbound_received_timestamp TEXT,
                    reply_received_timestamp TEXT,
                    inbound_text TEXT NOT NULL,
                    reply_text TEXT NOT NULL,
                    pairing_source TEXT NOT NULL,
                    pairing_confidence REAL NOT NULL,
                    updated_at TEXT NOT NULL
                )
                """
            )
            connection.execute(
                """
                CREATE INDEX IF NOT EXISTS idx_style_pairs_correspondent
                ON style_reply_pairs(correspondent_email, reply_received_timestamp DESC)
                """
            )
            connection.execute(
                """
                CREATE TABLE IF NOT EXISTS style_eval_examples (
                    reply_message_id TEXT PRIMARY KEY,
                    split TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                )
                """
            )
            connection.execute(
                """
                CREATE TABLE IF NOT EXISTS style_eval_results (
                    eval_result_id INTEGER PRIMARY KEY AUTOINCREMENT,
                    run_id TEXT NOT NULL,
                    reply_message_id TEXT NOT NULL,
                    correspondent_email TEXT NOT NULL,
                    subject TEXT NOT NULL,
                    generated_reply TEXT NOT NULL,
                    actual_reply TEXT NOT NULL,
                    style_example_ids TEXT NOT NULL,
                    generation_system_prompt TEXT NOT NULL,
                    generation_user_prompt TEXT NOT NULL,
                    grading_system_prompt TEXT NOT NULL,
                    grading_user_prompt TEXT NOT NULL,
                    tone_match INTEGER NOT NULL,
                    brevity_match INTEGER NOT NULL,
                    commitment_safety INTEGER NOT NULL,
                    clarity INTEGER NOT NULL,
                    overall INTEGER NOT NULL,
                    notes TEXT NOT NULL,
                    model TEXT NOT NULL,
                    reasoning_effort TEXT,
                    updated_at TEXT NOT NULL
                )
                """
            )
            connection.execute(
                """
                CREATE INDEX IF NOT EXISTS idx_style_eval_results_run
                ON style_eval_results(run_id, updated_at DESC)
                """
            )
            connection.execute(
                """
                CREATE INDEX IF NOT EXISTS idx_style_eval_results_reply
                ON style_eval_results(reply_message_id, updated_at DESC)
                """
            )
            connection.execute(
                """
                CREATE VIRTUAL TABLE IF NOT EXISTS style_examples_fts USING fts5(
                    reply_message_id UNINDEXED,
                    correspondent_email,
                    subject,
                    inbound_text,
                    reply_text
                )
                """
            )

    def upsert_message(self, message: MailboxMessage, *, folder: str, direction: str) -> None:
        now = datetime.now(timezone.utc).isoformat()
        recipient_emails = [recipient.address for recipient in message.all_recipients()]
        body_text = _message_text(message)
        with self._connect() as connection:
            connection.execute(
                """
                INSERT INTO style_messages (
                    message_id,
                    conversation_id,
                    folder,
                    direction,
                    sender_email,
                    recipient_emails,
                    subject,
                    received_timestamp,
                    body_text,
                    is_draft,
                    web_link,
                    updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(message_id) DO UPDATE SET
                    conversation_id = excluded.conversation_id,
                    folder = excluded.folder,
                    direction = excluded.direction,
                    sender_email = excluded.sender_email,
                    recipient_emails = excluded.recipient_emails,
                    subject = excluded.subject,
                    received_timestamp = excluded.received_timestamp,
                    body_text = excluded.body_text,
                    is_draft = excluded.is_draft,
                    web_link = excluded.web_link,
                    updated_at = excluded.updated_at
                """,
                (
                    message.id,
                    message.conversation_id,
                    folder,
                    direction,
                    message.sender_address or None,
                    json.dumps(recipient_emails),
                    message.subject,
                    message.received_at.isoformat() if message.received_at else None,
                    body_text,
                    int(message.is_draft),
                    message.web_link,
                    now,
                ),
            )

    def upsert_pair(
        self,
        *,
        inbound_message: MailboxMessage,
        reply_message: MailboxMessage,
        correspondent_email: str,
        pairing_source: str,
        pairing_confidence: float,
    ) -> None:
        now = datetime.now(timezone.utc).isoformat()
        inbound_text = _message_text(inbound_message)
        reply_text = _message_text(reply_message)
        with self._connect() as connection:
            connection.execute(
                """
                INSERT INTO style_reply_pairs (
                    reply_message_id,
                    inbound_message_id,
                    conversation_id,
                    correspondent_email,
                    subject,
                    inbound_received_timestamp,
                    reply_received_timestamp,
                    inbound_text,
                    reply_text,
                    pairing_source,
                    pairing_confidence,
                    updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(reply_message_id) DO UPDATE SET
                    inbound_message_id = excluded.inbound_message_id,
                    conversation_id = excluded.conversation_id,
                    correspondent_email = excluded.correspondent_email,
                    subject = excluded.subject,
                    inbound_received_timestamp = excluded.inbound_received_timestamp,
                    reply_received_timestamp = excluded.reply_received_timestamp,
                    inbound_text = excluded.inbound_text,
                    reply_text = excluded.reply_text,
                    pairing_source = excluded.pairing_source,
                    pairing_confidence = excluded.pairing_confidence,
                    updated_at = excluded.updated_at
                """,
                (
                    reply_message.id,
                    inbound_message.id,
                    reply_message.conversation_id,
                    correspondent_email,
                    inbound_message.subject or reply_message.subject,
                    inbound_message.received_at.isoformat() if inbound_message.received_at else None,
                    reply_message.received_at.isoformat() if reply_message.received_at else None,
                    inbound_text,
                    reply_text,
                    pairing_source,
                    pairing_confidence,
                    now,
                ),
            )
            connection.execute(
                "DELETE FROM style_examples_fts WHERE reply_message_id = ?",
                (reply_message.id,),
            )
            connection.execute(
                """
                INSERT INTO style_examples_fts (
                    reply_message_id,
                    correspondent_email,
                    subject,
                    inbound_text,
                    reply_text
                ) VALUES (?, ?, ?, ?, ?)
                """,
                (
                    reply_message.id,
                    correspondent_email,
                    inbound_message.subject or reply_message.subject,
                    inbound_text,
                    reply_text,
                ),
            )

    def delete_pair(self, reply_message_id: str) -> None:
        with self._connect() as connection:
            connection.execute(
                "DELETE FROM style_reply_pairs WHERE reply_message_id = ?",
                (reply_message_id,),
            )
            connection.execute(
                "DELETE FROM style_examples_fts WHERE reply_message_id = ?",
                (reply_message_id,),
            )

    def find_pairs_by_correspondent(
        self,
        correspondent_email: str,
        *,
        limit: int,
        exclude_reply_ids: Sequence[str] = (),
        reply_received_before: datetime | None = None,
    ) -> list[StylePairRecord]:
        clauses = ["correspondent_email = ?"]
        params: list[object] = [correspondent_email.casefold()]
        if reply_received_before is not None:
            clauses.append("reply_received_timestamp < ?")
            params.append(reply_received_before.isoformat())
        if exclude_reply_ids:
            placeholders = ", ".join("?" for _ in exclude_reply_ids)
            clauses.append(f"reply_message_id NOT IN ({placeholders})")
            params.extend(exclude_reply_ids)
        params.append(limit)
        with self._connect() as connection:
            rows = connection.execute(
                f"""
                SELECT
                    reply_message_id,
                    inbound_message_id,
                    conversation_id,
                    correspondent_email,
                    subject,
                    inbound_received_timestamp,
                    reply_received_timestamp,
                    inbound_text,
                    reply_text,
                    pairing_source,
                    pairing_confidence,
                    updated_at
                FROM style_reply_pairs
                WHERE {" AND ".join(clauses)}
                ORDER BY pairing_confidence DESC, reply_received_timestamp DESC
                LIMIT ?
                """,
                params,
            ).fetchall()
        return [StylePairRecord(**dict(row)) for row in rows]

    def search_pairs(
        self,
        query_text: str,
        *,
        limit: int,
        exclude_reply_ids: Sequence[str] = (),
        reply_received_before: datetime | None = None,
    ) -> list[StylePairRecord]:
        fts_query = _build_fts_query(query_text)
        if not fts_query:
            return []
        clauses = ["style_examples_fts MATCH ?"]
        params: list[object] = [fts_query]
        if reply_received_before is not None:
            clauses.append("p.reply_received_timestamp < ?")
            params.append(reply_received_before.isoformat())
        if exclude_reply_ids:
            placeholders = ", ".join("?" for _ in exclude_reply_ids)
            clauses.append(f"p.reply_message_id NOT IN ({placeholders})")
            params.extend(exclude_reply_ids)
        params.append(limit)
        with self._connect() as connection:
            rows = connection.execute(
                f"""
                SELECT
                    p.reply_message_id,
                    p.inbound_message_id,
                    p.conversation_id,
                    p.correspondent_email,
                    p.subject,
                    p.inbound_received_timestamp,
                    p.reply_received_timestamp,
                    p.inbound_text,
                    p.reply_text,
                    p.pairing_source,
                    p.pairing_confidence,
                    p.updated_at
                FROM style_examples_fts
                JOIN style_reply_pairs AS p
                  ON p.reply_message_id = style_examples_fts.reply_message_id
                WHERE {" AND ".join(clauses)}
                ORDER BY bm25(style_examples_fts), p.reply_received_timestamp DESC
                LIMIT ?
                """,
                params,
            ).fetchall()
        return [StylePairRecord(**dict(row)) for row in rows]

    def refresh_eval_holdout(self, *, holdout_days: int, limit: int) -> int:
        cutoff = datetime.now(timezone.utc) - timedelta(days=holdout_days)
        now = datetime.now(timezone.utc).isoformat()
        with self._connect() as connection:
            connection.execute("DELETE FROM style_eval_examples WHERE split = 'holdout'")
            rows = connection.execute(
                """
                SELECT reply_message_id
                FROM style_reply_pairs
                WHERE reply_received_timestamp >= ?
                ORDER BY reply_received_timestamp DESC
                LIMIT ?
                """,
                (cutoff.isoformat(), limit),
            ).fetchall()
            for row in rows:
                connection.execute(
                    """
                    INSERT INTO style_eval_examples (reply_message_id, split, updated_at)
                    VALUES (?, 'holdout', ?)
                    ON CONFLICT(reply_message_id) DO UPDATE SET
                        split = excluded.split,
                        updated_at = excluded.updated_at
                    """,
                    (row["reply_message_id"], now),
                )
        return len(rows)

    def load_eval_cases(self, *, limit: int) -> list[StyleEvalCase]:
        with self._connect() as connection:
            rows = connection.execute(
                """
                SELECT
                    p.reply_message_id,
                    p.correspondent_email,
                    p.subject,
                    p.inbound_text,
                    p.reply_text AS actual_reply_text,
                    p.reply_received_timestamp
                FROM style_eval_examples AS e
                JOIN style_reply_pairs AS p
                  ON p.reply_message_id = e.reply_message_id
                WHERE e.split = 'holdout'
                ORDER BY p.reply_received_timestamp DESC
                LIMIT ?
                """,
                (limit,),
            ).fetchall()
        return [StyleEvalCase(**dict(row)) for row in rows]

    def record_eval_result(
        self,
        *,
        run_id: str,
        reply_message_id: str,
        correspondent_email: str,
        subject: str,
        generated_reply: str,
        actual_reply: str,
        style_example_ids: Sequence[str],
        generation_system_prompt: str,
        generation_user_prompt: str,
        grading_system_prompt: str,
        grading_user_prompt: str,
        tone_match: int,
        brevity_match: int,
        commitment_safety: int,
        clarity: int,
        overall: int,
        notes: str,
        model: str,
        reasoning_effort: str | None,
    ) -> None:
        now = datetime.now(timezone.utc).isoformat()
        with self._connect() as connection:
            connection.execute(
                """
                INSERT INTO style_eval_results (
                    run_id,
                    reply_message_id,
                    correspondent_email,
                    subject,
                    generated_reply,
                    actual_reply,
                    style_example_ids,
                    generation_system_prompt,
                    generation_user_prompt,
                    grading_system_prompt,
                    grading_user_prompt,
                    tone_match,
                    brevity_match,
                    commitment_safety,
                    clarity,
                    overall,
                    notes,
                    model,
                    reasoning_effort,
                    updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    run_id,
                    reply_message_id,
                    correspondent_email,
                    subject,
                    generated_reply,
                    actual_reply,
                    json.dumps(list(style_example_ids)),
                    generation_system_prompt,
                    generation_user_prompt,
                    grading_system_prompt,
                    grading_user_prompt,
                    tone_match,
                    brevity_match,
                    commitment_safety,
                    clarity,
                    overall,
                    notes,
                    model,
                    reasoning_effort,
                    now,
                ),
            )


class StyleCorpusSyncer:
    def __init__(
        self,
        graph_client: GraphClient,
        store: StyleCorpusStore,
        *,
        identity: IdentityConfig,
        config: StyleCorpusConfig,
    ) -> None:
        self.graph_client = graph_client
        self.store = store
        self.identity = identity
        self.config = config
        self.logger = logging.getLogger("edraft.style_corpus.sync")
        self._identity_aliases = self._build_identity_aliases(identity)
        self._conversation_cache: dict[str, list[MailboxMessage]] = {}

    def sync(self) -> CorpusSyncReport:
        report = CorpusSyncReport()
        for folder in self.config.source_folders:
            remaining = self.config.sync_max_messages - report.scanned
            if remaining <= 0:
                break
            for message in self.graph_client.iter_messages(
                folder=folder,
                unread_only=False,
                limit=remaining,
                include_body=True,
            ):
                report.scanned += 1
                try:
                    if not self._is_candidate_reply(message, folder):
                        report.skipped += 1
                        continue
                    reply_text = _message_text(message)
                    if len(reply_text) < self.config.min_reply_chars:
                        self.store.delete_pair(message.id)
                        report.skipped += 1
                        continue
                    inbound_message = self._find_prior_inbound_message(message)
                    if inbound_message is None:
                        self.store.delete_pair(message.id)
                        report.skipped += 1
                        continue
                    inbound_text = _message_text(inbound_message)
                    if not inbound_text:
                        self.store.delete_pair(message.id)
                        report.skipped += 1
                        continue
                    confidence = self._pair_confidence(inbound_message, message)
                    if confidence < self.config.min_pair_confidence:
                        self.store.delete_pair(message.id)
                        report.skipped += 1
                        continue
                    self.store.upsert_message(inbound_message, folder="conversation", direction="inbound")
                    self.store.upsert_message(message, folder=folder, direction="outbound")
                    self.store.upsert_pair(
                        inbound_message=inbound_message,
                        reply_message=message,
                        correspondent_email=inbound_message.sender_address,
                        pairing_source="latest_prior_inbound",
                        pairing_confidence=confidence,
                    )
                    report.paired += 1
                except Exception:
                    report.errors += 1
                    self.logger.exception(
                        "Style corpus sync failed for message",
                        extra={"event": "style_corpus_sync_error", "message_id": message.id},
                    )
        self.logger.info(
            "Style corpus sync completed",
            extra={
                "event": "style_corpus_sync_complete",
                "scanned": report.scanned,
                "paired": report.paired,
                "skipped": report.skipped,
                "errors": report.errors,
            },
        )
        return report

    def _is_candidate_reply(self, message: MailboxMessage, folder: str) -> bool:
        if message.is_draft or not message.received_at:
            return False
        if folder.casefold() == "sentitems":
            if not message.all_recipients():
                return False
            if self._has_reply_headers(message):
                return True
            if self._has_reply_subject(message):
                return True
            return self._has_quoted_reply_markers(message)
        sender = message.sender_address
        if not sender:
            return False
        if sender.casefold() == self.identity.email.casefold():
            return True
        normalized = self._normalize_identifier(sender.split("@", 1)[0])
        return normalized in self._identity_aliases

    @staticmethod
    def _has_reply_headers(message: MailboxMessage) -> bool:
        return any(name in message.headers for name in REPLY_HEADER_NAMES)

    @staticmethod
    def _has_reply_subject(message: MailboxMessage) -> bool:
        subject = message.subject.casefold().strip()
        return subject.startswith(REPLY_SUBJECT_PREFIXES)

    @staticmethod
    def _has_quoted_reply_markers(message: MailboxMessage) -> bool:
        content = (message.body_content or "").casefold()
        if not content:
            return False
        markers = (
            "divrplyfwdmsg",
            "-----original message-----",
            "get outlook for ios",
            "get outlook for android",
            "sent from my iphone",
        )
        if any(marker in content for marker in markers):
            return True
        if " on " in content and " wrote:" in content:
            return True
        return "from:" in content and "sent:" in content

    def _find_prior_inbound_message(self, reply_message: MailboxMessage) -> MailboxMessage | None:
        if not reply_message.conversation_id or not reply_message.received_at:
            return None
        if reply_message.conversation_id in self._conversation_cache:
            related = [
                message
                for message in self._conversation_cache[reply_message.conversation_id]
                if message.id != reply_message.id
            ]
        else:
            related = self.graph_client.list_conversation_messages(
                reply_message.conversation_id,
                exclude_message_id=reply_message.id,
                limit=20,
            )
            self._conversation_cache[reply_message.conversation_id] = [*related, reply_message]
        candidates = [
            message
            for message in related
            if not message.is_draft
            and message.received_at is not None
            and message.received_at < reply_message.received_at
            and message.sender_address
            and not self._is_self_email(message.sender_address)
        ]
        if not candidates:
            return None
        candidates.sort(key=lambda item: item.received_at or datetime.min.replace(tzinfo=timezone.utc))
        return candidates[-1]

    def _pair_confidence(self, inbound_message: MailboxMessage, reply_message: MailboxMessage) -> float:
        confidence = 0.0
        if inbound_message.conversation_id and inbound_message.conversation_id == reply_message.conversation_id:
            confidence += 0.5
        if inbound_message.received_at and reply_message.received_at and inbound_message.received_at < reply_message.received_at:
            confidence += 0.2
        if inbound_message.sender_address and any(
            recipient.address == inbound_message.sender_address
            for recipient in reply_message.all_recipients()
        ):
            confidence += 0.2
        if inbound_message.sender_address:
            confidence += 0.1
        return min(confidence, 1.0)

    def _is_self_email(self, email_address: str) -> bool:
        if email_address.casefold() == self.identity.email.casefold():
            return True
        return self._normalize_identifier(email_address.split("@", 1)[0]) in self._identity_aliases

    @staticmethod
    def _normalize_identifier(value: str) -> str:
        return "".join(character for character in value.casefold() if character.isalnum())

    def _build_identity_aliases(self, identity: IdentityConfig) -> set[str]:
        aliases = set()
        local_part = identity.email.split("@", 1)[0]
        name_parts = [self._normalize_identifier(part) for part in identity.name.split() if part]
        first = name_parts[0] if name_parts else ""
        last = name_parts[-1] if len(name_parts) >= 2 else ""

        for raw in {identity.name, local_part}:
            normalized = self._normalize_identifier(raw)
            if normalized:
                aliases.add(normalized)

        if first:
            aliases.add(first)
        if last:
            aliases.add(last)
        if first and last:
            aliases.update(
                {
                    f"{first}{last}",
                    f"{first[:1]}{last}",
                    f"{last}{first}",
                    f"{last}{first[:1]}",
                }
            )
        return aliases


class StyleExampleRetriever:
    def __init__(self, store: StyleCorpusStore, config: StyleCorpusConfig) -> None:
        self.store = store
        self.config = config

    def retrieve(
        self,
        message: MailboxMessage,
        thread_context: ThreadContext,
        *,
        exclude_reply_ids: Sequence[str] = (),
        reply_received_before: datetime | None = None,
    ) -> list[StyleExample]:
        seen = set(exclude_reply_ids)
        reference_time = reply_received_before or message.received_at or datetime.now(timezone.utc)
        candidates: dict[str, RetrievalCandidate] = {}

        if self.config.retrieve_same_sender_first and message.sender_address:
            for pair in self.store.find_pairs_by_correspondent(
                message.sender_address,
                limit=max(self.config.max_examples * 4, 8),
                exclude_reply_ids=tuple(seen),
                reply_received_before=reply_received_before,
            ):
                if pair.reply_message_id in seen:
                    continue
                candidates[pair.reply_message_id] = RetrievalCandidate(
                    pair=pair,
                    source="same_sender",
                    source_priority=2,
                )
                seen.add(pair.reply_message_id)

        if self.config.fts_fallback_enabled:
            query_text = self._build_query_text(message, thread_context)
            for query_rank, pair in enumerate(
                self.store.search_pairs(
                    query_text,
                    limit=max(self.config.max_examples * 5, 10),
                    exclude_reply_ids=tuple(exclude_reply_ids),
                    reply_received_before=reply_received_before,
                )
            ):
                if pair.reply_message_id in exclude_reply_ids:
                    continue
                candidate = candidates.get(pair.reply_message_id)
                if candidate is None:
                    candidate = RetrievalCandidate(
                        pair=pair,
                        source="similar_conversation",
                        source_priority=1,
                    )
                    candidates[pair.reply_message_id] = candidate
                candidate.query_rank = (
                    query_rank
                    if candidate.query_rank is None
                    else min(candidate.query_rank, query_rank)
                )

        ranked_candidates = sorted(
            candidates.values(),
            key=lambda candidate: self._sort_key(candidate, reference_time=reference_time),
            reverse=True,
        )
        return [
            self._to_example(candidate.pair, source=candidate.source)
            for candidate in ranked_candidates[: self.config.max_examples]
        ]

    def _to_example(self, pair: StylePairRecord, *, source: str) -> StyleExample:
        return StyleExample(
            reply_message_id=pair.reply_message_id,
            correspondent_email=pair.correspondent_email,
            subject=pair.subject,
            inbound_text=truncate_text(pair.inbound_text, self.config.max_example_chars),
            reply_text=truncate_text(pair.reply_text, self.config.max_example_chars),
            source=source,
        )

    def _sort_key(
        self,
        candidate: RetrievalCandidate,
        *,
        reference_time: datetime,
    ) -> tuple[float, int, float, float]:
        reply_timestamp = parse_datetime(candidate.pair.reply_received_timestamp)
        return (
            self._retrieval_score(candidate, reference_time=reference_time),
            candidate.source_priority,
            self._query_rank_score(candidate.query_rank),
            reply_timestamp.timestamp() if reply_timestamp is not None else float("-inf"),
        )

    def _retrieval_score(
        self,
        candidate: RetrievalCandidate,
        *,
        reference_time: datetime,
    ) -> float:
        # Favor sender-specific examples, but let stronger/high-confidence matches
        # outrank weak examples from the same correspondent.
        score = candidate.pair.pairing_confidence * self.config.pairing_confidence_weight
        if candidate.source == "same_sender":
            score += self.config.same_sender_boost
        score += self._query_rank_score(candidate.query_rank)

        reply_timestamp = parse_datetime(candidate.pair.reply_received_timestamp)
        if reply_timestamp is not None:
            age_days = max((reference_time - reply_timestamp).total_seconds() / 86400, 0.0)
            recency_fraction = max(
                0.0,
                1.0 - min(age_days, float(self.config.recency_decay_days)) / float(self.config.recency_decay_days),
            )
            score += self.config.recency_max_bonus * recency_fraction
        return score

    def _query_rank_score(self, query_rank: int | None) -> float:
        if query_rank is None:
            return 0.0
        return max(0.0, self.config.query_rank_max_bonus - (query_rank * self.config.query_rank_step_penalty))

    @staticmethod
    def _build_query_text(message: MailboxMessage, thread_context: ThreadContext) -> str:
        parts = [message.subject, _message_text(message)]
        for related in thread_context.related_messages[-2:]:
            parts.append(related.subject)
            parts.append(truncate_text(_message_text(related), 400))
        return "\n".join(part for part in parts if part)


def _message_text(message: MailboxMessage) -> str:
    extracted = extract_authored_email_text(message.body_content, message.body_content_type).strip()
    if extracted:
        return extracted
    if not message.body_content:
        return (message.body_preview or "").strip()
    return ""


def _build_fts_query(query_text: str) -> str:
    tokens = []
    for token in re.findall(r"[a-z0-9]{3,}", query_text.casefold()):
        if token in STOPWORDS:
            continue
        if token not in tokens:
            tokens.append(token)
        if len(tokens) >= 8:
            break
    return " OR ".join(f"{token}*" for token in tokens)
