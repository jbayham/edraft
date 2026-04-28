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
    max_message_age_hours: int = 24
    max_messages_per_scan: int = 25
    thread_context_messages: int = 5
    reply_mode: str = "auto"
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
    model: str = "gpt-5.4"
    reasoning_effort: str | None = "medium"
    style_instructions: str = (
        "Write concise, professional replies. Prefer short responses unless "
        "more detail is clearly needed. Do not invent facts, ask a clarifying "
        "question when context is insufficient, and do not over-commit."
    )
    signature_block: str | None = None
    max_input_chars: int = 12000


@dataclass(slots=True)
class StyleCorpusConfig:
    enabled: bool = True
    source_folders: list[str] = field(default_factory=lambda: ["sentitems"])
    sync_max_messages: int = 250
    retrieve_same_sender_first: bool = True
    fts_fallback_enabled: bool = True
    max_examples: int = 3
    max_example_chars: int = 280
    min_reply_chars: int = 40
    min_pair_confidence: float = 0.6
    pairing_confidence_weight: float = 1.5
    same_sender_boost: float = 0.75
    query_rank_max_bonus: float = 0.5
    query_rank_step_penalty: float = 0.05
    recency_max_bonus: float = 0.3
    recency_decay_days: int = 90
    eval_holdout_days: int = 30
    eval_max_cases: int = 10


@dataclass(slots=True)
class SchedulingConfig:
    enabled: bool = True
    lookahead_days: int = 7
    max_suggestions: int = 3
    default_duration_minutes: int = 30
    business_hours_start: str = "09:00"
    business_hours_end: str = "17:00"
    weekdays_only: bool = True
    minimum_buffer_minutes: int = 15
    slot_step_minutes: int = 30


@dataclass(slots=True)
class BriefingConfig:
    email_lookback_days: int = 7
    related_emails_per_event: int = 5
    max_emails: int = 100
    sync_freshness_minutes: int = 30
    timezone: str | None = None
    model: str | None = None
    cache_enabled: bool = True
    output_directory: Path | None = None


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
    style_corpus: StyleCorpusConfig
    scheduling: SchedulingConfig
    briefing: BriefingConfig
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


def _parse_hhmm(raw: str, field_name: str) -> tuple[int, int]:
    parts = raw.strip().split(":")
    if len(parts) != 2:
        raise ConfigError(f"{field_name} must use HH:MM format")
    hours = int(parts[0])
    minutes = int(parts[1])
    if not 0 <= hours <= 23 or not 0 <= minutes <= 59:
        raise ConfigError(f"{field_name} must use a valid HH:MM time")
    return hours, minutes


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
    reply_mode = str(scan_raw.get("reply_mode", "auto")).strip().lower()
    if reply_mode not in {"auto", "reply", "reply_all"}:
        raise ConfigError("scan.reply_mode must be 'auto', 'reply', or 'reply_all'")
    processed_category = str(scan_raw.get("processed_category", "")).strip() or None
    scan = ScanConfig(
        folders=_coerce_list(scan_raw.get("folders", ["inbox"]), "scan.folders") or ["inbox"],
        scan_unread_only=bool(scan_raw.get("scan_unread_only", True)),
        max_message_age_hours=int(scan_raw.get("max_message_age_hours", 24)),
        max_messages_per_scan=int(scan_raw.get("max_messages_per_scan", 25)),
        thread_context_messages=int(scan_raw.get("thread_context_messages", 5)),
        reply_mode=reply_mode,
        dry_run=bool(scan_raw.get("dry_run", False)),
        processed_category=processed_category,
        apply_processed_category=bool(scan_raw.get("apply_processed_category", False)),
    )
    if scan.max_message_age_hours <= 0:
        raise ConfigError("scan.max_message_age_hours must be greater than 0")

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
        model=str(llm_raw.get("model", "gpt-5.4")).strip(),
        reasoning_effort=(
            str(llm_raw["reasoning_effort"]).strip().lower()
            if "reasoning_effort" in llm_raw and llm_raw["reasoning_effort"] is not None
            else "medium"
        ),
        style_instructions=str(llm_raw.get("style_instructions", DEFAULT_STYLE_INSTRUCTIONS)),
        signature_block=(str(llm_raw["signature_block"]) if "signature_block" in llm_raw else None),
        max_input_chars=int(llm_raw.get("max_input_chars", 12000)),
    )

    style_raw = raw.get("style_corpus", {})
    style_corpus = StyleCorpusConfig(
        enabled=bool(style_raw.get("enabled", True)),
        source_folders=(
            _coerce_list(style_raw.get("source_folders", ["sentitems"]), "style_corpus.source_folders")
            or ["sentitems"]
        ),
        sync_max_messages=int(style_raw.get("sync_max_messages", 250)),
        retrieve_same_sender_first=bool(style_raw.get("retrieve_same_sender_first", True)),
        fts_fallback_enabled=bool(style_raw.get("fts_fallback_enabled", True)),
        max_examples=int(style_raw.get("max_examples", 3)),
        max_example_chars=int(style_raw.get("max_example_chars", 280)),
        min_reply_chars=int(style_raw.get("min_reply_chars", 40)),
        min_pair_confidence=float(style_raw.get("min_pair_confidence", 0.6)),
        pairing_confidence_weight=float(style_raw.get("pairing_confidence_weight", 1.5)),
        same_sender_boost=float(style_raw.get("same_sender_boost", 0.75)),
        query_rank_max_bonus=float(style_raw.get("query_rank_max_bonus", 0.5)),
        query_rank_step_penalty=float(style_raw.get("query_rank_step_penalty", 0.05)),
        recency_max_bonus=float(style_raw.get("recency_max_bonus", 0.3)),
        recency_decay_days=int(style_raw.get("recency_decay_days", 90)),
        eval_holdout_days=int(style_raw.get("eval_holdout_days", 30)),
        eval_max_cases=int(style_raw.get("eval_max_cases", 10)),
    )
    if style_corpus.sync_max_messages <= 0:
        raise ConfigError("style_corpus.sync_max_messages must be greater than 0")
    if style_corpus.max_examples <= 0:
        raise ConfigError("style_corpus.max_examples must be greater than 0")
    if style_corpus.max_example_chars <= 0:
        raise ConfigError("style_corpus.max_example_chars must be greater than 0")
    if style_corpus.min_reply_chars <= 0:
        raise ConfigError("style_corpus.min_reply_chars must be greater than 0")
    if not 0.0 <= style_corpus.min_pair_confidence <= 1.0:
        raise ConfigError("style_corpus.min_pair_confidence must be between 0 and 1")
    if style_corpus.pairing_confidence_weight < 0:
        raise ConfigError("style_corpus.pairing_confidence_weight must be non-negative")
    if style_corpus.same_sender_boost < 0:
        raise ConfigError("style_corpus.same_sender_boost must be non-negative")
    if style_corpus.query_rank_max_bonus < 0:
        raise ConfigError("style_corpus.query_rank_max_bonus must be non-negative")
    if style_corpus.query_rank_step_penalty < 0:
        raise ConfigError("style_corpus.query_rank_step_penalty must be non-negative")
    if style_corpus.recency_max_bonus < 0:
        raise ConfigError("style_corpus.recency_max_bonus must be non-negative")
    if style_corpus.recency_decay_days <= 0:
        raise ConfigError("style_corpus.recency_decay_days must be greater than 0")
    if style_corpus.eval_holdout_days <= 0:
        raise ConfigError("style_corpus.eval_holdout_days must be greater than 0")
    if style_corpus.eval_max_cases <= 0:
        raise ConfigError("style_corpus.eval_max_cases must be greater than 0")

    scheduling_raw = raw.get("scheduling", {})
    business_hours_start = str(scheduling_raw.get("business_hours_start", "09:00")).strip()
    business_hours_end = str(scheduling_raw.get("business_hours_end", "17:00")).strip()
    _parse_hhmm(business_hours_start, "scheduling.business_hours_start")
    _parse_hhmm(business_hours_end, "scheduling.business_hours_end")
    scheduling = SchedulingConfig(
        enabled=bool(scheduling_raw.get("enabled", True)),
        lookahead_days=int(scheduling_raw.get("lookahead_days", 7)),
        max_suggestions=int(scheduling_raw.get("max_suggestions", 3)),
        default_duration_minutes=int(scheduling_raw.get("default_duration_minutes", 30)),
        business_hours_start=business_hours_start,
        business_hours_end=business_hours_end,
        weekdays_only=bool(scheduling_raw.get("weekdays_only", True)),
        minimum_buffer_minutes=int(scheduling_raw.get("minimum_buffer_minutes", 15)),
        slot_step_minutes=int(scheduling_raw.get("slot_step_minutes", 30)),
    )
    if scheduling.lookahead_days <= 0:
        raise ConfigError("scheduling.lookahead_days must be greater than 0")
    if scheduling.max_suggestions <= 0:
        raise ConfigError("scheduling.max_suggestions must be greater than 0")
    if scheduling.default_duration_minutes <= 0:
        raise ConfigError("scheduling.default_duration_minutes must be greater than 0")
    if scheduling.minimum_buffer_minutes < 0:
        raise ConfigError("scheduling.minimum_buffer_minutes must be non-negative")
    if scheduling.slot_step_minutes <= 0:
        raise ConfigError("scheduling.slot_step_minutes must be greater than 0")

    briefing_raw = raw.get("briefing", {})
    briefing_model = str(briefing_raw.get("model", "")).strip() or llm.model
    briefing_output_raw = str(briefing_raw.get("output_directory", "./data/briefings")).strip()
    briefing = BriefingConfig(
        email_lookback_days=int(briefing_raw.get("email_lookback_days", 7)),
        related_emails_per_event=int(briefing_raw.get("related_emails_per_event", 5)),
        max_emails=int(briefing_raw.get("max_emails", 100)),
        sync_freshness_minutes=int(briefing_raw.get("sync_freshness_minutes", 30)),
        timezone=(str(briefing_raw["timezone"]).strip() if "timezone" in briefing_raw and briefing_raw["timezone"] else None),
        model=briefing_model,
        cache_enabled=bool(briefing_raw.get("cache_enabled", True)),
        output_directory=_resolve_path(briefing_output_raw, cwd=Path.cwd()),
    )
    if briefing.email_lookback_days <= 0:
        raise ConfigError("briefing.email_lookback_days must be greater than 0")
    if briefing.related_emails_per_event <= 0:
        raise ConfigError("briefing.related_emails_per_event must be greater than 0")
    if briefing.max_emails <= 0:
        raise ConfigError("briefing.max_emails must be greater than 0")
    if briefing.sync_freshness_minutes < 0:
        raise ConfigError("briefing.sync_freshness_minutes must be non-negative")

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
        style_corpus=style_corpus,
        scheduling=scheduling,
        briefing=briefing,
        state=state,
        logging=logging,
        source_path=source_path,
    )
