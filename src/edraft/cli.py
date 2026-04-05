from __future__ import annotations

import json
from dataclasses import replace
from pathlib import Path
from typing import Annotated

import typer

from edraft.auth import GraphAuthenticator, load_auth_settings
from edraft.config import AppConfig, load_app_config
from edraft.db_inspector import DatabaseInspector
from edraft.draft_generator import DraftGenerator
from edraft.graph_client import GraphClient
from edraft.logging_config import configure_logging
from edraft.scanner import InboxScanner
from edraft.state_store import StateStore
from edraft.style_corpus import StyleCorpusStore, StyleCorpusSyncer, StyleExampleRetriever
from edraft.style_eval import StyleEvaluator


app = typer.Typer(help="Outlook reply-draft assistant. Creates drafts only and never sends.")


def build_components(
    config_path: Path | None = None,
    *,
    require_llm: bool,
) -> tuple[AppConfig, GraphClient, InboxScanner, StyleCorpusStore]:
    config = load_app_config(config_path)
    configure_logging(config.logging)
    authenticator = GraphAuthenticator(load_auth_settings())
    graph_client = GraphClient(authenticator.get_access_token)
    state_store = StateStore(config.state.database_path)
    style_store = StyleCorpusStore(config.state.database_path)
    draft_generator = DraftGenerator(config.llm, config.identity) if require_llm else None
    style_retriever = (
        StyleExampleRetriever(style_store, config.style_corpus)
        if config.style_corpus.enabled
        else None
    )
    scanner = InboxScanner(
        config=config,
        graph_client=graph_client,
        state_store=state_store,
        draft_generator=draft_generator,
        style_retriever=style_retriever,
    )
    return config, graph_client, scanner, style_store


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
    _, graph_client, scanner, _ = build_components(config_path, require_llm=True)
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
    _, graph_client, scanner, _ = build_components(config_path, require_llm=True)
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
    _, graph_client, scanner, _ = build_components(config_path, require_llm=False)
    try:
        payload = scanner.inspect_message(message_id)
    finally:
        graph_client.close()
    typer.echo(json.dumps(payload, indent=2))


@app.command("test-auth")
def test_auth(config_path: Annotated[Path | None, _config_option()] = None) -> None:
    """Verify Microsoft Graph authentication works for the current user."""
    _, graph_client, _, _ = build_components(config_path, require_llm=False)
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


@app.command("corpus-sync")
def corpus_sync(
    config_path: Annotated[Path | None, _config_option()] = None,
    limit: Annotated[int | None, typer.Option("--limit", min=1, help="Maximum sent messages to sync.")] = None,
) -> None:
    """Sync a local style corpus from your sent Outlook replies."""
    config, graph_client, _, style_store = build_components(config_path, require_llm=False)
    sync_config = (
        replace(config.style_corpus, sync_max_messages=limit)
        if limit is not None
        else config.style_corpus
    )
    syncer = StyleCorpusSyncer(
        graph_client,
        style_store,
        identity=config.identity,
        config=sync_config,
    )
    try:
        report = syncer.sync()
    finally:
        graph_client.close()
    typer.echo(
        f"scanned={report.scanned} paired={report.paired} skipped={report.skipped} errors={report.errors}"
    )


@app.command("eval-style")
def eval_style(
    config_path: Annotated[Path | None, _config_option()] = None,
    limit: Annotated[int | None, typer.Option("--limit", min=1, help="Maximum eval cases.")] = None,
    include_prompts: Annotated[
        bool,
        typer.Option(
            "--include-prompts",
            help="Include generation and grading prompts in the JSON output.",
        ),
    ] = False,
) -> None:
    """Evaluate style match against held-out real replies from the local corpus."""
    config, graph_client, scanner, style_store = build_components(config_path, require_llm=True)
    try:
        generator = scanner.draft_generator
        if generator is None:
            raise RuntimeError("Draft generator is not configured.")
        retriever = StyleExampleRetriever(style_store, config.style_corpus)
        evaluator = StyleEvaluator(
            config=config.style_corpus,
            generator=generator,
            store=style_store,
            retriever=retriever,
        )
        payload = evaluator.evaluate(limit=limit, include_prompts=include_prompts)
    finally:
        graph_client.close()
    typer.echo(json.dumps(payload, indent=2))


@app.command("db-inspect")
def db_inspect(
    config_path: Annotated[Path | None, _config_option()] = None,
    table: Annotated[
        str | None,
        typer.Option("--table", help="Specific table to inspect in detail."),
    ] = None,
    limit: Annotated[
        int,
        typer.Option("--limit", min=1, help="Maximum rows to print for a specific table."),
    ] = 10,
) -> None:
    """Inspect the local SQLite database used by edraft."""
    config = load_app_config(config_path)
    inspector = DatabaseInspector(config.state.database_path)
    payload = inspector.inspect_table(table, limit=limit) if table else inspector.summary()
    typer.echo(json.dumps(payload, indent=2))


@app.command("corpus-stats")
def corpus_stats(
    config_path: Annotated[Path | None, _config_option()] = None,
) -> None:
    """Show high-level statistics for the local style corpus."""
    config = load_app_config(config_path)
    inspector = DatabaseInspector(config.state.database_path)
    typer.echo(json.dumps(inspector.corpus_stats(), indent=2))
