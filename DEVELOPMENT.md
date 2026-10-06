# Development guide

Product plan: [IMPLEMENTATION_PLAN.md](IMPLEMENTATION_PLAN.md). This file is the working
agreement for everyone (human or agent) changing the code.

## Setup (identical on macOS and native Windows)

Prerequisites: Git, uv, Node.js, OpenCode.

```text
uv sync --frozen
npm ci
npm run build
uv run smartrail
```

Checks: `uv run ruff check .`, `uv run pytest -q`, `npm run typecheck`, `npm test`.

## Layout and ownership

| Path | Owner | Notes |
|---|---|---|
| `backend/contracts/`, `backend/config.py`, `backend/launcher.py`, `backend/main.py` | coordinator | shared interfaces and startup |
| `backend/runtime/` | runtime agent | OpenCode adapter; implements `OpenCodeRuntime` |
| `backend/application/` | application agent | persistence, routes, discussions, budget |
| `frontend/` | frontend agent | React UI; types generated from the contract |
| `pyproject.toml`, `uv.lock`, `package.json`, `package-lock.json`, `.github/` | coordinator | workers ask for dependency changes |
| `tests/` | coordinator (integration); workers add `tests/<area>/` for their own area | |

Never edit another owner's files. Need a contract change? Message the coordinator.

## Contracts

- HTTP/SSE shapes: `backend/contracts/models.py` + `backend/contracts/api_spec.py`.
- After any change: `uv run python scripts/export_openapi.py` and commit
  `backend/contracts/openapi.json` (a test fails if it is stale).
- Frontend types are generated from `openapi.json` (`npm run gen:types`).
- Runtime seam: `backend/contracts/runtime.py` (`OpenCodeRuntime` Protocol).
  `backend/contracts/fake_runtime.py` is the scriptable test double. Automated tests never
  call real model providers.

## Parallel development (git worktrees)

```text
git worktree add .worktrees/runtime     -b feature/runtime
git worktree add .worktrees/application -b feature/application
git worktree add .worktrees/frontend    -b feature/frontend
```

Each worktree uses its own ports and data directory so instances never collide:

| Worktree | `SMARTRAIL_PORT` | `SMARTRAIL_OPENCODE_PORT` | `SMARTRAIL_DATA_DIR` |
|---|---|---|---|
| runtime | 8771 | 4171 | `.worktrees/runtime/.data` |
| application | 8772 | 4172 | `.worktrees/application/.data` |
| frontend | 8773 | 4173 | `.worktrees/frontend/.data` |

Workers commit small, complete changes on their branch. The coordinator merges into `main`,
runs the full checks after each merge, and removes worktrees once integrated.

## Security rules

- Provider credentials live only in OpenCode. Never copy them into the database, logs,
  tests, or commits.
- Servers bind to `127.0.0.1` only; OpenCode is protected by a generated password.
- Runtime/test data lives in the platform app-data dir or `SMARTRAIL_DATA_DIR`, never in the repo.
