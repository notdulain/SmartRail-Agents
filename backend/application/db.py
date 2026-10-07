"""SQLite access (aiosqlite) with schema versioning.

One connection, autocommit mode. Single statements are atomic on their own; multi-statement
writes go through :meth:`Database.tx`, which serialises them under a lock and shields them
from task cancellation so a Stop never leaves a half-written transaction behind.
"""

from __future__ import annotations

import asyncio
from collections.abc import Sequence
from pathlib import Path
from typing import Any

import aiosqlite

SCHEMA_VERSION = 3

_SCHEMA_V1 = """
CREATE TABLE agents (
    id TEXT PRIMARY KEY,
    name TEXT NOT NULL,
    persona TEXT NOT NULL,
    provider_id TEXT NOT NULL,
    model_id TEXT NOT NULL,
    revision INTEGER NOT NULL DEFAULT 1,
    archived INTEGER NOT NULL DEFAULT 0,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);

CREATE TABLE conversations (
    id TEXT PRIMARY KEY,
    type TEXT NOT NULL CHECK (type IN ('direct', 'group')),
    title TEXT NOT NULL,
    topic TEXT,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);

CREATE TABLE participants (
    conversation_id TEXT NOT NULL REFERENCES conversations(id) ON DELETE CASCADE,
    agent_id TEXT NOT NULL REFERENCES agents(id),
    position INTEGER NOT NULL,
    PRIMARY KEY (conversation_id, agent_id)
);

CREATE TABLE runs (
    id TEXT PRIMARY KEY,
    conversation_id TEXT NOT NULL REFERENCES conversations(id) ON DELETE CASCADE,
    status TEXT NOT NULL,
    participants_json TEXT NOT NULL,
    coordinator_json TEXT,
    input_tokens INTEGER NOT NULL DEFAULT 0,
    output_tokens INTEGER NOT NULL DEFAULT 0,
    cost_usd REAL,
    error TEXT,
    error_code TEXT,
    created_at TEXT NOT NULL,
    finished_at TEXT
);
CREATE INDEX runs_status ON runs(status);

-- ``ord`` gives a stable global order; ``id`` is the public identifier.
CREATE TABLE messages (
    ord INTEGER PRIMARY KEY AUTOINCREMENT,
    id TEXT NOT NULL UNIQUE,
    conversation_id TEXT NOT NULL REFERENCES conversations(id) ON DELETE CASCADE,
    run_id TEXT REFERENCES runs(id),
    role TEXT NOT NULL,
    speaker_name TEXT NOT NULL,
    agent_json TEXT,
    provider_id TEXT,
    model_id TEXT,
    content TEXT NOT NULL,
    status TEXT NOT NULL,
    error TEXT,
    error_code TEXT,
    reply_to_id TEXT,
    stage INTEGER,
    created_at TEXT NOT NULL
);
CREATE INDEX messages_conversation ON messages(conversation_id, ord);
CREATE INDEX messages_run ON messages(run_id);

-- Persisted SSE log; ``seq`` is per run, starting at 1.
CREATE TABLE run_events (
    run_id TEXT NOT NULL REFERENCES runs(id) ON DELETE CASCADE,
    seq INTEGER NOT NULL,
    type TEXT NOT NULL,
    data TEXT NOT NULL,
    PRIMARY KEY (run_id, seq)
);

-- (conversation, agent) -> OpenCode session, created lazily. ``seen_ord`` is the last
-- message ordinal that session has been shown (group transcripts are sent incrementally).
CREATE TABLE session_map (
    conversation_id TEXT NOT NULL REFERENCES conversations(id) ON DELETE CASCADE,
    agent_id TEXT NOT NULL REFERENCES agents(id),
    session_id TEXT NOT NULL,
    seen_ord INTEGER NOT NULL DEFAULT 0,
    PRIMARY KEY (conversation_id, agent_id)
);

CREATE TABLE settings (
    id INTEGER PRIMARY KEY CHECK (id = 1),
    project_brief TEXT NOT NULL DEFAULT '',
    coordinator_agent_id TEXT REFERENCES agents(id),
    openrouter_monthly_budget_usd REAL NOT NULL,
    participant_max_tokens INTEGER NOT NULL,
    coordinator_max_tokens INTEGER NOT NULL
);

CREATE TABLE openrouter_spend (
    month TEXT PRIMARY KEY,  -- YYYY-MM (UTC)
    usd REAL NOT NULL DEFAULT 0
);
"""

# v2: agent working directories and file tools, message attachments and tool calls, and the
# directory each OpenCode session was created with (a session's directory is immutable, so a
# changed agent directory means a new session).
_SCHEMA_V2 = """
ALTER TABLE agents ADD COLUMN working_directory TEXT;
ALTER TABLE agents ADD COLUMN tool_access TEXT NOT NULL DEFAULT 'none';

-- ``attachments_json``: FileRef list (user messages). ``attachments_text``: the rendered file
-- contents exactly as sent to the model at send time. ``tool_calls_json``: ToolCall list.
ALTER TABLE messages ADD COLUMN attachments_json TEXT;
ALTER TABLE messages ADD COLUMN attachments_text TEXT;
ALTER TABLE messages ADD COLUMN tool_calls_json TEXT;

ALTER TABLE session_map ADD COLUMN directory TEXT;
"""

_SCHEMA_V3 = """
ALTER TABLE messages ADD COLUMN images_json TEXT;
"""

# version -> script upgrading from (version - 1). Append new entries; never edit old ones.
_MIGRATIONS: dict[int, str] = {1: _SCHEMA_V1, 2: _SCHEMA_V2, 3: _SCHEMA_V3}

Statement = tuple[str, Sequence[Any]]


class Database:
    def __init__(self, path: Path) -> None:
        self.path = path
        self._conn: aiosqlite.Connection | None = None
        self._write_lock = asyncio.Lock()

    @property
    def conn(self) -> aiosqlite.Connection:
        if self._conn is None:
            raise RuntimeError("database is not open")
        return self._conn

    async def open(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        conn = await aiosqlite.connect(self.path, isolation_level=None)
        conn.row_factory = aiosqlite.Row
        self._conn = conn
        await conn.execute("PRAGMA journal_mode=WAL")
        await conn.execute("PRAGMA foreign_keys=ON")
        await conn.execute("PRAGMA synchronous=NORMAL")
        await conn.execute("PRAGMA busy_timeout=5000")
        await self._migrate()

    async def _migrate(self) -> None:
        async with self.conn.execute("PRAGMA user_version") as cur:
            row = await cur.fetchone()
        version = int(row[0]) if row else 0
        if version > SCHEMA_VERSION:
            raise RuntimeError(
                f"database schema v{version} is newer than this app supports (v{SCHEMA_VERSION})"
            )
        for target in range(version + 1, SCHEMA_VERSION + 1):
            await self.conn.executescript(
                f"BEGIN;\n{_MIGRATIONS[target]}\nPRAGMA user_version = {target};\nCOMMIT;"
            )

    async def close(self) -> None:
        if self._conn is not None:
            await self._conn.close()
            self._conn = None

    async def fetchall(self, sql: str, params: Sequence[Any] = ()) -> list[aiosqlite.Row]:
        async with self.conn.execute(sql, params) as cur:
            return list(await cur.fetchall())

    async def fetchone(self, sql: str, params: Sequence[Any] = ()) -> aiosqlite.Row | None:
        async with self.conn.execute(sql, params) as cur:
            return await cur.fetchone()

    async def execute(self, sql: str, params: Sequence[Any] = ()) -> None:
        """One atomic statement."""
        await asyncio.shield(self._execute(sql, params))

    async def _execute(self, sql: str, params: Sequence[Any]) -> None:
        async with self._write_lock:
            await self.conn.execute(sql, params)

    async def tx(self, statements: Sequence[Statement]) -> None:
        """Run several statements atomically; survives cancellation of the caller."""
        await asyncio.shield(self._tx(statements))

    async def _tx(self, statements: Sequence[Statement]) -> None:
        async with self._write_lock:
            await self.conn.execute("BEGIN IMMEDIATE")
            try:
                for sql, params in statements:
                    await self.conn.execute(sql, params)
            except BaseException:
                await self.conn.execute("ROLLBACK")
                raise
            await self.conn.execute("COMMIT")
