from pathlib import Path

from edraft.config import load_app_config, parse_app_config


def test_parse_app_config_with_defaults(tmp_path: Path) -> None:
    config = parse_app_config(
        {
            "identity": {"name": "Jude Bayham", "email": "Jude@example.com"},
            "state": {"database_path": str(tmp_path / "state.sqlite3")},
        },
        source_path=tmp_path / "edraft.toml",
    )
    assert config.identity.email == "jude@example.com"
    assert config.scan.reply_mode == "auto"
    assert config.scan.max_message_age_hours == 24
    assert config.filters.address_score_threshold == 2
    assert config.llm.model == "gpt-5.4"
    assert config.llm.reasoning_effort == "medium"
    assert config.style_corpus.enabled is True
    assert config.style_corpus.source_folders == ["sentitems"]
    assert config.style_corpus.max_examples == 3
    assert config.style_corpus.pairing_confidence_weight == 1.5
    assert config.style_corpus.same_sender_boost == 0.75
    assert config.style_corpus.query_rank_max_bonus == 0.5
    assert config.style_corpus.query_rank_step_penalty == 0.05
    assert config.style_corpus.recency_max_bonus == 0.3
    assert config.style_corpus.recency_decay_days == 90
    assert config.scheduling.enabled is True
    assert config.scheduling.lookahead_days == 7
    assert config.scheduling.max_suggestions == 3
    assert config.scheduling.default_duration_minutes == 30
    assert config.scheduling.business_hours_start == "09:00"
    assert config.scheduling.business_hours_end == "17:00"
    assert config.scheduling.weekdays_only is True
    assert config.scheduling.minimum_buffer_minutes == 15
    assert config.briefing.email_lookback_days == 7
    assert config.briefing.related_emails_per_event == 5
    assert config.briefing.max_emails == 100
    assert config.briefing.sync_freshness_minutes == 30
    assert config.briefing.model == "gpt-5.4"


def test_load_app_config_reads_toml_file(tmp_path: Path) -> None:
    config_path = tmp_path / "edraft.toml"
    config_path.write_text(
        """
[identity]
name = "Jude Bayham"
email = "jude@example.com"

[scan]
max_message_age_hours = 12
reply_mode = "reply_all"

[state]
database_path = "./state.sqlite3"

[style_corpus]
max_examples = 2
pairing_confidence_weight = 2.0
same_sender_boost = 0.25
query_rank_max_bonus = 0.7
query_rank_step_penalty = 0.02
recency_max_bonus = 0.4
recency_decay_days = 45

[scheduling]
lookahead_days = 14
max_suggestions = 2
default_duration_minutes = 60
business_hours_start = "08:30"
business_hours_end = "16:30"
weekdays_only = false
minimum_buffer_minutes = 10
slot_step_minutes = 15

[briefing]
email_lookback_days = 10
related_emails_per_event = 3
max_emails = 75
sync_freshness_minutes = 5
timezone = "UTC"
model = "gpt-5.4"
        """.strip()
    )
    config = load_app_config(config_path)
    assert config.scan.reply_mode == "reply_all"
    assert config.scan.max_message_age_hours == 12
    assert config.style_corpus.max_examples == 2
    assert config.style_corpus.pairing_confidence_weight == 2.0
    assert config.style_corpus.same_sender_boost == 0.25
    assert config.style_corpus.query_rank_max_bonus == 0.7
    assert config.style_corpus.query_rank_step_penalty == 0.02
    assert config.style_corpus.recency_max_bonus == 0.4
    assert config.style_corpus.recency_decay_days == 45
    assert config.scheduling.lookahead_days == 14
    assert config.scheduling.max_suggestions == 2
    assert config.scheduling.default_duration_minutes == 60
    assert config.scheduling.business_hours_start == "08:30"
    assert config.scheduling.business_hours_end == "16:30"
    assert config.scheduling.weekdays_only is False
    assert config.scheduling.minimum_buffer_minutes == 10
    assert config.scheduling.slot_step_minutes == 15
    assert config.briefing.email_lookback_days == 10
    assert config.briefing.related_emails_per_event == 3
    assert config.briefing.max_emails == 75
    assert config.briefing.sync_freshness_minutes == 5
    assert config.briefing.timezone == "UTC"
    assert config.briefing.model == "gpt-5.4"
