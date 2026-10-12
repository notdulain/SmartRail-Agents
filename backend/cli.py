"""Command line client: lets a coding agent (Claude Code, Codex, ...) consult SmartRail agents.

    smartrail agents                              who can be asked
    smartrail ask AGENT "question"                new direct conversation, prints the reply
    smartrail ask --conversation ID "follow-up"   continue that conversation
    smartrail reply ID                            collect the reply of an ask that timed out

Everything goes through the running app's HTTP API, so a consultation is an ordinary direct
conversation: it appears in the sidebar, honours the agent's persona, model, working
directory and tool access, and shares the app's one-run-at-a-time rule (``ask`` waits its turn).

Exit codes: 0 reply received, 1 the run failed / was cancelled / was paused, 2 bad usage or
unknown agent/conversation, 3 SmartRail is not running, 4 timed out waiting.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import sys
import time
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from typing import Any

import httpx

from backend.config import LOOPBACK, load_config

EXIT_OK, EXIT_FAILED, EXIT_USAGE, EXIT_UNREACHABLE, EXIT_TIMEOUT = 0, 1, 2, 3, 4

COMMANDS = ("agents", "ask", "reply")
DEFAULT_LABEL = "a coding agent"
DEFAULT_TIMEOUT_S = 600.0
POLL_S = 0.5
BUSY_RETRY_S = 2.0
MESSAGE_MAX = 20_000  # SendMessageRequest.content

_FRAME = (
    "[Consultation request from {label}, working on this project and calling through the "
    "SmartRail command line. Your reply goes back to it, not to a person: answer directly "
    "and concretely.]\n\n"
)


class CliError(Exception):
    def __init__(self, message: str, code: int = EXIT_FAILED, api_code: str | None = None) -> None:
        super().__init__(message)
        self.code = code
        self.api_code = api_code  # the backend's ErrorCode, when the error came from it


@dataclass(frozen=True)
class Reply:
    conversation_id: str
    run_id: str
    status: str  # completed | failed | cancelled | paused
    error: str | None
    messages: list[dict[str, Any]]  # the run's agent messages, in order

    @property
    def ok(self) -> bool:
        return self.status == "completed"

    @property
    def text(self) -> str:
        """The reply as plain text; several speakers (group conversations) get headings."""
        spoken = [m for m in self.messages if m["content"].strip()]
        if len(spoken) == 1:
            return spoken[0]["content"].strip()
        return "\n\n".join(f"## {m['speaker_name']}\n\n{m['content'].strip()}" for m in spoken)

    def as_json(self) -> dict[str, Any]:
        return {
            "conversation_id": self.conversation_id,
            "run_id": self.run_id,
            "status": self.status,
            "error": self.error,
            "replies": [
                {
                    "agent": m["speaker_name"],
                    "agent_id": (m.get("agent") or {}).get("agent_id"),
                    "status": m["status"],
                    "content": m["content"],
                    "error": m.get("error"),
                    "tool_calls": m.get("tool_calls", []),
                }
                for m in self.messages
            ],
        }


# ----------------------------------------------------------------------------- HTTP


def _api_error(res: httpx.Response) -> CliError:
    try:
        body = res.json()
        message, api_code = str(body["message"]), str(body["code"])
    except (ValueError, KeyError, TypeError):
        return CliError(f"SmartRail answered HTTP {res.status_code}.")
    usage = res.status_code in (404, 422) and api_code in ("not_found", "validation_error")
    return CliError(message, EXIT_USAGE if usage else EXIT_FAILED, api_code)


async def _call(client: httpx.AsyncClient, method: str, path: str, **kwargs: Any) -> Any:
    try:
        res = await client.request(method, f"/api{path}", **kwargs)
    except httpx.TransportError as exc:
        raise CliError(
            f"Cannot reach SmartRail at {client.base_url}. Start it with `uv run smartrail`, "
            "or point --url / SMARTRAIL_URL at it.",
            EXIT_UNREACHABLE,
        ) from exc
    if not res.is_success:
        raise _api_error(res)
    return res.json()


# ----------------------------------------------------------------------------- operations


async def list_agents(client: httpx.AsyncClient, include_archived: bool = False) -> list[dict]:
    query = "?include_archived=true" if include_archived else ""
    return await _call(client, "GET", f"/agents{query}")


def pick_agent(agents: Sequence[dict], ref: str) -> dict:
    """Resolve an agent by id, exact name, or unique name fragment (case-insensitive)."""
    wanted = ref.strip().casefold()
    tiers = (
        [a for a in agents if a["id"].casefold() == wanted],
        [a for a in agents if a["name"].casefold() == wanted],
        [a for a in agents if wanted and wanted in a["name"].casefold()],
    )
    for found in tiers:
        if len(found) == 1:
            return found[0]
        if len(found) > 1:
            options = ", ".join(f"{a['name']} ({a['id']})" for a in found)
            raise CliError(f"{ref!r} matches several agents: {options}. Use the id.", EXIT_USAGE)
    known = ", ".join(a["name"] for a in agents) or "none yet"
    raise CliError(
        f"No agent matches {ref!r}. Available: {known}. Run `smartrail agents` for ids.",
        EXIT_USAGE,
    )


def _title(label: str, agent: dict, message: str) -> str:
    first = " ".join(message.split())
    who = label if label != DEFAULT_LABEL else "Coding agent"
    return f"{who} → {agent['name']}: {first[:70]}"[:200]


async def _send(
    client: httpx.AsyncClient,
    conversation_id: str,
    content: str,
    deadline: float,
    log: Callable[[str], None],
) -> str:
    """Post the message, waiting while another run holds the app's single run slot."""
    announced = False
    while True:
        try:
            sent = await _call(
                client,
                "POST",
                f"/conversations/{conversation_id}/messages",
                json={"content": content},
            )
            return sent["run_id"]
        except CliError as exc:
            if exc.api_code != "run_active":
                raise
            if time.monotonic() >= deadline:
                raise CliError(
                    "Another SmartRail run is still in progress; gave up waiting for it.",
                    EXIT_TIMEOUT,
                ) from None
            if not announced:
                log("Another SmartRail run is in progress; waiting for it to finish...")
                announced = True
            await asyncio.sleep(BUSY_RETRY_S)


async def _collect(
    client: httpx.AsyncClient, conversation_id: str, run_id: str, deadline: float
) -> Reply:
    while True:
        run = await _call(client, "GET", f"/runs/{run_id}")
        if run["status"] != "running":
            break
        if time.monotonic() >= deadline:
            raise CliError(
                f"No reply in time. The run is still going in SmartRail (conversation "
                f"{conversation_id}); collect it later with: smartrail reply {conversation_id}",
                EXIT_TIMEOUT,
            )
        await asyncio.sleep(POLL_S)
    detail = await _call(client, "GET", f"/conversations/{conversation_id}")
    messages = [m for m in detail["messages"] if m["run_id"] == run_id and m["role"] == "agent"]
    return Reply(conversation_id, run_id, run["status"], run.get("error"), messages)


async def ask(
    client: httpx.AsyncClient,
    message: str,
    *,
    agent_ref: str | None = None,
    conversation_id: str | None = None,
    label: str = DEFAULT_LABEL,
    timeout_s: float = DEFAULT_TIMEOUT_S,
    log: Callable[[str], None] = lambda _msg: None,
) -> Reply:
    """Send ``message`` to an agent (new direct conversation) or into ``conversation_id``."""
    if not message.strip():
        raise CliError("The message is empty.", EXIT_USAGE)
    content = _FRAME.format(label=label) + message.strip()
    if len(content) > MESSAGE_MAX:
        room = MESSAGE_MAX - len(content) + len(message.strip())
        raise CliError(
            f"The message is too long: SmartRail accepts at most {MESSAGE_MAX} characters "
            f"including a short header, so about {room} for the text.",
            EXIT_USAGE,
        )
    deadline = time.monotonic() + timeout_s
    if conversation_id is None:
        if not agent_ref:
            raise CliError("Name the agent to ask (see `smartrail agents`).", EXIT_USAGE)
        agent = pick_agent(await list_agents(client), agent_ref)
        conv = await _call(
            client,
            "POST",
            "/conversations",
            json={
                "type": "direct",
                "participant_ids": [agent["id"]],
                "title": _title(label, agent, message),
            },
        )
        conversation_id = conv["id"]
        log(f"Asking {agent['name']} (conversation {conversation_id})...")
    run_id = await _send(client, conversation_id, content, deadline, log)
    return await _collect(client, conversation_id, run_id, deadline)


async def latest_reply(
    client: httpx.AsyncClient, conversation_id: str, timeout_s: float = DEFAULT_TIMEOUT_S
) -> Reply:
    """The reply of the conversation's current run, or of its most recent finished one."""
    deadline = time.monotonic() + timeout_s
    detail = await _call(client, "GET", f"/conversations/{conversation_id}")
    run_id = detail["active_run_id"] or next(
        (m["run_id"] for m in reversed(detail["messages"]) if m["run_id"]), None
    )
    if run_id is None:
        raise CliError(f"Conversation {conversation_id} has no messages yet.", EXIT_USAGE)
    return await _collect(client, conversation_id, run_id, deadline)


# ----------------------------------------------------------------------------- CLI


def _first_line(text: str, width: int = 110) -> str:
    line = next((ln.strip() for ln in text.splitlines() if ln.strip()), "")
    return line if len(line) <= width else line[: width - 1] + "…"


def _format_agents(agents: Sequence[dict]) -> str:
    if not agents:
        return "No agents yet. Create one in the SmartRail app."
    rows = []
    for a in agents:
        tags = [f"{a['provider_id']}/{a['model_id']}"]
        if a.get("working_directory"):
            tags.append(f"{a['tool_access']} files in {a['working_directory']}")
        if a.get("archived"):
            tags.append("archived")
        rows.append(f"{a['id']}  {a['name']}  ({'; '.join(tags)})\n    {_first_line(a['persona'])}")
    return "\n".join(rows)


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="smartrail",
        description="Run `smartrail` with no arguments to start the app. The commands below "
        "talk to an already running app.",
    )
    sub = parser.add_subparsers(dest="command", metavar="{agents,ask,reply}")

    def common(p: argparse.ArgumentParser) -> None:
        p.add_argument(
            "--url",
            help="app URL (default: $SMARTRAIL_URL, else http://127.0.0.1:$SMARTRAIL_PORT or 8765)",
        )
        p.add_argument("--json", action="store_true", help="machine-readable output")

    agents = sub.add_parser("agents", help="list the agents that can be asked")
    agents.add_argument("--all", action="store_true", help="include archived agents")
    common(agents)

    ask_p = sub.add_parser(
        "ask",
        help="ask an agent a question and print its reply",
        description="Ask AGENT (id or name) MESSAGE, or continue a conversation with "
        "--conversation. MESSAGE '-' reads it from stdin. The agent cannot see your session: "
        "put everything it needs in the message.",
    )
    ask_p.add_argument("args", nargs="+", metavar="[AGENT] MESSAGE")
    ask_p.add_argument("--conversation", "-c", metavar="ID", help="continue this conversation")
    ask_p.add_argument(
        "--as",
        dest="label",
        default=DEFAULT_LABEL,
        metavar="NAME",
        help="who is asking, e.g. 'Claude Code' (shown to the agent and in the title)",
    )
    ask_p.add_argument("--timeout", type=float, default=DEFAULT_TIMEOUT_S, metavar="SECONDS")
    common(ask_p)

    reply = sub.add_parser("reply", help="collect the reply of a conversation's latest run")
    reply.add_argument("conversation", metavar="CONVERSATION_ID")
    reply.add_argument("--timeout", type=float, default=DEFAULT_TIMEOUT_S, metavar="SECONDS")
    common(reply)
    return parser


def _base_url(explicit: str | None) -> str:
    if explicit:
        return explicit.rstrip("/")
    if os.environ.get("SMARTRAIL_URL"):
        return os.environ["SMARTRAIL_URL"].rstrip("/")
    return f"http://{LOOPBACK}:{load_config().port}"


def _err(text: str) -> None:
    print(f"smartrail: {text}", file=sys.stderr)


def _emit(reply: Reply, as_json: bool) -> int:
    if as_json:
        print(json.dumps(reply.as_json(), indent=2, ensure_ascii=False))
    else:
        if reply.text:
            print(reply.text)
        again = f'smartrail ask --conversation {reply.conversation_id} "..."'
        _err(f"conversation {reply.conversation_id}; follow up with: {again}")
        if not reply.ok:
            detail = reply.error or next(
                (m["error"] for m in reply.messages if m.get("error")), None
            )
            _err(f"the run {reply.status}" + (f": {detail}" if detail else "."))
    return EXIT_OK if reply.ok else EXIT_FAILED


async def main_async(
    argv: Sequence[str] | None = None, *, transport: httpx.AsyncBaseTransport | None = None
) -> int:
    parser = _parser()
    args = parser.parse_args(argv)
    if args.command is None:
        parser.print_help()
        return EXIT_USAGE
    try:
        async with httpx.AsyncClient(
            base_url=_base_url(args.url), transport=transport, timeout=30.0
        ) as client:
            if args.command == "agents":
                agents = await list_agents(client, args.all)
                print(
                    json.dumps(agents, indent=2, ensure_ascii=False)
                    if args.json
                    else _format_agents(agents)
                )
                return EXIT_OK
            if args.command == "reply":
                return _emit(await latest_reply(client, args.conversation, args.timeout), args.json)
            if args.conversation:
                if len(args.args) != 1:
                    raise CliError("With --conversation, give only the message.", EXIT_USAGE)
                agent_ref, message = None, args.args[0]
            else:
                if len(args.args) != 2:
                    raise CliError('Usage: smartrail ask AGENT "message"', EXIT_USAGE)
                agent_ref, message = args.args
            if message == "-":
                message = sys.stdin.read()
            reply = await ask(
                client,
                message,
                agent_ref=agent_ref,
                conversation_id=args.conversation,
                label=args.label,
                timeout_s=args.timeout,
                log=lambda text: _err(text),
            )
            return _emit(reply, args.json)
    except CliError as exc:
        _err(str(exc))
        return exc.code


def main(argv: Sequence[str] | None = None) -> int:
    # Coding agents read UTF-8; Windows consoles default to a legacy code page.
    for stream in (sys.stdin, sys.stdout, sys.stderr):
        if hasattr(stream, "reconfigure"):
            stream.reconfigure(encoding="utf-8", errors="replace")
    try:
        return asyncio.run(main_async(argv))
    except KeyboardInterrupt:
        return 130


if __name__ == "__main__":
    raise SystemExit(main())
