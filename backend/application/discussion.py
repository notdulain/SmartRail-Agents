"""Bounded group discussion as a Microsoft Agent Framework workflow.

One exchange is a chain of executors, one per turn, executed strictly in sequence:

    stage 0  coordinator agenda
    stage 1  every participant contributes (conversation participant order)
    stage 2  every participant replies to the next participant's stage-1 contribution
    stage 3  coordinator summary (agreements, disagreements, unanswered questions)

Each :class:`OpenCodeExecutor` performs one model turn through ``runtime.stream`` (via
:meth:`RunManager.run_turn`). The framework provides ordering and cancellation: cancelling the
run task cancels the in-flight executor and no later executor ever starts. Turn results travel
through the shared :class:`DiscussionDriver`, not through framework messages, so the workflow
payload is just a baton.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import TYPE_CHECKING

from agent_framework import Executor, WorkflowBuilder, WorkflowContext, handler

from backend.contracts.models import AgentSnapshot, ErrorCode, Message

from . import prompts

if TYPE_CHECKING:
    from .runs import ActiveRun, RunManager, TurnOutcome


@dataclass(frozen=True)
class Turn:
    stage: int
    speaker: AgentSnapshot
    peer: AgentSnapshot | None = None  # stage 2: whose stage-1 contribution is answered


@dataclass
class Baton:
    """Workflow payload passed from turn to turn."""

    index: int = 0


def plan_turns(coordinator: AgentSnapshot, participants: list[AgentSnapshot]) -> list[Turn]:
    """Deterministic turn order for one bounded exchange."""
    count = len(participants)
    turns = [Turn(0, coordinator)]
    turns += [Turn(1, p) for p in participants]
    # Each participant answers the next one in order (the last wraps to the first), so every
    # stage-1 contribution is answered exactly once and nobody answers themselves.
    turns += [Turn(2, p, participants[(i + 1) % count]) for i, p in enumerate(participants)]
    turns.append(Turn(3, coordinator))
    return turns


@dataclass
class DiscussionDriver:
    manager: RunManager
    active: ActiveRun
    participants: list[AgentSnapshot]
    coordinator: AgentSnapshot
    round1: dict[str, Message] = field(default_factory=dict)  # agent_id -> stage-1 message
    outcome: TurnOutcome | None = None  # first turn that did not complete halts the exchange

    async def take_turn(self, turn: Turn) -> None:
        if self.outcome is not None:
            return  # an earlier turn failed or paused the run; nobody else speaks
        active, store = self.active, self.manager.store
        conversation = active.conversation
        assert conversation is not None
        topic = conversation.topic or conversation.title
        names = [p.name for p in self.participants]
        speaker = turn.speaker
        coordinating = turn.stage in (0, 3)

        # A missing or outdated session (the agent's directory changed) has seen nothing.
        found = await store.usable_session(
            conversation.id, speaker.agent_id, speaker.working_directory
        )
        unseen = await store.messages_after(conversation.id, found.seen_ord if found else 0)
        messages = [m for _, m in unseen]
        files = await store.attachment_texts([m.id for m in messages if m.attachments])
        keep = {active.user_message.id} if active.user_message else set()
        transcript = prompts.format_transcript(messages, files, keep)

        reply_to_id: str | None = None
        if turn.stage == 0:
            task = prompts.agenda_task(names)
        elif turn.stage == 1:
            task = prompts.contribution_task()
        elif turn.stage == 2:
            assert turn.peer is not None
            task = prompts.peer_reply_task(turn.peer.name)
            reply_to_id = self.round1[turn.peer.agent_id].id
        else:
            task = prompts.summary_task()

        system = prompts.system_text(
            speaker.persona,
            active.brief,
            prompts.group_framing(speaker.name, names, topic, coordinator=coordinating),
            prompts.tools_text(speaker),
        )
        outcome = await self.manager.run_turn(
            active,
            speaker=speaker,
            stage=turn.stage,
            reply_to_id=reply_to_id,
            system=system,
            user_text=prompts.turn_text(task, topic, transcript),
            max_tokens=(
                active.coordinator_max_tokens if coordinating else active.participant_max_tokens
            ),
        )
        if outcome.kind == "complete" and outcome.message is not None:
            if turn.stage == 1:
                self.round1[speaker.agent_id] = outcome.message
        else:
            self.outcome = outcome


class OpenCodeExecutor(Executor):
    """Agent Framework executor that performs one agent turn through the OpenCode runtime."""

    def __init__(self, executor_id: str, turn: Turn, driver: DiscussionDriver) -> None:
        super().__init__(id=executor_id)
        self.turn = turn
        self.driver = driver

    @handler
    async def run_turn(self, baton: Baton, ctx: WorkflowContext[Baton]) -> None:
        await self.driver.take_turn(self.turn)
        await ctx.send_message(Baton(baton.index + 1))


async def run_group(manager: RunManager, active: ActiveRun) -> TurnOutcome:
    """Run one bounded exchange and return its outcome (complete / failed / paused)."""
    from .runs import TurnOutcome

    run = active.run
    if run.coordinator is None:
        return TurnOutcome(
            "failed", code=ErrorCode.VALIDATION_ERROR, error="no coordinator configured"
        )
    driver = DiscussionDriver(manager, active, list(run.participants), run.coordinator)
    turns = plan_turns(run.coordinator, driver.participants)
    executors = [
        OpenCodeExecutor(f"turn-{i:04d}-stage{t.stage}", t, driver) for i, t in enumerate(turns)
    ]
    workflow = (
        WorkflowBuilder(start_executor=executors[0], max_iterations=len(executors) + 10)
        .add_chain(executors)
        .build()
    )
    await workflow.run(Baton())
    return driver.outcome or TurnOutcome("complete")
