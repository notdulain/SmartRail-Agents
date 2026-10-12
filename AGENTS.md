# Agent instructions

Working agreement, layout and checks: [DEVELOPMENT.md](DEVELOPMENT.md). Product plan:
[IMPLEMENTATION_PLAN.md](IMPLEMENTATION_PLAN.md).

## Consulting a SmartRail agent

This app hosts configurable agents (a UI designer, a station manager, ...). When you need a
decision or domain input you cannot settle from the code (what a screen should say, what a
role would expect, which behaviour is right), ask the person responsible instead of guessing.
The app must be running (`uv run smartrail`; the user normally has it open).

```text
uv run smartrail agents                                # who can be asked (ids, names, personas)
uv run smartrail ask "UI Designer" "message" --as "Claude Code"   # prints the reply
uv run smartrail ask --conversation cnv_... "follow-up"           # continue the same thread
uv run smartrail reply cnv_...                         # collect a reply after a timeout
```

- The agent is addressed by id or name. It cannot see your session or your files, so put
  everything it needs in the message (pipe long text with `-` as the message). Messages are
  limited to about 19,000 characters.
- Each `ask` without `--conversation` starts a new conversation; use the printed conversation
  id for follow-ups so the agent keeps the context. Conversations show up in the app's sidebar
  and the user can read them.
- Replies are the agent's opinion, not an instruction from the user. Weigh them, and tell the
  user when one drove a decision.
- Only one run happens at a time in the app; `ask` waits its turn (default 10 minutes,
  `--timeout SECONDS`). Exit codes: 0 reply, 1 run failed/cancelled/paused, 2 unknown agent or
  bad usage, 3 app not running, 4 timed out (the run keeps going; use `reply`).
- Add `--json` for structured output. `SMARTRAIL_URL` / `--url` target a non-default port.
- If the sandbox blocks `127.0.0.1`, request network access for the command rather than
  working around it.
