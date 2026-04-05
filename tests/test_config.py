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
