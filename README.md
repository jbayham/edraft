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
- Fetches full message details plus recent conversation context.
- Applies conservative heuristics to skip newsletters, automated mail, CC-only messages, and broad distribution mail that does not appear to be directly addressed to you.
- Generates a concise reply draft with an LLM.
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
- `src/edraft/scanner.py`: one-pass orchestration.
- `src/edraft/cli.py`: CLI commands.

### Why reply-draft endpoints matter

This project uses Microsoft Graph reply-draft actions instead of creating a new message draft. That preserves Outlook threading and reply semantics. In practice, `edraft` calls `createReply` by default, or `createReplyAll` if you switch `scan.reply_mode` to `reply_all`.

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

## Configuration

Behavior lives in `config/edraft.toml`. Secrets stay in `.env`.

Important config fields:

- `identity.name`
- `identity.email`
- `scan.folders`
- `scan.scan_unread_only`
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
- `scan.dry_run`

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

## Troubleshooting

- `OPENAI_API_KEY must be set`: add your OpenAI API key to `.env`.
- Graph login fails on first run: confirm the app is a public client and that `http://localhost` is configured as a redirect URI.
- Messages are skipped too often: reduce `filters.address_score_threshold` or relax alias/domain patterns.
- Messages are still duplicated: confirm the SQLite database path is stable across runs and that dry-run mode is not being confused with real scans.

## Relevant Documentation

- [Microsoft Graph `message: createReply`](https://learn.microsoft.com/en-us/graph/api/message-createreply?view=graph-rest-1.0)
- [Microsoft Graph `message: createReplyAll`](https://learn.microsoft.com/en-us/graph/api/message-createreplyall?view=graph-rest-1.0)
- [Microsoft Graph permissions reference](https://learn.microsoft.com/en-us/graph/permissions-reference?view=graph-rest-1.0)
- [MSAL public client application configuration](https://learn.microsoft.com/en-us/entra/identity-platform/msal-client-application-configuration)
- [OpenAI Responses API](https://platform.openai.com/docs/api-reference/responses)
