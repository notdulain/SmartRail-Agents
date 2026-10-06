# SmartRail Agents

Local browser app for creating AI agents, chatting with them, and running discussions between
them. Models come from your OpenCode installation; provider sign-in stays in OpenCode.
See [IMPLEMENTATION_PLAN.md](IMPLEMENTATION_PLAN.md) and [DEVELOPMENT.md](DEVELOPMENT.md).

## Run it

Prerequisites: Git, [uv](https://docs.astral.sh/uv/), Node.js, and
[OpenCode](https://opencode.ai/docs/) (installed, with at least one provider connected).

Same commands in a macOS terminal and Windows PowerShell:

```text
uv sync --frozen
npm ci
npm run build
uv run smartrail
```

`uv run smartrail` starts an app-managed OpenCode server (loopback only, random password),
waits until it is healthy, serves the app at <http://127.0.0.1:8765/>, and opens your browser.
Ctrl+C stops everything, including OpenCode.

### Connect providers (once)

Provider credentials live in OpenCode, not in this app. Connect them in a terminal, then click
**Refresh** in the model picker:

```text
opencode auth login        # choose OpenAI → ChatGPT sign-in for the GPT-6-sol coordinator
```

### First use

1. **Settings**: pick a coordinator agent (needed for group discussions) and optionally write the
   shared project brief.
2. **New agent**: name, persona (or a starter template), provider, model.
3. **New discussion**: a direct chat with one agent, or a group (2+ agents plus a topic).
   Each send runs one bounded exchange; **Stop** cancels it.
4. Conversation menu → **Export transcript** writes Markdown only when you ask.

### Options (environment variables)

| Variable | Purpose |
|---|---|
| `SMARTRAIL_PORT` | app port (default 8765) |
| `SMARTRAIL_DATA_DIR` | database, logs, runtime files (default: your OS app-data folder) |
| `SMARTRAIL_OPENCODE_PORT` | fixed port for the managed OpenCode (default: random free port) |
| `SMARTRAIL_OPENCODE_URL` + `SMARTRAIL_OPENCODE_PASSWORD` | attach to an OpenCode you started yourself |
| `SMARTRAIL_OPENCODE_BIN` | path to the `opencode` executable |
| `SMARTRAIL_NO_BROWSER=1` | do not open the browser |

### Try the UI without any model calls

```text
SMARTRAIL_PORT=8773 SMARTRAIL_DATA_DIR=.data uv run python scripts/dev_fake_backend.py
```

Uses a fake runtime that echoes text (no OpenCode, no cost).

## Development

```text
uv run ruff check .
uv run pytest -q
npm run typecheck && npm test
```
