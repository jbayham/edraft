from __future__ import annotations

import json
import logging
from datetime import datetime, timedelta, timezone

from edraft.config import AppConfig
from edraft.draft_creator import DraftCreator
from edraft.draft_generator import DraftGenerationError, DraftGenerator
from edraft.filters import MessageFilter
from edraft.graph_client import GraphClient
from edraft.message_fetcher import MessageFetcher
from edraft.models import MailboxMessage, MeetingSuggestion, ScanReport, ThreadContext
from edraft.scheduling import MeetingReplyPlanner
from edraft.state_store import StateStore
from edraft.style_corpus import StyleExampleRetriever
from edraft.thread_context import ThreadContextBuilder


class InboxScanner:
    def __init__(
        self,
        *,
        config: AppConfig,
        graph_client: GraphClient,
        state_store: StateStore,
        draft_generator: DraftGenerator | None,
        style_retriever: StyleExampleRetriever | None = None,
    ) -> None:
        self.config = config
        self.graph_client = graph_client
        self.state_store = state_store
        self.draft_generator = draft_generator
        self.style_retriever = style_retriever
        self.filter = MessageFilter(config.filters, config.identity)
        self.fetcher = MessageFetcher(
            graph_client,
            processed_category=config.scan.processed_category,
        )
        self.thread_builder = ThreadContextBuilder(
            graph_client,
            max_messages=config.scan.thread_context_messages,
        )
        self.meeting_planner = (
            MeetingReplyPlanner(graph_client, config.scheduling, config.identity)
            if config.scheduling.enabled
            else None
        )
        self.draft_creator = DraftCreator(
            graph_client,
            reply_mode=config.scan.reply_mode,
            identity=config.identity,
        )
        self.logger = logging.getLogger("edraft.scanner")

    def scan_once(self, *, dry_run: bool | None = None) -> ScanReport:
        effective_dry_run = self.config.scan.dry_run if dry_run is None else dry_run
        report = ScanReport()
        received_after = datetime.now(timezone.utc) - timedelta(
            hours=self.config.scan.max_message_age_hours
        )
        summaries = self.fetcher.fetch_unread_messages(
            folders=self.config.scan.folders,
            unread_only=self.config.scan.scan_unread_only,
            max_messages=self.config.scan.max_messages_per_scan,
            received_after=received_after,
        )
        for summary in summaries:
            report.examined += 1
            if self.state_store.has_terminal_record(summary.id):
                report.skipped += 1
                self.logger.info(
                    "Skipping already processed message",
                    extra={"event": "already_processed", "message_id": summary.id},
                )
                continue
            try:
                message = self.graph_client.get_message(summary.id)
                self._process_message(message, dry_run=effective_dry_run, report=report)
            except Exception as exc:
                report.errors += 1
                self.logger.exception(
                    "Message processing failed",
                    extra={"event": "message_error", "message_id": summary.id},
                )
                if not effective_dry_run:
                    self.state_store.record_action(
                        source_message_id=summary.id,
                        conversation_id=summary.conversation_id,
                        subject=summary.subject,
                        received_timestamp=summary.received_at.isoformat() if summary.received_at else None,
                        action="error",
                        reason=str(exc),
                    )
        return report

    def inspect_message(self, message_id: str) -> dict[str, object]:
        message = self.graph_client.get_message(message_id)
        decision = self.filter.evaluate(message)
        context = self.thread_builder.build(message)
        meeting_plan = None
        if self.meeting_planner is not None:
            meeting_plan = self.meeting_planner.plan(message, context)
        state = self.state_store.get_record(message_id)
        meeting_state = self.state_store.get_meeting_suggestion(message_id)
        return {
            "message": {
                "id": message.id,
                "conversation_id": message.conversation_id,
                "subject": message.subject,
                "from": message.sender_address,
                "to": [recipient.address for recipient in message.to_recipients],
                "cc": [recipient.address for recipient in message.cc_recipients],
                "received_at": message.received_at.isoformat() if message.received_at else None,
                "headers": message.headers,
            },
            "filter_decision": decision.to_dict(),
            "state": state.__dict__ if state else None,
            "meeting_analysis": meeting_plan.to_dict() if meeting_plan else None,
            "meeting_suggestion": meeting_state.__dict__ if meeting_state else None,
            "thread_context": [
                {
                    "id": item.id,
                    "subject": item.subject,
                    "from": item.sender_address,
                    "received_at": item.received_at.isoformat() if item.received_at else None,
                }
                for item in context.related_messages
            ],
        }

    def _process_message(self, message: MailboxMessage, *, dry_run: bool, report: ScanReport) -> None:
        if self.state_store.has_terminal_record(message.id):
            report.skipped += 1
            self.logger.info(
                "Skipping already processed message",
                extra={"event": "already_processed", "message_id": message.id},
            )
            return

        minimal_context = ThreadContext(conversation_id=message.conversation_id, related_messages=[])
        meeting_intent = (
            self.meeting_planner.intent_analyzer.analyze(message, minimal_context)
            if self.meeting_planner is not None
            else None
        )
        decision = self.filter.evaluate(message)
        if not decision.should_draft and not self._should_override_for_scheduling(decision, meeting_intent):
            report.skipped += 1
            self.logger.info(
                "Skipping message",
                extra={
                    "event": "message_skipped",
                    "message_id": message.id,
                    "reason": decision.primary_reason,
                    "signals": json.dumps(decision.matched_signals),
                },
            )
            if not dry_run:
                self.state_store.record_action(
                    source_message_id=message.id,
                    conversation_id=message.conversation_id,
                    subject=message.subject,
                    received_timestamp=message.received_at.isoformat() if message.received_at else None,
                    action="skipped",
                    reason=f"{decision.primary_reason} | score={decision.score}",
                )
                self._apply_processed_category_if_needed(message)
            return

        context = self.thread_builder.build(message)
        meeting_plan = (
            self.meeting_planner.plan(message, context, intent=meeting_intent)
            if self.meeting_planner is not None and meeting_intent is not None and meeting_intent.is_meeting_request
            else None
        )
        style_examples = (
            self.style_retriever.retrieve(message, context)
            if self.style_retriever is not None and self.config.style_corpus.enabled
            else []
        )
        if self.draft_generator is None:
            raise DraftGenerationError("Draft generator is not configured.")
        draft_text = self.draft_generator.generate(
            message,
            context,
            style_examples,
            meeting_plan=meeting_plan.plan if meeting_plan is not None else None,
        )

        if dry_run:
            report.drafted += 1
            self.logger.info(
                "Dry run would create draft",
                extra={
                    "event": "draft_would_create",
                    "message_id": message.id,
                    "style_example_ids": json.dumps([example.reply_message_id for example in style_examples]),
                    "meeting_plan": json.dumps(meeting_plan.to_dict() if meeting_plan is not None else None),
                    "preview": draft_text[:200],
                },
            )
            return

        draft = self.draft_creator.create(message, draft_text)
        self.state_store.record_action(
            source_message_id=message.id,
            conversation_id=message.conversation_id,
            subject=message.subject,
            received_timestamp=message.received_at.isoformat() if message.received_at else None,
            action="drafted",
            reason="reply_draft_created",
            created_draft_id=draft.id,
        )
        if meeting_plan is not None:
            self.state_store.record_meeting_suggestion(
                MeetingSuggestion(
                    source_message_id=message.id,
                    conversation_id=message.conversation_id,
                    subject=message.subject,
                    sender_email=message.sender_address,
                    intent=meeting_plan.plan.intent.to_dict(),
                    query_start=meeting_plan.plan.query_start.isoformat() if meeting_plan.plan.query_start else None,
                    query_end=meeting_plan.plan.query_end.isoformat() if meeting_plan.plan.query_end else None,
                    suggested_slots=[slot.to_dict() for slot in meeting_plan.candidate_slots],
                    generated_reply=draft_text,
                    updated_at=datetime.now(timezone.utc).isoformat(),
                )
            )
        self._apply_processed_category_if_needed(message)
        report.drafted += 1
        self.logger.info(
            "Created Outlook draft reply",
            extra={
                "event": "draft_created",
                "message_id": message.id,
                "draft_id": draft.id,
                "style_example_ids": json.dumps([example.reply_message_id for example in style_examples]),
                "meeting_plan": json.dumps(meeting_plan.to_dict() if meeting_plan is not None else None),
                "draft_web_link": draft.web_link,
            },
        )

    def _apply_processed_category_if_needed(self, message: MailboxMessage) -> None:
        category = self.config.scan.processed_category
        if not category or not self.config.scan.apply_processed_category:
            return
        self.graph_client.add_category_to_message(message.id, category, message.categories)

    @staticmethod
    def _should_override_for_scheduling(
        decision: object,
        meeting_intent: object | None,
    ) -> bool:
        if meeting_intent is None:
            return False
        if not getattr(meeting_intent, "is_meeting_request", False):
            return False
        return getattr(decision, "primary_reason", "") == "not_confidently_addressed_to_me"
