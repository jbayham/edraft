from __future__ import annotations

import json
from pathlib import Path
from typing import Annotated

import typer

from edraft.auth import GraphAuthenticator, load_auth_settings
from edraft.config import AppConfig, load_app_config
from edraft.draft_generator import DraftGenerator
from edraft.graph_client import GraphClient
from edraft.logging_config import configure_logging
from edraft.scanner import InboxScanner
from edraft.state_store import StateStore


app = typer.Typer(help="Outlook reply-draft assistant. Creates drafts only and never sends.")


def build_components(
    config_path: Path | None = None,
    *,
    require_llm: bool,
) -> tuple[AppConfig, GraphClient, InboxScanner]:
    config = load_app_config(config_path)
    configure_logging(config.logging)
    authenticator = GraphAuthenticator(load_auth_settings())
    graph_client = GraphClient(authenticator.get_access_token)
    state_store = StateStore(config.state.database_path)
    draft_generator = DraftGenerator(config.llm, config.identity) if require_llm else None
    scanner = InboxScanner(
        config=config,
        graph_client=graph_client,
        state_store=state_store,
        draft_generator=draft_generator,
    )
    return config, graph_client, scanner


def _config_option() -> object:
    return typer.Option(
        "--config",
        "-c",
        help="Path to the TOML config file.",
        exists=True,
        file_okay=True,
        dir_okay=False,
        resolve_path=True,
    )


@app.command("scan")
def scan(config_path: Annotated[Path | None, _config_option()] = None) -> None:
    """Run one scan pass and create Outlook reply drafts."""
    _, graph_client, scanner = build_components(config_path, require_llm=True)
    try:
        report = scanner.scan_once(dry_run=False)
    finally:
        graph_client.close()
    typer.echo(
        f"examined={report.examined} skipped={report.skipped} drafted={report.drafted} errors={report.errors}"
    )


@app.command("dry-run")
def dry_run(config_path: Annotated[Path | None, _config_option()] = None) -> None:
    """Run one scan pass without creating drafts or mutating state."""
    _, graph_client, scanner = build_components(config_path, require_llm=True)
    try:
        report = scanner.scan_once(dry_run=True)
    finally:
        graph_client.close()
    typer.echo(
        f"examined={report.examined} skipped={report.skipped} would_draft={report.drafted} errors={report.errors}"
    )


@app.command("inspect")
def inspect_message(
    message_id: str,
    config_path: Annotated[Path | None, _config_option()] = None,
) -> None:
    """Inspect a single message and print filter reasoning plus thread context."""
    _, graph_client, scanner = build_components(config_path, require_llm=False)
    try:
        payload = scanner.inspect_message(message_id)
    finally:
        graph_client.close()
    typer.echo(json.dumps(payload, indent=2))


@app.command("test-auth")
def test_auth(config_path: Annotated[Path | None, _config_option()] = None) -> None:
    """Verify Microsoft Graph authentication works for the current user."""
    _, graph_client, _ = build_components(config_path, require_llm=False)
    try:
        me = graph_client.get_me()
    finally:
        graph_client.close()
    typer.echo(
        json.dumps(
            {
                "displayName": me.get("displayName"),
                "userPrincipalName": me.get("userPrincipalName"),
                "mail": me.get("mail"),
            },
            indent=2,
        )
    )
