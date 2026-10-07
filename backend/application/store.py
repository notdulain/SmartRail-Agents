"""Typed persistence layer: SQL in, contract models out."""

from __future__ import annotations

import json
from collections.abc import Sequence
from datetime import datetime
from typing import Any

import aiosqlite

from backend.contracts.models import (
    DEFAULT_COORDINATOR_MAX_TOKENS,
    DEFAULT_OPENROUTER_BUDGET_USD,
    DEFAULT_PARTICIPANT_MAX_TOKENS,
    Agent,
    AgentSnapshot,
    Conversation,
    ConversationType,
    ErrorCode,
    Message,
    MessageRole,
    MessageStatus,
    Run,
    RunStatus,
    Settings,
    ToolAccess,
    Usage,
)

from .db import Database, Statement
from .util import iso, month_key, parse_dt


def snapshot_of(agent: Agent) -> AgentSnapshot:
    return AgentSnapshot(
        agent_id=agent.id,
        name=agent.name,
        persona=agent.persona,
        provider_id=agent.provider_id,
        model_id=agent.model_id,
        revision=agent.revision,
        working_directory=agent.working_directory,
        tool_access=agent.tool_access,
    )


def _agent(row: aiosqlite.Row) -> Agent:
    return Agent(
        id=row["id"],
        name=row["name"],
        persona=row["persona"],
        provider_id=row["provider_id"],
        model_id=row["model_id"],
        working_directory=row["working_directory"],
        tool_access=ToolAccess(row["tool_access"]),
        revision=row["revision"],
        archived=bool(row["archived"]),
        created_at=parse_dt(row["created_at"]),
        updated_at=parse_dt(row["updated_at"]),
    )


def _message(row: aiosqlite.Row) -> Message:
    return Message(
        id=row["id"],
        conversation_id=row["conversation_id"],
        run_id=row["run_id"],
        role=MessageRole(row["role"]),
        speaker_name=row["speaker_name"],
        agent=AgentSnapshot.model_validate_json(row["agent_json"]) if row["agent_json"] else None,
        provider_id=row["provider_id"],
        model_id=row["model_id"],
        content=row["content"],
        status=MessageStatus(row["status"]),
        error=row["error"],
        error_code=ErrorCode(row["error_code"]) if row["error_code"] else None,
        reply_to_id=row["reply_to_id"],
        stage=row["stage"],
        created_at=parse_dt(row["created_at"]),
    )


def _run(row: aiosqlite.Row) -> Run:
    return Run(
        id=row["id"],
        conversation_id=row["conversation_id"],
        status=RunStatus(row["status"]),
        participants=[
            AgentSnapshot.model_validate(p) for p in json.loads(row["participants_json"])
        ],
        coordinator=(
            AgentSnapshot.model_validate_json(row["coordinator_json"])
            if row["coordinator_json"]
            else None
        ),
        usage=Usage(
            input_tokens=row["input_tokens"],
            output_tokens=row["output_tokens"],
            cost_usd=row["cost_usd"],
        ),
        error=row["error"],
        error_code=ErrorCode(row["error_code"]) if row["error_code"] else None,
        created_at=parse_dt(row["created_at"]),
        finished_at=parse_dt(row["finished_at"]) if row["finished_at"] else None,
    )


class Store:
    def __init__(self, db: Database) -> None:
        self.db = db

    # ------------------------------------------------------------------ settings

    async def init_settings(self) -> None:
        await self.db.execute(
            "INSERT OR IGNORE INTO settings (id, openrouter_monthly_budget_usd,"
            " participant_max_tokens, coordinator_max_tokens) VALUES (1, ?, ?, ?)",
            (
                DEFAULT_OPENROUTER_BUDGET_USD,
                DEFAULT_PARTICIPANT_MAX_TOKENS,
                DEFAULT_COORDINATOR_MAX_TOKENS,
            ),
        )

    async def get_settings(self) -> Settings:
        row = await self.db.fetchone("SELECT * FROM settings WHERE id = 1")
        assert row is not None
        return Settings(
            project_brief=row["project_brief"],
            coordinator_agent_id=row["coordinator_agent_id"],
            openrouter_monthly_budget_usd=row["openrouter_monthly_budget_usd"],
            openrouter_spent_usd=await self.get_spend(),
            participant_max_tokens=row["participant_max_tokens"],
            coordinator_max_tokens=row["coordinator_max_tokens"],
        )

    async def update_settings(self, fields: dict[str, Any]) -> None:
        if not fields:
            return
        cols = ", ".join(f"{k} = ?" for k in fields)
        await self.db.execute(f"UPDATE settings SET {cols} WHERE id = 1", list(fields.values()))

    async def get_spend(self, month: str | None = None) -> float:
        row = await self.db.fetchone(
            "SELECT usd FROM openrouter_spend WHERE month = ?", (month or month_key(),)
        )
        return float(row["usd"]) if row else 0.0

    @staticmethod
    def stmt_add_spend(usd: float, month: str | None = None) -> Statement:
        return (
            "INSERT INTO openrouter_spend (month, usd) VALUES (?, ?)"
            " ON CONFLICT(month) DO UPDATE SET usd = usd + excluded.usd",
            (month or month_key(), usd),
        )

    # ------------------------------------------------------------------ agents

    async def insert_agent(self, agent: Agent) -> None:
        await self.db.execute(
            "INSERT INTO agents (id, name, persona, provider_id, model_id, working_directory,"
            " tool_access, revision, archived, created_at, updated_at)"
            " VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (
                agent.id,
                agent.name,
                agent.persona,
                agent.provider_id,
                agent.model_id,
                agent.working_directory,
                agent.tool_access.value,
                agent.revision,
                int(agent.archived),
                iso(agent.created_at),
                iso(agent.updated_at),
            ),
        )

    async def get_agent(self, agent_id: str) -> Agent | None:
        row = await self.db.fetchone("SELECT * FROM agents WHERE id = ?", (agent_id,))
        return _agent(row) if row else None

    async def get_agents(self, agent_ids: Sequence[str]) -> dict[str, Agent]:
        if not agent_ids:
            return {}
        marks = ",".join("?" for _ in agent_ids)
        rows = await self.db.fetchall(f"SELECT * FROM agents WHERE id IN ({marks})", agent_ids)
        return {r["id"]: _agent(r) for r in rows}

    async def list_agents(self, include_archived: bool) -> list[Agent]:
        sql = "SELECT * FROM agents"
        if not include_archived:
            sql += " WHERE archived = 0"
        rows = await self.db.fetchall(sql + " ORDER BY rowid")
        return [_agent(r) for r in rows]

    async def save_agent(self, agent: Agent) -> None:
        await self.db.execute(
            "UPDATE agents SET name = ?, persona = ?, provider_id = ?, model_id = ?,"
            " working_directory = ?, tool_access = ?, revision = ?, archived = ?, updated_at = ?"
            " WHERE id = ?",
            (
                agent.name,
                agent.persona,
                agent.provider_id,
                agent.model_id,
                agent.working_directory,
                agent.tool_access.value,
                agent.revision,
                int(agent.archived),
                iso(agent.updated_at),
                agent.id,
            ),
        )

    # ------------------------------------------------------------------ conversations

    async def insert_conversation(self, conv: Conversation) -> None:
        stmts: list[Statement] = [
            (
                "INSERT INTO conversations (id, type, title, topic, created_at, updated_at)"
                " VALUES (?, ?, ?, ?, ?, ?)",
                (
                    conv.id,
                    conv.type.value,
                    conv.title,
                    conv.topic,
                    iso(conv.created_at),
                    iso(conv.updated_at),
                ),
            )
        ]
        for pos, agent_id in enumerate(conv.participant_ids):
            stmts.append(
                (
                    "INSERT INTO participants (conversation_id, agent_id, position)"
                    " VALUES (?, ?, ?)",
                    (conv.id, agent_id, pos),
                )
            )
        await self.db.tx(stmts)

    async def _conversations(self, rows: Sequence[aiosqlite.Row]) -> list[Conversation]:
        if not rows:
            return []
        marks = ",".join("?" for _ in rows)
        prows = await self.db.fetchall(
            f"SELECT conversation_id, agent_id FROM participants WHERE conversation_id IN ({marks})"
            " ORDER BY conversation_id, position",
            [r["id"] for r in rows],
        )
        members: dict[str, list[str]] = {}
        for p in prows:
            members.setdefault(p["conversation_id"], []).append(p["agent_id"])
        return [
            Conversation(
                id=r["id"],
                type=ConversationType(r["type"]),
                title=r["title"],
                topic=r["topic"],
                participant_ids=members.get(r["id"], []),
                created_at=parse_dt(r["created_at"]),
                updated_at=parse_dt(r["updated_at"]),
            )
            for r in rows
        ]

    async def get_conversation(self, conv_id: str) -> Conversation | None:
        row = await self.db.fetchone("SELECT * FROM conversations WHERE id = ?", (conv_id,))
        return (await self._conversations([row]))[0] if row else None

    async def list_conversations(self) -> list[Conversation]:
        rows = await self.db.fetchall(
            "SELECT * FROM conversations ORDER BY updated_at DESC, rowid DESC"
        )
        return await self._conversations(rows)

    @staticmethod
    def stmt_touch_conversation(conv_id: str, when: datetime) -> Statement:
        return ("UPDATE conversations SET updated_at = ? WHERE id = ?", (iso(when), conv_id))

    # ------------------------------------------------------------------ messages

    @staticmethod
    def stmt_insert_message(msg: Message) -> Statement:
        return (
            "INSERT INTO messages (id, conversation_id, run_id, role, speaker_name, agent_json,"
            " provider_id, model_id, content, status, error, error_code, reply_to_id, stage,"
            " created_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (
                msg.id,
                msg.conversation_id,
                msg.run_id,
                msg.role.value,
                msg.speaker_name,
                msg.agent.model_dump_json() if msg.agent else None,
                msg.provider_id,
                msg.model_id,
                msg.content,
                msg.status.value,
                msg.error,
                msg.error_code.value if msg.error_code else None,
                msg.reply_to_id,
                msg.stage,
                iso(msg.created_at),
            ),
        )

    @staticmethod
    def stmt_append_content(message_id: str, delta: str) -> Statement:
        return ("UPDATE messages SET content = content || ? WHERE id = ?", (delta, message_id))

    @staticmethod
    def stmt_finish_message(msg: Message) -> Statement:
        return (
            "UPDATE messages SET content = ?, status = ?, error = ?, error_code = ? WHERE id = ?",
            (
                msg.content,
                msg.status.value,
                msg.error,
                msg.error_code.value if msg.error_code else None,
                msg.id,
            ),
        )

    async def get_message(self, message_id: str) -> Message | None:
        row = await self.db.fetchone("SELECT * FROM messages WHERE id = ?", (message_id,))
        return _message(row) if row else None

    async def list_messages(self, conv_id: str) -> list[Message]:
        rows = await self.db.fetchall(
            "SELECT * FROM messages WHERE conversation_id = ? ORDER BY ord", (conv_id,)
        )
        return [_message(r) for r in rows]

    async def message_ord(self, message_id: str) -> int:
        row = await self.db.fetchone("SELECT ord FROM messages WHERE id = ?", (message_id,))
        return int(row["ord"]) if row else 0

    async def messages_after(self, conv_id: str, after_ord: int) -> list[tuple[int, Message]]:
        rows = await self.db.fetchall(
            "SELECT * FROM messages WHERE conversation_id = ? AND ord > ? ORDER BY ord",
            (conv_id, after_ord),
        )
        return [(int(r["ord"]), _message(r)) for r in rows]

    async def streaming_messages(self, run_id: str | None = None) -> list[Message]:
        if run_id is None:
            rows = await self.db.fetchall(
                "SELECT * FROM messages WHERE status = 'streaming' ORDER BY ord"
            )
        else:
            rows = await self.db.fetchall(
                "SELECT * FROM messages WHERE status = 'streaming' AND run_id = ? ORDER BY ord",
                (run_id,),
            )
        return [_message(r) for r in rows]

    # ------------------------------------------------------------------ runs

    @staticmethod
    def stmt_insert_run(run: Run) -> Statement:
        return (
            "INSERT INTO runs (id, conversation_id, status, participants_json, coordinator_json,"
            " input_tokens, output_tokens, cost_usd, error, error_code, created_at, finished_at)"
            " VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (
                run.id,
                run.conversation_id,
                run.status.value,
                json.dumps([p.model_dump(mode="json") for p in run.participants]),
                run.coordinator.model_dump_json() if run.coordinator else None,
                run.usage.input_tokens,
                run.usage.output_tokens,
                run.usage.cost_usd,
                run.error,
                run.error_code.value if run.error_code else None,
                iso(run.created_at),
                iso(run.finished_at) if run.finished_at else None,
            ),
        )

    @staticmethod
    def stmt_finish_run(run: Run) -> Statement:
        return (
            "UPDATE runs SET status = ?, error = ?, error_code = ?, finished_at = ? WHERE id = ?",
            (
                run.status.value,
                run.error,
                run.error_code.value if run.error_code else None,
                iso(run.finished_at) if run.finished_at else None,
                run.id,
            ),
        )

    @staticmethod
    def stmt_add_usage(run_id: str, usage: Usage, billed_cost: float | None) -> Statement:
        return (
            "UPDATE runs SET input_tokens = input_tokens + ?, output_tokens = output_tokens + ?,"
            " cost_usd = CASE WHEN ? IS NULL THEN cost_usd ELSE COALESCE(cost_usd, 0) + ? END"
            " WHERE id = ?",
            (usage.input_tokens, usage.output_tokens, billed_cost, billed_cost, run_id),
        )

    async def get_run(self, run_id: str) -> Run | None:
        row = await self.db.fetchone("SELECT * FROM runs WHERE id = ?", (run_id,))
        return _run(row) if row else None

    async def running_runs(self) -> list[Run]:
        rows = await self.db.fetchall("SELECT * FROM runs WHERE status = 'running' ORDER BY rowid")
        return [_run(r) for r in rows]

    # ------------------------------------------------------------------ run events

    @staticmethod
    def stmt_insert_event(run_id: str, seq: int, type_: str, data: str) -> Statement:
        return (
            "INSERT INTO run_events (run_id, seq, type, data) VALUES (?, ?, ?, ?)",
            (run_id, seq, type_, data),
        )

    async def last_event_seq(self, run_id: str) -> int:
        row = await self.db.fetchone(
            "SELECT MAX(seq) AS s FROM run_events WHERE run_id = ?", (run_id,)
        )
        return int(row["s"]) if row and row["s"] is not None else 0

    async def events_after(self, run_id: str, after: int, limit: int = 200) -> list[aiosqlite.Row]:
        return await self.db.fetchall(
            "SELECT seq, type, data FROM run_events WHERE run_id = ? AND seq > ?"
            " ORDER BY seq LIMIT ?",
            (run_id, after, limit),
        )

    # ------------------------------------------------------------------ sessions

    async def get_session(self, conv_id: str, agent_id: str) -> tuple[str, int] | None:
        row = await self.db.fetchone(
            "SELECT session_id, seen_ord FROM session_map"
            " WHERE conversation_id = ? AND agent_id = ?",
            (conv_id, agent_id),
        )
        return (row["session_id"], int(row["seen_ord"])) if row else None

    async def put_session(self, conv_id: str, agent_id: str, session_id: str) -> None:
        await self.db.execute(
            "INSERT OR IGNORE INTO session_map (conversation_id, agent_id, session_id)"
            " VALUES (?, ?, ?)",
            (conv_id, agent_id, session_id),
        )

    @staticmethod
    def stmt_mark_seen(conv_id: str, agent_id: str, ord_: int) -> Statement:
        return (
            "UPDATE session_map SET seen_ord = MAX(seen_ord, ?)"
            " WHERE conversation_id = ? AND agent_id = ?",
            (ord_, conv_id, agent_id),
        )
