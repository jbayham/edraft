# edraft

`edraft` is a local Python CLI/service that scans unread Outlook inbox messages, decides conservatively whether a message deserves a reply draft, generates a proposed reply with an LLM, and saves that reply as an Outlook draft in the original thread.

It does not send email.

That is a hard constraint in this repo:

- there is no send-email command
- there is no Graph `send` endpoint usage
- draft creation uses Microsoft Graph reply-draft endpoints so replies stay in Outlook Drafts for manual review

## What It Does

- Authenticates to Microsoft 365 with delegated Microsoft Graph permissions for a single local user.
- Reads unread messages from configured mail folders, defaulting to `Inbox`.
- Limits draft candidates to unread messages received within the configured age window, defaulting to the last 24 hours.
- Fetches full message details plus recent conversation context.
- Applies conservative heuristics to skip newsletters, automated mail, CC-only messages, and broad distribution mail that does not appear to be directly addressed to you.
- Generates a concise reply draft with an LLM.
- Can sync your historical sent replies into a local style corpus and retrieve similar past replies to improve tone.
- Creates an Outlook reply draft in the existing thread using `createReply` or `createReplyAll`.
- Tracks processed messages in a local SQLite database so hourly runs are idempotent.
- Optionally tags source messages with an Outlook category after processing.

## What It Will Not Do

- It never sends email.
- It does not request `Mail.Send`.
- It does not create unrelated new draft messages with a fake `RE:` subject.
- It does not assume every unread message deserves a reply.

## Architecture

Core modules:

- `src/edraft/auth.py`: delegated Microsoft login via MSAL token cache.
- `src/edraft/graph_client.py`: Graph mail reads plus reply-draft creation.
- `src/edraft/message_fetcher.py`: unread message discovery with processed-message suppression.
- `src/edraft/filters.py`: conservative skip heuristics.
- `src/edraft/thread_context.py`: recent conversation retrieval.
- `src/edraft/draft_generator.py`: LLM prompt construction and draft generation.
- `src/edraft/draft_creator.py`: reply-draft creation only.
- `src/edraft/state_store.py`: SQLite state for idempotency.
- `src/edraft/style_corpus.py`: local style-corpus sync, storage, and retrieval.
- `src/edraft/style_eval.py`: held-out style evaluation workflow.
- `src/edraft/scanner.py`: one-pass orchestration.
- `src/edraft/cli.py`: CLI commands.

### Why reply-draft endpoints matter

This project uses Microsoft Graph reply-draft actions instead of creating a new message draft. That preserves Outlook threading and reply semantics. By default, `edraft` uses `scan.reply_mode = "auto"`: it calls `createReplyAll` when the message includes other recipients in `To` or `Cc`, and `createReply` otherwise.

## Setup

### 1. Create a virtual environment

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -e ".[dev]"
```

### 2. Create your config and env files

```bash
cp config/edraft.example.toml config/edraft.toml
cp .env.example .env
```

Edit `config/edraft.toml` for your identity, scan preferences, filters, and LLM style.

Edit `.env` for your Microsoft and OpenAI credentials.

If your tenant already allows the public client app used by `Microsoft365R`, you can leave `MICROSOFT_CLIENT_ID` unset and `edraft` will use that published client ID as a fallback. `edraft` still requests only `User.Read` and `Mail.ReadWrite`.

## Azure App Registration

Use a delegated public-client app for a single-user desktop workflow.

1. Go to Azure Portal -> Microsoft Entra ID -> App registrations -> New registration.
2. Create a single-tenant or multi-tenant app, depending on your mailbox setup.
3. After creation, copy the Application (client) ID into `MICROSOFT_CLIENT_ID`.
4. Copy the Directory (tenant) ID into `MICROSOFT_TENANT_ID`.
5. Open `Authentication`.
6. Add a mobile and desktop redirect URI for `http://localhost`.
7. Ensure the app is treated as a public client.
8. Open `API permissions`.
9. Add delegated permissions:
   `User.Read`
   `Mail.ReadWrite`
10. Do not add `Mail.Send`.
11. Grant consent if your tenant requires it.

You do not need to add `offline_access` manually in the app code or portal permissions list for this tool. MSAL handles the reserved OIDC scopes for public-client sign-in flows.

The first `edraft test-auth` or `edraft scan` run opens a local sign-in flow through MSAL. Tokens are cached locally in `data/msal_token_cache.bin` unless you override `EDRAFT_TOKEN_CACHE_PATH`.

### Optional fallback: reuse the Microsoft365R public client

If your organization already permits the public client used by the R package `Microsoft365R`, `edraft` can reuse it. In that case:

- set `MICROSOFT_TENANT_ID`
- leave `MICROSOFT_CLIENT_ID` blank or unset

`edraft` will fall back to the published Microsoft365R client ID. This can help when your tenant blocks approval of a new app registration. The tradeoff is that the public client registration itself is broader than `edraft`, even though `edraft` still requests only `User.Read` and `Mail.ReadWrite`.

## Configuration

Behavior lives in `config/edraft.toml`. Secrets stay in `.env`.

Important config fields:

- `identity.name`
- `identity.email`
- `scan.folders`
- `scan.scan_unread_only`
- `scan.max_message_age_hours`
- `scan.max_messages_per_scan`
- `scan.thread_context_messages`
- `scan.reply_mode`
- `scan.processed_category`
- `scan.apply_processed_category`
- `filters.sender_patterns`
- `filters.domain_patterns`
- `filters.group_alias_patterns`
- `filters.address_score_threshold`
- `llm.model`
- `llm.style_instructions`
- `llm.signature_block`
- `style_corpus.enabled`
- `style_corpus.source_folders`
- `style_corpus.sync_max_messages`
- `style_corpus.max_examples`
- `style_corpus.max_example_chars`
- `style_corpus.pairing_confidence_weight`
- `style_corpus.same_sender_boost`
- `style_corpus.query_rank_max_bonus`
- `style_corpus.query_rank_step_penalty`
- `style_corpus.recency_max_bonus`
- `style_corpus.recency_decay_days`
- `style_corpus.eval_holdout_days`
- `scan.dry_run`

The style retrieval weights control how archived examples are ranked:

- `style_corpus.pairing_confidence_weight`: how strongly to trust high-confidence inbound/reply pairings.
- `style_corpus.same_sender_boost`: extra preference for examples from the same correspondent.
- `style_corpus.query_rank_max_bonus`: maximum boost for the best full-text match.
- `style_corpus.query_rank_step_penalty`: how quickly that full-text boost drops for lower-ranked matches.
- `style_corpus.recency_max_bonus`: maximum preference for newer replies.
- `style_corpus.recency_decay_days`: how long the recency bonus takes to fade to zero.

## CLI

### Test auth

```bash
edraft test-auth
```

### Scan once and create reply drafts

```bash
edraft scan
```

### Dry run

Same logic, but no Outlook draft is created and no local state is written:

```bash
edraft dry-run
```

### Inspect one message

```bash
edraft inspect <message-id>
```

This prints message details, thread context, filter decisions, and local state for debugging.

### Sync the style corpus

This builds a local corpus of inbound/sent reply pairs from your Outlook history. It does not send email.

```bash
edraft corpus-sync
```

### Evaluate style match

This runs held-out cases from the local corpus, generates drafts, and grades them against your real replies.

```bash
edraft eval-style --limit 5
```

### Inspect the local SQLite database

This reads the local `edraft` database only. It does not contact Microsoft Graph.

Summary view:

```bash
edraft db-inspect
```

Inspect one table with rows:

```bash
edraft db-inspect --table style_reply_pairs --limit 20
```

## Hourly Execution

The primary operating mode is a single pass per invocation.

### Cron example

```cron
0 * * * * cd /Users/yourname/path/to/edraft && /Users/yourname/path/to/edraft/.venv/bin/edraft scan >> /Users/yourname/path/to/edraft/data/cron.log 2>&1
```

### Windows Task Scheduler

Create an hourly task that runs:

```powershell
C:\path\to\edraft\.venv\Scripts\edraft.exe scan
```

Set the working directory to your repo root so the default `config/edraft.toml` path resolves cleanly.

## Filtering Heuristics

`edraft` skips messages conservatively when signals suggest the message is not an appropriate auto-draft candidate.

Implemented heuristics include:

- `List-Unsubscribe`, `List-Id`, or bulk-precedence headers
- no-reply and automated sender patterns
- `Auto-Submitted` and calendar-message headers
- CC-only messages
- large recipient lists with no direct salutation
- broad alias patterns such as `team@` or `all@`

The direct-address heuristic is score-based. Being in `To` and greeted by name pushes the score up. Being only in `Cc`, appearing in a broad broadcast, or matching list-style signals pushes the score down. If the score is below threshold, the message is skipped.

## State Management

Processed messages are tracked in local SQLite at `state.database_path`.

Stored fields include:

- source message ID
- conversation ID
- subject
- received timestamp
- action: `skipped`, `drafted`, or `error`
- reason
- created draft ID
- timestamps

This prevents duplicate draft creation across hourly runs. By default, `drafted` and `skipped` are terminal actions; `error` records are retried on future runs.

The same SQLite file also stores the optional style corpus in separate tables for:

- archived message text used for style retrieval
- inbound/reply pairs
- full-text search data
- held-out eval selections

The `edraft db-inspect` command can show a summary of those tables or dump rows from one table as JSON.

## Logging

Logs are emitted in JSON by default and include:

- messages examined
- skip reasons
- draft creation events
- draft IDs
- error details

## Testing

```bash
pytest
```

The tests mock Graph and LLM boundaries and cover:

- sender and newsletter filtering
- CC-only detection
- direct-address scoring
- duplicate prevention
- config loading
- reply-draft endpoint usage without send behavior
- style corpus sync and retrieval
- held-out style evaluation

## Troubleshooting

- `OPENAI_API_KEY must be set`: add your OpenAI API key to `.env`.
- Graph login fails on first run: confirm the app is a public client and that `http://localhost` is configured as a redirect URI.
- Graph login is blocked for your own app registration: try leaving `MICROSOFT_CLIENT_ID` unset to use the Microsoft365R fallback client if your tenant already allows it.
- Messages are skipped too often: reduce `filters.address_score_threshold` or relax alias/domain patterns.
- Style retrieval is weak: run `edraft corpus-sync` again after you have more real sent replies, or raise `style_corpus.sync_max_messages`.
- Style retrieval is picking the wrong examples: tune `style_corpus.same_sender_boost`, `style_corpus.pairing_confidence_weight`, and the FTS/recency weights in `config/edraft.toml`.
- Messages are still duplicated: confirm the SQLite database path is stable across runs and that dry-run mode is not being confused with real scans.

## Relevant Documentation

- [Microsoft Graph `message: createReply`](https://learn.microsoft.com/en-us/graph/api/message-createreply?view=graph-rest-1.0)
- [Microsoft Graph `message: createReplyAll`](https://learn.microsoft.com/en-us/graph/api/message-createreplyall?view=graph-rest-1.0)
- [Microsoft Graph permissions reference](https://learn.microsoft.com/en-us/graph/permissions-reference?view=graph-rest-1.0)
- [MSAL public client application configuration](https://learn.microsoft.com/en-us/entra/identity-platform/msal-client-application-configuration)
- [OpenAI Responses API](https://platform.openai.com/docs/api-reference/responses)
