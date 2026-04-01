from __future__ import annotations

import re

from edraft.config import FilterConfig, IdentityConfig
from edraft.models import FilterDecision, MailboxMessage
from edraft.text_utils import to_prompt_text, truncate_text


GREETING_PREFIXES = ("hi", "hello", "dear", "hey")


class MessageFilter:
    def __init__(self, config: FilterConfig, identity: IdentityConfig) -> None:
        self.config = config
        self.identity = identity
        self._identity_tokens = self._build_identity_tokens(identity)
        self._identity_aliases = self._build_identity_aliases(identity)

    def evaluate(self, message: MailboxMessage) -> FilterDecision:
        reasons: list[str] = []
        signals: list[str] = []
        score = 0

        if self.config.exclude_automated:
            automated_signals = self._automated_signals(message)
            if automated_signals:
                reasons.append("automated_or_no_reply")
                signals.extend(automated_signals)
                score -= 4

        newsletter_signals = self._newsletter_signals(message)
        if self.config.exclude_newsletters and newsletter_signals:
            reasons.append("newsletter_or_bulk")
            signals.extend(newsletter_signals)
            score -= 4

        if self._is_cc_only(message):
            signals.append("recipient:cc_only")
            score -= 4
            if self.config.exclude_cc_only:
                reasons.append("cc_only")

        direct_address_score, direct_signals = self._direct_address_score(
            message,
            penalize_for_newsletter=bool(newsletter_signals),
        )
        score += direct_address_score
        signals.extend(direct_signals)

        if self.config.require_direct_address and score < self.config.address_score_threshold:
            reasons.append("not_confidently_addressed_to_me")

        should_draft = not reasons
        primary_reason = "eligible" if should_draft else reasons[0]
        return FilterDecision(
            should_draft=should_draft,
            primary_reason=primary_reason,
            reasons=reasons,
            score=score,
            matched_signals=sorted(set(signals)),
        )

    def _automated_signals(self, message: MailboxMessage) -> list[str]:
        signals: list[str] = []
        sender = message.sender_address
        local_part = sender.split("@", 1)[0]
        if any(pattern.casefold() in local_part.casefold() for pattern in self.config.sender_patterns):
            signals.append("sender:no_reply_pattern")
        auto_submitted = message.headers.get("auto-submitted", "").lower()
        if auto_submitted and auto_submitted != "no":
            signals.append("header:auto_submitted")
        content_class = message.headers.get("content-class", "").lower()
        if "calendarmessage" in content_class:
            signals.append("header:calendar_message")
        subject = message.subject.lower()
        if subject.startswith(("automatic reply", "out of office", "accepted:", "declined:", "tentative:")):
            signals.append("subject:auto_reply")
        return signals

    def _newsletter_signals(self, message: MailboxMessage) -> list[str]:
        signals: list[str] = []
        sender = message.sender_address
        domain = sender.split("@", 1)[1] if "@" in sender else ""
        if any(pattern.casefold() in sender.casefold() for pattern in self.config.sender_patterns):
            signals.append("sender:pattern")
        if any(pattern.casefold() in domain.casefold() for pattern in self.config.domain_patterns):
            signals.append("sender:domain_pattern")
        if self.config.newsletter_header_detection:
            if "list-unsubscribe" in message.headers:
                signals.append("header:list_unsubscribe")
            if "list-id" in message.headers:
                signals.append("header:list_id")
            precedence = message.headers.get("precedence", "").lower()
            if precedence in {"bulk", "list", "junk"}:
                signals.append("header:precedence_bulk")
        return signals

    def _is_cc_only(self, message: MailboxMessage) -> bool:
        in_to = any(self._recipient_match_type(recipient) != "none" for recipient in message.to_recipients)
        in_cc = any(self._recipient_match_type(recipient) != "none" for recipient in message.cc_recipients)
        return in_cc and not in_to

    def _direct_address_score(
        self,
        message: MailboxMessage,
        *,
        penalize_for_newsletter: bool,
    ) -> tuple[int, list[str]]:
        score = 0
        signals: list[str] = []

        to_match = self._best_recipient_match_type(message.to_recipients)
        cc_match = self._best_recipient_match_type(message.cc_recipients)
        recipient_count = len(message.to_recipients) + len(message.cc_recipients)

        if to_match == "exact":
            score += 3
            signals.append("recipient:to")
        elif to_match == "alias":
            score += 2
            signals.append("recipient:to_alias")
        elif cc_match == "exact":
            score -= 3
            signals.append("recipient:cc")
        elif cc_match == "alias":
            score -= 3
            signals.append("recipient:cc_alias")
        else:
            score -= 2
            signals.append("recipient:not_found")

        if recipient_count <= self.config.max_direct_recipients:
            score += 1
            signals.append("recipient:few_total_recipients")
        elif to_match == "none":
            score -= 2
            signals.append("recipient:many_total_recipients")
        else:
            signals.append("recipient:many_total_recipients")

        salutation = self._find_direct_salutation(message)
        if salutation:
            score += 2
            signals.append(f"salutation:{salutation}")

        broad_alias = self._matches_group_alias(message)
        if broad_alias:
            score -= 2
            signals.append("recipient:group_alias")

        if penalize_for_newsletter:
            score -= 1

        return score, signals

    def _find_direct_salutation(self, message: MailboxMessage) -> str | None:
        text = truncate_text(to_prompt_text(message.body_content, message.body_content_type), 500).lower()
        lines = [line.strip(" ,:;") for line in text.splitlines() if line.strip()]
        candidates = lines[:3]
        for line in candidates:
            for prefix in GREETING_PREFIXES:
                if not line.startswith(prefix):
                    continue
                for token in self._identity_tokens:
                    pattern = rf"\b{re.escape(prefix)}\b[\s,]+{re.escape(token)}\b"
                    if re.search(pattern, line):
                        return token
        return None

    def _matches_group_alias(self, message: MailboxMessage) -> bool:
        all_addresses = [recipient.address for recipient in message.all_recipients()]
        return any(
            pattern.casefold() in address.casefold()
            for pattern in self.config.group_alias_patterns
            for address in all_addresses
        )

    @staticmethod
    def _build_identity_tokens(identity: IdentityConfig) -> list[str]:
        tokens = {identity.email.casefold()}
        name = identity.name.strip()
        if name:
            tokens.add(name.casefold())
            parts = [part.casefold() for part in name.split() if part]
            if parts:
                tokens.add(parts[0])
        local_part = identity.email.split("@", 1)[0].replace(".", " ").strip()
        if local_part:
            tokens.add(local_part.casefold())
            tokens.update(part.casefold() for part in local_part.split() if part)
        return sorted(token for token in tokens if token)

    @staticmethod
    def _normalize_identifier(value: str) -> str:
        return re.sub(r"[^a-z0-9]", "", value.casefold())

    def _best_recipient_match_type(self, recipients: list) -> str:
        result = "none"
        for recipient in recipients:
            match_type = self._recipient_match_type(recipient)
            if match_type == "exact":
                return "exact"
            if match_type == "alias":
                result = "alias"
        return result

    def _recipient_match_type(self, recipient) -> str:
        if recipient.matches(self.identity.email):
            return "exact"
        candidate_values = [recipient.name, recipient.address.split("@", 1)[0]]
        for value in candidate_values:
            normalized = self._normalize_identifier(value)
            if normalized and normalized in self._identity_aliases:
                return "alias"
        return "none"

    def _build_identity_aliases(self, identity: IdentityConfig) -> set[str]:
        aliases = set()
        local_part = identity.email.split("@", 1)[0]
        parts = [self._normalize_identifier(part) for part in identity.name.split() if part]
        first = parts[0] if parts else ""
        last = parts[-1] if len(parts) >= 2 else ""

        for value in {identity.name, local_part}:
            normalized = self._normalize_identifier(value)
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
        return {alias for alias in aliases if alias}
