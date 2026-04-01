from __future__ import annotations

import os
import tomllib
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from dotenv import load_dotenv


DEFAULT_CONFIG_PATH = Path("config/edraft.toml")


class ConfigError(RuntimeError):
    """Raised when edraft configuration is invalid."""


@dataclass(slots=True)
class IdentityConfig:
    name: str
    email: str


@dataclass(slots=True)
class ScanConfig:
    folders: list[str] = field(default_factory=lambda: ["inbox"])
    scan_unread_only: bool = True
    max_messages_per_scan: int = 25
    thread_context_messages: int = 5
    reply_mode: str = "reply"
    dry_run: bool = False
    processed_category: str | None = None
    apply_processed_category: bool = False


@dataclass(slots=True)
class FilterConfig:
    exclude_newsletters: bool = True
    exclude_automated: bool = True
    exclude_cc_only: bool = True
    require_direct_address: bool = True
    newsletter_header_detection: bool = True
    sender_patterns: list[str] = field(default_factory=list)
    domain_patterns: list[str] = field(default_factory=list)
    group_alias_patterns: list[str] = field(default_factory=list)
    max_direct_recipients: int = 4
    address_score_threshold: int = 2


@dataclass(slots=True)
class LLMConfig:
    provider: str = "openai"
    model: str = "gpt-4o-mini"
    style_instructions: str = (
        "Write concise, professional replies. Prefer short responses unless "
        "more detail is clearly needed. Do not invent facts, ask a clarifying "
        "question when context is insufficient, and do not over-commit."
    )
    signature_block: str | None = None
    max_input_chars: int = 12000


DEFAULT_STYLE_INSTRUCTIONS = (
    "Write concise, professional replies. Prefer short responses unless "
    "more detail is clearly needed. Do not invent facts, ask a clarifying "
    "question when context is insufficient, and do not over-commit."
)


@dataclass(slots=True)
class StateConfig:
    database_path: Path


@dataclass(slots=True)
class LoggingConfig:
    level: str = "INFO"
    format: str = "json"


@dataclass(slots=True)
class AppConfig:
    identity: IdentityConfig
    scan: ScanConfig
    filters: FilterConfig
    llm: LLMConfig
    state: StateConfig
    logging: LoggingConfig
    source_path: Path


def _resolve_path(raw: str, cwd: Path) -> Path:
    path = Path(raw).expanduser()
    if not path.is_absolute():
        path = cwd / path
    return path


def _coerce_list(raw: Any, field_name: str) -> list[str]:
    if raw is None:
        return []
    if not isinstance(raw, list) or not all(isinstance(item, str) for item in raw):
        raise ConfigError(f"{field_name} must be a list of strings")
    return raw


def load_app_config(path: Path | None = None) -> AppConfig:
    load_dotenv()
    config_path = path or Path(os.getenv("EDRAFT_CONFIG", DEFAULT_CONFIG_PATH))
    if not config_path.exists():
        raise ConfigError(
            f"Config file not found at {config_path}. Copy config/edraft.example.toml "
            "to config/edraft.toml and customize it."
        )
    with config_path.open("rb") as handle:
        raw = tomllib.load(handle)
    return parse_app_config(raw, source_path=config_path)


def parse_app_config(raw: dict[str, Any], source_path: Path) -> AppConfig:
    try:
        identity_raw = raw["identity"]
    except KeyError as exc:
        raise ConfigError("Missing [identity] section in config file") from exc

    identity = IdentityConfig(
        name=str(identity_raw["name"]).strip(),
        email=str(identity_raw["email"]).strip().lower(),
    )
    if not identity.name or not identity.email:
        raise ConfigError("identity.name and identity.email must be non-empty")

    scan_raw = raw.get("scan", {})
    reply_mode = str(scan_raw.get("reply_mode", "reply")).strip().lower()
    if reply_mode not in {"reply", "reply_all"}:
        raise ConfigError("scan.reply_mode must be 'reply' or 'reply_all'")
    processed_category = str(scan_raw.get("processed_category", "")).strip() or None
    scan = ScanConfig(
        folders=_coerce_list(scan_raw.get("folders", ["inbox"]), "scan.folders") or ["inbox"],
        scan_unread_only=bool(scan_raw.get("scan_unread_only", True)),
        max_messages_per_scan=int(scan_raw.get("max_messages_per_scan", 25)),
        thread_context_messages=int(scan_raw.get("thread_context_messages", 5)),
        reply_mode=reply_mode,
        dry_run=bool(scan_raw.get("dry_run", False)),
        processed_category=processed_category,
        apply_processed_category=bool(scan_raw.get("apply_processed_category", False)),
    )

    filter_raw = raw.get("filters", {})
    filters = FilterConfig(
        exclude_newsletters=bool(filter_raw.get("exclude_newsletters", True)),
        exclude_automated=bool(filter_raw.get("exclude_automated", True)),
        exclude_cc_only=bool(filter_raw.get("exclude_cc_only", True)),
        require_direct_address=bool(filter_raw.get("require_direct_address", True)),
        newsletter_header_detection=bool(filter_raw.get("newsletter_header_detection", True)),
        sender_patterns=_coerce_list(filter_raw.get("sender_patterns", []), "filters.sender_patterns"),
        domain_patterns=_coerce_list(filter_raw.get("domain_patterns", []), "filters.domain_patterns"),
        group_alias_patterns=_coerce_list(
            filter_raw.get("group_alias_patterns", []),
            "filters.group_alias_patterns",
        ),
        max_direct_recipients=int(filter_raw.get("max_direct_recipients", 4)),
        address_score_threshold=int(filter_raw.get("address_score_threshold", 2)),
    )

    llm_raw = raw.get("llm", {})
    llm = LLMConfig(
        provider=str(llm_raw.get("provider", "openai")).strip().lower(),
        model=str(llm_raw.get("model", "gpt-4o-mini")).strip(),
        style_instructions=str(llm_raw.get("style_instructions", DEFAULT_STYLE_INSTRUCTIONS)),
        signature_block=(str(llm_raw["signature_block"]) if "signature_block" in llm_raw else None),
        max_input_chars=int(llm_raw.get("max_input_chars", 12000)),
    )

    state_raw = raw.get("state", {})
    database_raw = str(state_raw.get("database_path", "./data/edraft.sqlite3"))
    state = StateConfig(database_path=_resolve_path(database_raw, cwd=Path.cwd()))

    logging_raw = raw.get("logging", {})
    logging = LoggingConfig(
        level=str(logging_raw.get("level", "INFO")).strip().upper(),
        format=str(logging_raw.get("format", "json")).strip().lower(),
    )

    return AppConfig(
        identity=identity,
        scan=scan,
        filters=filters,
        llm=llm,
        state=state,
        logging=logging,
        source_path=source_path,
    )
