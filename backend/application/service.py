"""Business rules behind the HTTP routes."""

from __future__ import annotations

from backend.config import AppConfig
from backend.contracts.models import (
    Agent,
    AgentCreate,
    AgentUpdate,
    Conversation,
    ConversationCreate,
    ConversationDetail,
    ConversationType,
    ErrorCode,
    ProvidersResponse,
    Run,
    SendMessageRequest,
    SendMessageResponse,
    Settings,
    SettingsUpdate,
)
from backend.contracts.runtime import OpenCodeRuntime, RuntimeUnavailableError

from .db import Database
from .errors import AppError, invalid, not_found
from .export import render_markdown
from .runs import RunManager
from .store import Store, snapshot_of
from .util import new_id, utcnow


class Service:
    def __init__(self, config: AppConfig, runtime: OpenCodeRuntime) -> None:
        self.config = config
        self.runtime = runtime
        self.db = Database(config.db_path)
        self.store = Store(self.db)
        self.runs = RunManager(self.store, runtime)

    # ------------------------------------------------------------------ lifecycle

    async def start(self) -> None:
        await self.db.open()
        await self.store.init_settings()
        await self.runs.recover_interrupted()

    async def stop(self) -> None:
        await self.runs.shutdown()
        try:
            await self.runtime.aclose()
        finally:
            await self.db.close()

    # ------------------------------------------------------------------ providers

    async def providers(self, refresh: bool) -> ProvidersResponse:
        try:
            return await self.runtime.list_providers(refresh)
        except RuntimeUnavailableError as exc:
            raise AppError(
                503, ErrorCode.PROVIDER_UNAVAILABLE, f"OpenCode is unreachable: {exc}"
            ) from exc

    async def _validate_model(
        self, provider_id: str, model_id: str, agent_id: str | None = None
    ) -> None:
        """Reject unknown/disconnected providers and models; never substitute another one."""
        problem: tuple[ErrorCode, str] | None = None
        for refresh in (False, True):  # a stale catalog gets one forced refresh before rejecting
            catalog = await self.providers(refresh)
            provider = next((p for p in catalog.providers if p.id == provider_id), None)
            if provider is None:
                problem = (
                    ErrorCode.PROVIDER_UNAVAILABLE,
                    f"Provider {provider_id!r} is not known to OpenCode.",
                )
            elif not provider.connected:
                problem = (
                    ErrorCode.PROVIDER_UNAVAILABLE,
                    f"Provider {provider.name!r} is not connected. Connect it in OpenCode, "
                    "then refresh providers.",
                )
            elif not any(m.id == model_id for m in provider.models):
                problem = (
                    ErrorCode.MODEL_UNAVAILABLE,
                    f"Model {model_id!r} is not offered by provider {provider.name!r}.",
                )
            else:
                return
        assert problem is not None
        raise AppError(422, problem[0], problem[1], agent_id)

    # ------------------------------------------------------------------ agents

    async def list_agents(self, include_archived: bool) -> list[Agent]:
        return await self.store.list_agents(include_archived)

    async def create_agent(self, body: AgentCreate) -> Agent:
        await self._validate_model(body.provider_id, body.model_id)
        now = utcnow()
        agent = Agent(
            id=new_id("agt"),
            name=body.name.strip() or body.name,
            persona=body.persona,
            provider_id=body.provider_id,
            model_id=body.model_id,
            revision=1,
            archived=False,
            created_at=now,
            updated_at=now,
        )
        if not agent.name.strip():
            raise invalid("name must not be blank")
        await self.store.insert_agent(agent)
        return agent

    async def update_agent(self, agent_id: str, body: AgentUpdate) -> Agent:
        agent = await self.store.get_agent(agent_id)
        if agent is None:
            raise not_found("Agent", agent_id)
        changes = body.model_dump(exclude_none=True)
        if "name" in changes:
            changes["name"] = changes["name"].strip()
            if not changes["name"]:
                raise invalid("name must not be blank", agent_id)
        archived = changes.pop("archived", None)

        edited = {k: v for k, v in changes.items() if getattr(agent, k) != v}
        if "provider_id" in changes or "model_id" in changes:
            await self._validate_model(
                changes.get("provider_id", agent.provider_id),
                changes.get("model_id", agent.model_id),
                agent_id,
            )
        update: dict[str, object] = dict(edited)
        if edited:
            update["revision"] = agent.revision + 1
        if archived is not None and archived != agent.archived:
            update["archived"] = archived
        if update:
            update["updated_at"] = utcnow()
            agent = agent.model_copy(update=update)
            await self.store.save_agent(agent)
        return agent

    # ------------------------------------------------------------------ conversations

    async def list_conversations(self) -> list[Conversation]:
        return await self.store.list_conversations()

    async def create_conversation(self, body: ConversationCreate) -> Conversation:
        agents = await self.store.get_agents(body.participant_ids)
        for agent_id in body.participant_ids:
            agent = agents.get(agent_id)
            if agent is None:
                raise not_found("Agent", agent_id)
            if agent.archived:
                raise invalid(
                    f"Agent {agent.name!r} is archived and cannot join new conversations.",
                    agent_id,
                )
        if body.type is ConversationType.DIRECT:
            default_title = agents[body.participant_ids[0]].name
            topic = None
        else:
            topic = (body.topic or "").strip()
            default_title = topic[:80]
        title = (body.title or "").strip() or default_title
        now = utcnow()
        conv = Conversation(
            id=new_id("cnv"),
            type=body.type,
            title=title,
            topic=topic,
            participant_ids=list(body.participant_ids),
            created_at=now,
            updated_at=now,
        )
        await self.store.insert_conversation(conv)
        return conv

    async def get_conversation(self, conv_id: str) -> ConversationDetail:
        conv = await self.store.get_conversation(conv_id)
        if conv is None:
            raise not_found("Conversation", conv_id)
        active = self.runs.active
        return ConversationDetail(
            conversation=conv,
            messages=await self.store.list_messages(conv_id),
            active_run_id=active.run_id
            if active and active.run.conversation_id == conv_id
            else None,
        )

    async def send_message(self, conv_id: str, body: SendMessageRequest) -> SendMessageResponse:
        conv = await self.store.get_conversation(conv_id)
        if conv is None:
            raise not_found("Conversation", conv_id)
        if self.runs.active is not None:
            raise self.runs.run_active_error()
        content = body.content
        if not content.strip():
            raise invalid("content must not be blank")

        agents = await self.store.get_agents(conv.participant_ids)
        snapshots = []
        for agent_id in conv.participant_ids:
            agent = agents.get(agent_id)
            if agent is None:
                raise not_found("Agent", agent_id)
            if agent.archived:
                raise invalid(
                    f"Agent {agent.name!r} is archived. Unarchive it before continuing "
                    "this conversation.",
                    agent_id,
                )
            snapshots.append(snapshot_of(agent))

        settings = await self.store.get_settings()
        coordinator = None
        if conv.type is ConversationType.GROUP:
            coordinator = await self._coordinator(settings)

        run, user_message = await self.runs.start_run(
            conv,
            content,
            snapshots,
            coordinator,
            brief=settings.project_brief,
            participant_max_tokens=settings.participant_max_tokens,
            coordinator_max_tokens=settings.coordinator_max_tokens,
        )
        return SendMessageResponse(run_id=run.id, user_message_id=user_message.id)

    async def _coordinator(self, settings: Settings):
        coord_id = settings.coordinator_agent_id
        if not coord_id:
            raise invalid(
                "No coordinator agent is set. Choose one in Settings before starting a "
                "group discussion."
            )
        agent = await self.store.get_agent(coord_id)
        if agent is None:
            raise invalid("The configured coordinator agent no longer exists.", coord_id)
        if agent.archived:
            raise invalid(
                f"The coordinator agent {agent.name!r} is archived. Choose another coordinator "
                "in Settings.",
                coord_id,
            )
        return snapshot_of(agent)

    # ------------------------------------------------------------------ runs

    async def get_run(self, run_id: str) -> Run:
        run = await self.store.get_run(run_id)
        if run is None:
            raise not_found("Run", run_id)
        return run

    async def stop_run(self, run_id: str) -> Run:
        run = await self.runs.stop(run_id)
        if run is None:
            raise not_found("Run", run_id)
        return run

    # ------------------------------------------------------------------ settings

    async def get_settings(self) -> Settings:
        return await self.store.get_settings()

    async def update_settings(self, body: SettingsUpdate) -> Settings:
        fields = {
            k: v
            for k, v in body.model_dump().items()
            if k in body.model_fields_set and v is not None
        }
        if "coordinator_agent_id" in body.model_fields_set:
            coord_id = body.coordinator_agent_id
            if coord_id is None:
                fields["coordinator_agent_id"] = None
            else:
                agent = await self.store.get_agent(coord_id)
                if agent is None:
                    raise not_found("Agent", coord_id)
                if agent.archived:
                    raise invalid(
                        f"Agent {agent.name!r} is archived and cannot be the coordinator.", coord_id
                    )
        await self.store.update_settings(fields)
        return await self.store.get_settings()

    # ------------------------------------------------------------------ export

    async def export_markdown(self, conv_id: str) -> tuple[str, str]:
        """Return (filename, markdown). Built in memory on request; never written to disk."""
        conv = await self.store.get_conversation(conv_id)
        if conv is None:
            raise not_found("Conversation", conv_id)
        messages = await self.store.list_messages(conv_id)
        agents = await self.store.get_agents(conv.participant_ids)
        # Historical names: the label each agent had when it first spoke here; only agents
        # that never spoke fall back to their current name.
        spoken: dict[str, str] = {}
        for msg in messages:
            if msg.agent is not None:
                spoken.setdefault(msg.agent.agent_id, msg.agent.name)
        participants = [
            spoken.get(a) or agents[a].name
            for a in conv.participant_ids
            if a in spoken or a in agents
        ]
        return render_markdown(conv, messages, participants)
