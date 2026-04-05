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
        """.strip()
    )
    config = load_app_config(config_path)
    assert config.scan.reply_mode == "reply_all"
    assert config.scan.max_message_age_hours == 12
    assert config.style_corpus.max_examples == 2
