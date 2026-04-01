from __future__ import annotations

from bs4 import BeautifulSoup

from edraft.config import IdentityConfig
from edraft.graph_client import GraphClient
from edraft.models import DraftResult, MailboxMessage, Recipient
from edraft.text_utils import plain_text_to_html


class DraftCreator:
    """Create reply drafts only.

    This module intentionally has no send-email behavior. It uses Graph reply-draft
    endpoints so the result stays in Outlook Drafts for manual review.
    """

    def __init__(
        self,
        graph_client: GraphClient,
        *,
        reply_mode: str,
        identity: IdentityConfig,
    ) -> None:
        self.graph_client = graph_client
        self.reply_mode = reply_mode
        self.identity = identity
        self._identity_aliases = self._build_identity_aliases(identity)

    def create(self, message: MailboxMessage, draft_text: str) -> DraftResult:
        reply_mode = self.resolve_reply_mode(message)
        draft = self.graph_client.create_reply_draft(
            source_message_id=message.id,
            comment=None,
            reply_mode=reply_mode,
        )
        draft_message = self.graph_client.get_message(draft.id)
        merged_html = self.merge_reply_html(
            reply_html=self.render_reply_html(draft_text),
            existing_html=draft_message.body_content,
        )
        self.graph_client.update_message_body_html(draft.id, merged_html)
        return draft

    @staticmethod
    def render_reply_html(draft_text: str) -> str:
        body_html = plain_text_to_html(draft_text)
        return f'<div class="edraft-reply">{body_html}</div><div><br></div>'

    @staticmethod
    def merge_reply_html(*, reply_html: str, existing_html: str) -> str:
        soup = BeautifulSoup(existing_html or "<html><body></body></html>", "html.parser")
        body = soup.body or soup

        separator = body.find(id="divRplyFwdMsg")
        if separator is not None:
            insertion_anchor = separator
            previous = separator.previous_sibling
            while previous is not None and isinstance(previous, str) and previous.strip() == "":
                candidate = previous
                previous = previous.previous_sibling
                candidate.extract()
            if previous is not None and getattr(previous, "name", None) == "hr":
                insertion_anchor = previous

            node = body.contents[0] if body.contents else None
            while node is not None and node is not insertion_anchor:
                next_node = node.next_sibling
                node.extract()
                node = next_node
        else:
            body.clear()

        fragment = BeautifulSoup(reply_html, "html.parser")
        nodes = [child.extract() for child in list(fragment.contents)]
        anchor_index = body.contents.index(insertion_anchor) if separator is not None else 0
        for offset, node in enumerate(nodes):
            body.insert(anchor_index + offset, node)

        return str(soup)

    def resolve_reply_mode(self, message: MailboxMessage) -> str:
        if self.reply_mode != "auto":
            return self.reply_mode
        other_recipients = {
            recipient.address
            for recipient in [*message.to_recipients, *message.cc_recipients]
            if not self._is_self_recipient(recipient)
        }
        return "reply_all" if other_recipients else "reply"

    def _is_self_recipient(self, recipient: Recipient) -> bool:
        if recipient.matches(self.identity.email):
            return True
        local_part = recipient.address.split("@", 1)[0]
        normalized_candidates = {
            self._normalize_identifier(recipient.name),
            self._normalize_identifier(local_part),
        }
        return any(candidate and candidate in self._identity_aliases for candidate in normalized_candidates)

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
