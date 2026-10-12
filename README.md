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
2. **New agent**: name, persona (or a starter template), provider, model. Optionally pick a
   **working directory** (Browse…) and a **file access** level: *Read only* (read, list and
   search files) or *Read & write* (also create and edit files). Agents can never leave their
   working directory, and never get shell, web or MCP tools.
3. **New discussion**: a direct chat with one agent, or a group (2+ agents plus a topic).
   Each send runs one bounded exchange; **Stop** cancels it. Type `@` in the composer to attach
   files from a participant's working directory; their text is sent with your message. Paste a
   PNG, JPEG, GIF, or WebP image into the composer to send it with your message. Tool
   calls an agent makes (files read, edited, searched) are listed above its reply. Copy an
   agent reply with its **Copy** button. Unsent text is saved per conversation, and the sidebar
   search finds discussions by title or topic.
4. Conversation menu → **Export transcript** writes Markdown only when you ask.

### Let Claude Code and Codex sessions in other projects consult your agents

Coding sessions working on *another* repository can ask a SmartRail agent for input (a UI
designer, a station manager, ...) through the command line. One-time setup, from this repo:

```text
uv run smartrail connect          # installs a guide for Claude Code and Codex, all projects
uv run smartrail connect --print  # show the guide instead; --remove undoes the install
```

It writes a Claude Code skill (`~/.claude/skills/smartrail-agents/SKILL.md`) and a marked block
in Codex's `~/.codex/AGENTS.md`, both naming the absolute path of this installation's
`smartrail` executable, so nothing needs to be on PATH. Start a new session afterwards. SmartRail
must be running when a session asks. The commands the sessions then use:

```text
smartrail agents                                  # who can be asked
smartrail ask "UI Designer" "question" --as "Claude Code"
smartrail ask --conversation cnv_... "follow-up"
smartrail reply cnv_...                           # collect a reply after a timeout (ask waits 100 s)
```

Each consultation is an ordinary direct chat in the sidebar (titled
`Claude Code → UI Designer: ...`) that you can read, continue or delete. `--json` gives
structured output; `SMARTRAIL_URL` or `--url` points at a non-default port.

### Options (environment variables)

| Variable | Purpose |
|---|---|
| `SMARTRAIL_PORT` | app port (default 8765; the `ask` commands use it too) |
| `SMARTRAIL_URL` | where `smartrail ask/agents/reply` find the app (default `http://127.0.0.1:$SMARTRAIL_PORT`) |
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
