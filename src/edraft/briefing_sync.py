from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone

from edraft.config import AppConfig
from edraft.email_cache import EmailCacheStore
from edraft.graph_client import GraphClient


@dataclass(slots=True)
class BriefingSyncReport:
    scanned: int = 0
    skipped_fresh: int = 0
    errors: int = 0

    def to_dict(self) -> dict[str, int]:
        return {
            "scanned": self.scanned,
            "skipped_fresh": self.skipped_fresh,
            "errors": self.errors,
        }


class BriefingEmailSyncer:
    def __init__(
        self,
        *,
        graph_client: GraphClient,
        store: EmailCacheStore,
        config: AppConfig,
    ) -> None:
        self.graph_client = graph_client
        self.store = store
        self.config = config
        self.logger = logging.getLogger("edraft.briefing.sync")

    def sync_recent_email(self, *, force: bool = False) -> BriefingSyncReport:
        report = BriefingSyncReport()
        cutoff = datetime.now(timezone.utc) - timedelta(
            days=self.config.briefing.email_lookback_days
        )
        inbound_folders = self.config.scan.folders or ["inbox"]
        for folder in inbound_folders:
            self._sync_folder(
                folder=folder,
                direction="inbound",
                cutoff=cutoff,
                date_field="receivedDateTime",
                force=force,
                report=report,
            )
        self._sync_folder(
            folder="sentitems",
            direction="outbound",
            cutoff=cutoff,
            date_field="sentDateTime",
            force=force,
            report=report,
        )
        self.logger.info(
            "Briefing email sync completed",
            extra={
                "event": "briefing_email_sync_complete",
                "scanned": report.scanned,
                "skipped_fresh": report.skipped_fresh,
                "errors": report.errors,
            },
        )
        return report

    def _sync_folder(
        self,
        *,
        folder: str,
        direction: str,
        cutoff: datetime,
        date_field: str,
        force: bool,
        report: BriefingSyncReport,
    ) -> None:
        if not force and self.store.is_sync_fresh(
            folder=folder,
            direction=direction,
            lookback_days=self.config.briefing.email_lookback_days,
            freshness_minutes=self.config.briefing.sync_freshness_minutes,
        ):
            report.skipped_fresh += 1
            return
        try:
            for message in self.graph_client.iter_messages(
                folder=folder,
                unread_only=False,
                limit=self.config.briefing.max_emails,
                received_after=cutoff,
                include_body=True,
                date_field=date_field,
            ):
                self.store.upsert_message(
                    message,
                    source_folder=folder,
                    direction=direction,
                )
                report.scanned += 1
            self.store.record_sync(
                folder=folder,
                direction=direction,
                lookback_days=self.config.briefing.email_lookback_days,
                success=True,
            )
        except Exception as exc:
            report.errors += 1
            self.store.record_sync(
                folder=folder,
                direction=direction,
                lookback_days=self.config.briefing.email_lookback_days,
                success=False,
                error=str(exc),
            )
            self.logger.exception(
                "Briefing email sync failed",
                extra={
                    "event": "briefing_email_sync_error",
                    "folder": folder,
                    "direction": direction,
                },
            )
