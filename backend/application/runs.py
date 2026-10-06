"""Run lifecycle: one active run at a time, persisted event log, Stop, restart recovery.

Every SSE event is written to ``run_events`` (in the same transaction as the state change it
describes) before subscribers are woken. Subscribers read the log from SQLite, so reconnecting
replays history without ever touching the runtime.
"""

from __future__ import annotations

import asyncio
import logging
from collections.abc import AsyncIterator, Sequence
from contextlib import aclosing
from dataclasses import dataclass, field
from typing import Any, Literal

from backend.contracts.models import (
    TERMINAL_EVENT_TYPES,
    AgentSnapshot,
    Conversation,
    ConversationType,
    ErrorCode,
    Message,
    MessageCompletedEvent,
    MessageDeltaEvent,
    MessageRole,
    MessageStartedEvent,
    MessageStatus,
    ProvidersResponse,
    Run,
    RunCancelledEvent,
    RunCompletedEvent,
    RunFailedEvent,
    RunPausedEvent,
    RunStartedEvent,
    RunStatus,
    Usage,
)
from backend.contracts.runtime import (
    Completed,
    CompletionRequest,
    Failed,
    OpenCodeRuntime,
    RuntimeUnavailableError,
    TextDelta,
)

from . import budget, prompts
from .db import Statement
from .errors import AppError
from .store import Store
from .util import new_id, utcnow

log = logging.getLogger(__name__)

INTERRUPTED = "interrupted by restart"


class Notifier:
    """Wakes event-stream subscribers; each waiter is a one-shot future."""

    def __init__(self) -> None:
        self._waiters: set[asyncio.Future[None]] = set()

    def waiter(self) -> asyncio.Future[None]:
        fut: asyncio.Future[None] = asyncio.get_running_loop().create_future()
        self._waiters.add(fut)
        return fut

    def discard(self, fut: asyncio.Future[None]) -> None:
        self._waiters.discard(fut)

    def notify(self) -> None:
        waiters, self._waiters = self._waiters, set()
        for fut in waiters:
            if not fut.done():
                fut.set_result(None)


@dataclass
class TurnOutcome:
    kind: Literal["complete", "failed", "paused"]
    message: Message | None = None
    code: ErrorCode | None = None
    error: str | None = None


@dataclass
class ActiveRun:
    run: Run
    next_seq: int = 1
    conversation: Conversation | None = None
    user_message: Message | None = None
    brief: str = ""
    participant_max_tokens: int = 1024
    coordinator_max_tokens: int = 2048
    notifier: Notifier = field(default_factory=Notifier)
    task: asyncio.Task[None] | None = None
    current_session: str | None = None
    stop_reason: Literal["user", "shutdown"] | None = None
    finishing: asyncio.Future[None] | None = None
    catalog: ProvidersResponse | None = None

    @property
    def run_id(self) -> str:
        return self.run.id


class RunManager:
    def __init__(self, store: Store, runtime: OpenCodeRuntime) -> None:
        self.store = store
        self.runtime = runtime
        self.active: ActiveRun | None = None  # the run that blocks new sends
        self.tasks: dict[str, ActiveRun] = {}  # runs whose task has not fully finished
        self._lock = asyncio.Lock()

    # ------------------------------------------------------------------ events

    async def emit(
        self,
        active: ActiveRun,
        cls: type[Any],
        extra: Sequence[Statement] = (),
        **fields: Any,
    ) -> None:
        """Persist (with ``extra`` state changes, atomically) and then publish one event."""
        seq = active.next_seq
        active.next_seq += 1
        event = cls(seq=seq, run_id=active.run_id, **fields)
        stmts = [
            *extra,
            self.store.stmt_insert_event(active.run_id, seq, event.type, event.model_dump_json()),
        ]
        await self.store.db.tx(stmts)
        active.notifier.notify()

    async def stream_events(self, run_id: str, after: int) -> AsyncIterator[dict[str, str]]:
        """SSE frames for ``run_id`` with ``seq > after``; ends after the terminal event."""
        while True:
            active = self.tasks.get(run_id)
            waiter = active.notifier.waiter() if active else None
            try:
                rows = await self.store.events_after(run_id, after)
                for row in rows:
                    after = int(row["seq"])
                    yield {"id": str(after), "event": row["type"], "data": row["data"]}
                    if row["type"] in TERMINAL_EVENT_TYPES:
                        return
                if rows:
                    continue
                if active is None or waiter is None:
                    return  # nothing is producing events any more
                await waiter
            finally:
                if active and waiter:
                    active.notifier.discard(waiter)

    # ------------------------------------------------------------------ starting

    async def start_run(
        self,
        conversation: Conversation,
        content: str,
        participants: list[AgentSnapshot],
        coordinator: AgentSnapshot | None,
        *,
        brief: str,
        participant_max_tokens: int,
        coordinator_max_tokens: int,
    ) -> tuple[Run, Message]:
        async with self._lock:
            if self.active is not None:
                raise self.run_active_error()
            now = utcnow()
            run = Run(
                id=new_id("run"),
                conversation_id=conversation.id,
                status=RunStatus.RUNNING,
                participants=participants,
                coordinator=coordinator,
                created_at=now,
            )
            user_message = Message(
                id=new_id("msg"),
                conversation_id=conversation.id,
                run_id=run.id,
                role=MessageRole.USER,
                speaker_name="You",
                content=content,
                status=MessageStatus.COMPLETE,
                created_at=now,
            )
            await self.store.db.tx(
                [
                    self.store.stmt_insert_run(run),
                    self.store.stmt_insert_message(user_message),
                    self.store.stmt_touch_conversation(conversation.id, now),
                ]
            )
            active = ActiveRun(
                run=run,
                conversation=conversation,
                user_message=user_message,
                brief=brief,
                participant_max_tokens=participant_max_tokens,
                coordinator_max_tokens=coordinator_max_tokens,
            )
            self.active = active
            self.tasks[run.id] = active
            active.task = asyncio.create_task(self._main(active), name=f"run-{run.id}")
            return run, user_message

    def run_active_error(self) -> AppError:
        return AppError(
            409,
            ErrorCode.RUN_ACTIVE,
            "Another run is still in progress. Wait for it to finish or stop it first.",
        )

    # ------------------------------------------------------------------ main task

    async def _main(self, active: ActiveRun) -> None:
        try:
            await self.emit(active, RunStartedEvent, run=active.run)
            if active.conversation and active.conversation.type is ConversationType.DIRECT:
                outcome = await self._run_direct(active)
            else:
                from .discussion import run_group

                outcome = await run_group(self, active)
            await self._finish_from_outcome(active, outcome)
        except asyncio.CancelledError:
            await self._on_cancelled(active)
        except Exception as exc:
            log.exception("run %s crashed", active.run_id)
            await self._on_crash(active, exc)
        finally:
            if self.active is active:
                self.active = None
            self.tasks.pop(active.run_id, None)
            active.notifier.notify()

    async def _run_direct(self, active: ActiveRun) -> TurnOutcome:
        assert active.user_message is not None
        speaker = active.run.participants[0]
        return await self.run_turn(
            active,
            speaker=speaker,
            stage=None,
            reply_to_id=None,
            system=prompts.system_text(speaker.persona, active.brief),
            user_text=active.user_message.content,
            max_tokens=active.participant_max_tokens,
        )

    async def _finish_from_outcome(self, active: ActiveRun, outcome: TurnOutcome) -> None:
        if outcome.kind == "complete":
            await self._finish(active, RunStatus.COMPLETED)
        elif outcome.kind == "paused":
            await self._finish(
                active,
                RunStatus.PAUSED,
                error=outcome.error,
                code=outcome.code or ErrorCode.BUDGET_EXHAUSTED,
                reason=outcome.error or "paused",
            )
        else:
            await self._finish(active, RunStatus.FAILED, error=outcome.error, code=outcome.code)

    async def _finish(
        self,
        active: ActiveRun,
        status: RunStatus,
        error: str | None = None,
        code: ErrorCode | None = None,
        reason: str | None = None,
    ) -> None:
        """Write the terminal state + event exactly once, even if Stop races with completion."""
        if active.finishing is None:
            active.finishing = asyncio.ensure_future(
                self._do_finish(active, status, error, code, reason)
            )
        await asyncio.shield(active.finishing)

    async def _do_finish(
        self,
        active: ActiveRun,
        status: RunStatus,
        error: str | None,
        code: ErrorCode | None,
        reason: str | None,
    ) -> None:
        # Release the "one active run" slot before the terminal event is visible, so a client
        # that reacts to it by sending again is never refused with a stale 409.
        if self.active is active:
            self.active = None
        current = await self.store.get_run(active.run_id)
        assert current is not None
        run = current.model_copy(
            update={
                "status": status,
                "error": error,
                "error_code": code,
                "finished_at": utcnow(),
            }
        )
        extra: list[Statement] = [self.store.stmt_finish_run(run)]
        if status is RunStatus.COMPLETED:
            await self.emit(active, RunCompletedEvent, extra, run=run)
        elif status is RunStatus.FAILED:
            await self.emit(active, RunFailedEvent, extra, run=run)
        elif status is RunStatus.CANCELLED:
            await self.emit(active, RunCancelledEvent, extra, run=run)
        else:
            await self.emit(active, RunPausedEvent, extra, run=run, reason=reason or "paused")

    async def _finalize_streaming(
        self,
        active: ActiveRun,
        status: MessageStatus,
        error: str | None = None,
        code: ErrorCode | None = None,
    ) -> None:
        for msg in await self.store.streaming_messages(active.run_id):
            done = msg.model_copy(update={"status": status, "error": error, "error_code": code})
            extra = [self.store.stmt_finish_message(done)]
            if msg.content and msg.agent:
                ord_ = await self.store.message_ord(msg.id)
                extra.append(
                    self.store.stmt_mark_seen(msg.conversation_id, msg.agent.agent_id, ord_)
                )
            await self.emit(active, MessageCompletedEvent, extra, message=done)

    async def _on_cancelled(self, active: ActiveRun) -> None:
        if active.finishing is not None:  # already completing; let that win
            await asyncio.shield(active.finishing)
            return
        try:
            if active.current_session:
                await self._abort(active.current_session)
            if active.stop_reason == "shutdown":
                await self._finalize_streaming(
                    active, MessageStatus.FAILED, INTERRUPTED, ErrorCode.RUNTIME_ERROR
                )
                await self._finish(active, RunStatus.FAILED, INTERRUPTED, ErrorCode.RUNTIME_ERROR)
            else:
                await self._finalize_streaming(active, MessageStatus.CANCELLED)
                await self._finish(active, RunStatus.CANCELLED)
        except Exception:
            log.exception("failed to record cancellation of run %s", active.run_id)

    async def _on_crash(self, active: ActiveRun, exc: Exception) -> None:
        if active.finishing is not None:
            return
        message = f"internal error: {exc}"
        try:
            await self._finalize_streaming(
                active, MessageStatus.FAILED, message, ErrorCode.RUNTIME_ERROR
            )
            await self._finish(active, RunStatus.FAILED, message, ErrorCode.RUNTIME_ERROR)
        except Exception:
            log.exception("failed to record crash of run %s", active.run_id)

    async def _abort(self, session_id: str) -> None:
        try:
            await self.runtime.abort(session_id)
        except Exception:
            log.warning("runtime abort failed for session %s", session_id, exc_info=True)

    # ------------------------------------------------------------------ stop / shutdown

    async def stop(self, run_id: str) -> Run | None:
        active = self.tasks.get(run_id)
        if active is not None and active.task is not None:
            if active.stop_reason is None:
                active.stop_reason = "user"
            active.task.cancel()
            await asyncio.wait({active.task})
        run = await self.store.get_run(run_id)
        if run is not None and run.status is RunStatus.RUNNING:
            # Orphan (no task in this process); close it out so it cannot linger.
            await self.fail_interrupted(run, cancelled=True)
            run = await self.store.get_run(run_id)
        return run

    async def shutdown(self) -> None:
        for active in list(self.tasks.values()):
            if active.task is not None:
                active.stop_reason = "shutdown"
                active.task.cancel()
        tasks = [a.task for a in self.tasks.values() if a.task is not None]
        if tasks:
            await asyncio.wait(tasks, timeout=10)

    # ------------------------------------------------------------------ recovery

    async def recover_interrupted(self) -> None:
        for run in await self.store.running_runs():
            await self.fail_interrupted(run)

    async def fail_interrupted(self, run: Run, cancelled: bool = False) -> None:
        active = ActiveRun(run=run, next_seq=await self.store.last_event_seq(run.id) + 1)
        if cancelled:
            await self._finalize_streaming(active, MessageStatus.CANCELLED)
            await self._finish(active, RunStatus.CANCELLED)
        else:
            await self._finalize_streaming(
                active, MessageStatus.FAILED, INTERRUPTED, ErrorCode.RUNTIME_ERROR
            )
            await self._finish(active, RunStatus.FAILED, INTERRUPTED, ErrorCode.RUNTIME_ERROR)

    # ------------------------------------------------------------------ one model turn

    async def _price(self, active: ActiveRun, speaker: AgentSnapshot):
        if active.catalog is None:
            try:
                active.catalog = await self.runtime.list_providers()
            except Exception:
                return None
        return budget.find_price(active.catalog, speaker.provider_id, speaker.model_id)

    async def _session_for(self, active: ActiveRun, speaker: AgentSnapshot) -> str:
        assert active.conversation is not None
        conv_id = active.conversation.id
        found = await self.store.get_session(conv_id, speaker.agent_id)
        if found:
            return found[0]
        session_id = await self.runtime.create_session(
            f"SmartRail: {active.conversation.title} / {speaker.name}"
        )
        await self.store.put_session(conv_id, speaker.agent_id, session_id)
        found = await self.store.get_session(conv_id, speaker.agent_id)
        return found[0] if found else session_id

    async def run_turn(
        self,
        active: ActiveRun,
        *,
        speaker: AgentSnapshot,
        stage: int | None,
        reply_to_id: str | None,
        system: str,
        user_text: str,
        max_tokens: int,
    ) -> TurnOutcome:
        """One agent turn: budget gate, message row, runtime stream, final state."""
        assert active.conversation is not None
        conv_id = active.conversation.id
        is_openrouter = speaker.provider_id == budget.OPENROUTER
        price = None
        if is_openrouter:
            price = await self._price(active, speaker)
            settings = await self.store.get_settings()
            estimate = budget.estimate_request_cost(price, system + user_text, max_tokens)
            if budget.would_exceed(
                settings.openrouter_spent_usd, estimate, settings.openrouter_monthly_budget_usd
            ):
                reason = (
                    f"OpenRouter monthly budget reached before {speaker.name}'s turn: "
                    f"${settings.openrouter_spent_usd:.4f} spent, up to ${estimate:.4f} "
                    f"needed, budget ${settings.openrouter_monthly_budget_usd:.2f}. "
                    "Raise the budget in Settings and send a new message to continue."
                )
                return TurnOutcome("paused", code=ErrorCode.BUDGET_EXHAUSTED, error=reason)

        message = Message(
            id=new_id("msg"),
            conversation_id=conv_id,
            run_id=active.run_id,
            role=MessageRole.AGENT,
            speaker_name=speaker.name,
            agent=speaker,
            provider_id=speaker.provider_id,
            model_id=speaker.model_id,
            content="",
            status=MessageStatus.STREAMING,
            reply_to_id=reply_to_id,
            stage=stage,
            created_at=utcnow(),
        )
        await self.emit(
            active,
            MessageStartedEvent,
            [self.store.stmt_insert_message(message)],
            message=message,
        )
        own_ord = await self.store.message_ord(message.id)

        text = ""
        got_text = False
        final: Completed | None = None
        failure: Failed | None = None
        try:
            try:
                session_id = await self._session_for(active, speaker)
                active.current_session = session_id
                request = CompletionRequest(
                    session_id=session_id,
                    provider_id=speaker.provider_id,
                    model_id=speaker.model_id,
                    system=system,
                    user_text=user_text,
                    max_output_tokens=max_tokens,
                )
                async with aclosing(self.runtime.stream(request)) as events:
                    async for event in events:
                        if final is not None or failure is not None:
                            continue  # terminal event already seen; let the iterator end
                        if isinstance(event, TextDelta):
                            if not event.text:
                                continue
                            got_text = True
                            text += event.text
                            await self.emit(
                                active,
                                MessageDeltaEvent,
                                [self.store.stmt_append_content(message.id, event.text)],
                                message_id=message.id,
                                delta=event.text,
                            )
                        elif isinstance(event, Completed):
                            final = event
                        elif isinstance(event, Failed):
                            failure = event
                if final is None and failure is None:
                    failure = Failed(ErrorCode.RUNTIME_ERROR, "runtime ended without a result")
            except RuntimeUnavailableError as exc:
                failure = Failed(
                    ErrorCode.PROVIDER_UNAVAILABLE, str(exc) or "OpenCode is unreachable"
                )
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                log.exception("runtime error during turn of %s", speaker.name)
                failure = Failed(ErrorCode.RUNTIME_ERROR, f"runtime error: {exc}")
        except asyncio.CancelledError:
            if is_openrouter and got_text:
                await self._record_partial_spend(active, price, system + user_text, text)
            raise
        finally:
            active.current_session = None

        seen: list[Statement] = []
        if got_text or final is not None:
            seen.append(self.store.stmt_mark_seen(conv_id, speaker.agent_id, own_ord))
        touch = self.store.stmt_touch_conversation(conv_id, utcnow())

        if final is not None:
            usage: Usage = final.usage
            billed: float | None = None
            extra: list[Statement] = []
            if is_openrouter:
                billed = budget.actual_cost(
                    usage, price, budget.estimate_tokens(system + user_text)
                )
                extra.append(self.store.stmt_add_spend(billed))
            done = message.model_copy(
                update={"content": final.text or text, "status": MessageStatus.COMPLETE}
            )
            await self.emit(
                active,
                MessageCompletedEvent,
                [
                    self.store.stmt_finish_message(done),
                    self.store.stmt_add_usage(active.run_id, usage, billed),
                    *extra,
                    *seen,
                    touch,
                ],
                message=done,
            )
            if stage == 0 or stage == 3:
                if usage.output_tokens >= active.coordinator_max_tokens:
                    reason = (
                        f"Coordinator output allowance exhausted ({usage.output_tokens} of "
                        f"{active.coordinator_max_tokens} tokens). Raise the coordinator limit in "
                        "Settings and send a new message to continue."
                    )
                    return TurnOutcome("paused", done, ErrorCode.BUDGET_EXHAUSTED, reason)
            return TurnOutcome("complete", done)

        assert failure is not None
        done = message.model_copy(
            update={
                "content": text,
                "status": MessageStatus.FAILED,
                "error": failure.message,
                "error_code": failure.code,
            }
        )
        await self.emit(
            active,
            MessageCompletedEvent,
            [self.store.stmt_finish_message(done), *seen, touch],
            message=done,
        )
        return TurnOutcome(
            "failed",
            done,
            failure.code,
            f"{speaker.name}: {failure.message}",
        )

    async def _record_partial_spend(
        self, active: ActiveRun, price, input_text: str, partial_text: str
    ) -> None:
        """A stopped OpenRouter call still costs money; count an estimate so budgets stay honest."""
        usage = Usage(
            input_tokens=budget.estimate_tokens(input_text),
            output_tokens=budget.estimate_tokens(partial_text),
        )
        cost = budget.price_cost(price, usage.input_tokens, usage.output_tokens)
        try:
            await self.store.db.tx(
                [
                    self.store.stmt_add_spend(cost),
                    self.store.stmt_add_usage(active.run_id, usage, cost),
                ]
            )
        except Exception:
            log.warning("could not record partial spend", exc_info=True)
