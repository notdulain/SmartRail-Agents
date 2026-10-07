import { screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it, vi } from "vitest";
import {
  makeAgent,
  makeConversation,
  makeMessage,
  makeProviders,
  makeRun,
  makeSettings,
  makeUserMessage,
} from "../test/fixtures";
import { errorResponse, liveSse, mockFetch, sseResponse } from "../test/mockFetch";
import { renderWithProviders } from "../test/render";
import { fakeFileSearch } from "../test/fakeFiles";
import { ChatView } from "./ChatView";

type Ev = { seq: number; type: string; [k: string]: unknown };
const base = { run_id: "run_1" };
const started = (seq: number): Ev => ({
  ...base,
  seq,
  type: "message.started",
  message: makeMessage({ id: "m1", status: "streaming", content: "" }),
});
const delta = (seq: number, d: string): Ev => ({
  ...base,
  seq,
  type: "message.delta",
  message_id: "m1",
  delta: d,
});
const completed = (seq: number, content: string): Ev => ({
  ...base,
  seq,
  type: "message.completed",
  message: makeMessage({ id: "m1", content }),
});
const runDone = (seq: number, type: string, extra: object = {}): Ev => ({
  ...base,
  seq,
  type,
  run: makeRun({ status: "completed" }),
  ...extra,
});

function detail(over: object = {}) {
  return {
    conversation: makeConversation(),
    messages: [] as unknown[],
    active_run_id: null as string | null,
    ...over,
  };
}

function setup(routes: Record<string, (r: never) => unknown> = {}, props: object = {}) {
  const handlers = {
    "GET /api/agents": () => [makeAgent()],
    "GET /api/conversations": () => [makeConversation()],
    "GET /api/settings": () => makeSettings({ coordinator_agent_id: "agt_1" }),
    "GET /api/providers": () => makeProviders(),
    ...routes,
  };
  const api = mockFetch(handlers as never);
  const callbacks = {
    onOpenSidebar: vi.fn(),
    onOpenSettings: vi.fn(),
    onEditAgent: vi.fn(),
    onNewDiscussion: vi.fn(),
    onNewAgent: vi.fn(),
    onRunEnded: vi.fn(),
    ...props,
  };
  renderWithProviders(<ChatView conversationId="cnv_1" {...callbacks} />);
  return { api, callbacks };
}

const composer = () => screen.getByLabelText("Message");

describe("ChatView composer and Stop", () => {
  it("sends on Enter, keeps Shift+Enter as a newline, then streams the reply live", async () => {
    const live = liveSse();
    let loaded = 0;
    const { api } = setup({
      "GET /api/conversations/cnv_1": () => {
        loaded++;
        return loaded === 1
          ? detail()
          : detail({ messages: [makeUserMessage({ content: "Where is my train?" })], active_run_id: "run_1" });
      },
      "POST /api/conversations/cnv_1/messages": () => ({ run_id: "run_1", user_message_id: "msg_user" }),
      "GET /api/runs/run_1/events": () => live.response,
    });
    await screen.findByText(/No messages yet/);
    await userEvent.type(composer(), "Where is my{Shift>}{Enter}{/Shift}train?");
    expect(composer()).toHaveValue("Where is my\ntrain?");
    expect(api.count("POST /api/conversations/cnv_1/messages")).toBe(0);
    await userEvent.clear(composer());
    await userEvent.type(composer(), "Where is my train?{Enter}");
    await waitFor(() =>
      expect(api.calls.find((c) => c.method === "POST")?.body).toEqual({ content: "Where is my train?" }),
    );
    // While the run is active the composer is locked and Stop is offered.
    expect(await screen.findByRole("button", { name: "Stop" })).toBeInTheDocument();
    expect(composer()).toBeDisabled();
    expect(screen.queryByRole("button", { name: "Send" })).not.toBeInTheDocument();

    live.push(started(1));
    live.push(delta(2, "Platform "));
    expect(await screen.findByText("Platform")).toBeInTheDocument();
    live.push(delta(3, "four"));
    expect(await screen.findByText("Platform four")).toBeInTheDocument();
    live.push(completed(4, "Platform four"));
    live.push(runDone(5, "run.completed"));
    await waitFor(() => expect(composer()).toBeEnabled());
    expect(screen.queryByRole("button", { name: "Stop" })).not.toBeInTheDocument();
    expect(screen.getByText("Platform four")).toBeInTheDocument();
  });

  it("attaches files from participants with a working directory and sends them", async () => {
    let loaded = 0;
    const { api } = setup({
      "GET /api/agents": () => [
        makeAgent({ id: "agt_1", name: "Driver", working_directory: "C:\\work", tool_access: "read_only" }),
        makeAgent({ id: "agt_2", name: "Guard" }),
      ],
      "GET /api/conversations/cnv_1": () => {
        loaded++;
        return loaded === 1
          ? detail({
              conversation: makeConversation({ type: "group", topic: "T", participant_ids: ["agt_1", "agt_2"] }),
            })
          : detail({
              conversation: makeConversation({ type: "group", topic: "T", participant_ids: ["agt_1", "agt_2"] }),
              messages: [
                makeUserMessage({ content: "Read it", attachments: [{ agent_id: "agt_1", path: "README.md" }] }),
              ],
            });
      },
      "GET /api/agents/agt_1/files": fakeFileSearch(),
      "POST /api/conversations/cnv_1/messages": () => ({ run_id: "run_1", user_message_id: "msg_user" }),
      "GET /api/runs/run_1/events": () => sseResponse([runDone(1, "run.completed")]),
    });
    await screen.findByText(/Start with the topic/);
    await userEvent.type(composer(), "@readme");
    const option = await screen.findByRole("option", { name: /README\.md/ });
    // Only the participant with a folder is searched; its group is labelled.
    expect(screen.getByRole("group", { name: "Driver" })).toContainElement(option);
    expect(screen.queryByRole("group", { name: "Guard" })).not.toBeInTheDocument();
    await userEvent.keyboard("{Enter}");
    await userEvent.type(composer(), "Read it{Enter}");
    await waitFor(() =>
      expect(api.calls.find((c) => c.method === "POST")?.body).toEqual({
        content: "Read it",
        attachments: [{ agent_id: "agt_1", path: "README.md" }],
      }),
    );
    expect(api.calls.some((c) => c.path === "/api/agents/agt_2/files")).toBe(false);
    const sent = await screen.findByRole("list", { name: "Attached files" });
    expect(sent).toHaveTextContent("Driver");
    expect(sent).toHaveTextContent("README.md");
    expect(screen.queryByRole("list", { name: "Attachments" })).not.toBeInTheDocument();
  });

  it("does not send empty or whitespace-only messages", async () => {
    const { api } = setup({ "GET /api/conversations/cnv_1": () => detail() });
    await screen.findByText(/No messages yet/);
    await userEvent.type(composer(), "   {Enter}");
    expect(api.count("POST /api/conversations/cnv_1/messages")).toBe(0);
    expect(screen.getByRole("button", { name: "Send" })).toBeDisabled();
  });

  it("shows Stop for an active run, posts /stop, and re-enables after the cancellation event", async () => {
    const live = liveSse();
    const { api } = setup({
      "GET /api/conversations/cnv_1": () =>
        detail({
          messages: [makeUserMessage(), makeMessage({ id: "m1", status: "streaming", content: "Par" })],
          active_run_id: "run_1",
        }),
      "GET /api/runs/run_1/events": () => live.response,
      "POST /api/runs/run_1/stop": () => makeRun({ status: "running" }),
    });
    const stop = await screen.findByRole("button", { name: "Stop" });
    expect(composer()).toBeDisabled();
    live.push({ ...started(1) });
    live.push(delta(2, "Par"));
    await userEvent.click(stop);
    await waitFor(() => expect(api.count("POST /api/runs/run_1/stop")).toBe(1));
    expect(await screen.findByRole("button", { name: "Stopping…" })).toBeDisabled();

    live.push({
      ...base,
      seq: 3,
      type: "message.completed",
      message: makeMessage({ id: "m1", content: "Par", status: "cancelled" }),
    });
    live.push(runDone(4, "run.cancelled"));
    await waitFor(() => expect(composer()).toBeEnabled());
    expect(screen.getByText("Stopped")).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "Stop" })).not.toBeInTheDocument();
  });

  it("handles 409 run_active gracefully by attaching to the running reply", async () => {
    const live = liveSse();
    let loaded = 0;
    const { api } = setup({
      "GET /api/conversations/cnv_1": () => {
        loaded++;
        return loaded === 1
          ? detail()
          : detail({
              messages: [makeMessage({ id: "m1", status: "streaming", content: "Alre" })],
              active_run_id: "run_1",
            });
      },
      "POST /api/conversations/cnv_1/messages": () =>
        errorResponse(409, "run_active", "A run is already active"),
      "GET /api/runs/run_1/events": () => live.response,
    });
    await screen.findByText(/No messages yet/);
    await userEvent.type(composer(), "hello{Enter}");
    expect(await screen.findByRole("button", { name: "Stop" })).toBeInTheDocument();
    expect(screen.getByText(/already being generated/)).toBeInTheDocument();
    // The draft is kept and no scary error toast is shown.
    expect(screen.queryAllByRole("alert")).toHaveLength(0);
    expect(api.count("POST /api/conversations/cnv_1/messages")).toBe(1);
  });

  it("shows other send errors as a toast and keeps the draft", async () => {
    setup({
      "GET /api/conversations/cnv_1": () => detail(),
      "POST /api/conversations/cnv_1/messages": () =>
        errorResponse(429, "rate_limited", "Too many requests"),
    });
    await screen.findByText(/No messages yet/);
    await userEvent.type(composer(), "hello{Enter}");
    const alert = await screen.findByRole("alert");
    expect(alert).toHaveTextContent("Rate limited");
    expect(alert).toHaveTextContent("Too many requests");
    expect(composer()).toHaveValue("hello");
  });

  it("shows a paused banner with the reason when a run pauses", async () => {
    const live = liveSse();
    const { callbacks } = setup({
      "GET /api/conversations/cnv_1": () => detail({ messages: [makeUserMessage()], active_run_id: "run_1" }),
      "GET /api/runs/run_1/events": () => live.response,
    });
    await screen.findByRole("button", { name: "Stop" });
    live.push({ ...runDone(1, "run.paused"), reason: "OpenRouter budget used up" });
    const banner = await screen.findByText(/OpenRouter budget used up/);
    expect(banner.closest("[role=alert]")).toHaveTextContent("Run paused.");
    await waitFor(() => expect(composer()).toBeEnabled());
    await userEvent.click(within(banner.closest("[role=alert]") as HTMLElement).getByRole("button", { name: "Open Settings" }));
    expect(callbacks.onOpenSettings).toHaveBeenCalled();
    expect(callbacks.onRunEnded).toHaveBeenCalled();
  });
});

describe("ChatView loading and resuming", () => {
  it("resumes the active run on load from the start of the stream without duplicating text", async () => {
    const partial = makeMessage({ id: "m1", status: "streaming", content: "Hel" });
    const m = setup({
      "GET /api/conversations/cnv_1": () =>
        detail({ messages: [makeUserMessage(), partial], active_run_id: "run_1" }),
      "GET /api/runs/run_1/events": () =>
        sseResponse([
          { ...base, seq: 1, type: "run.started", run: makeRun() },
          started(2),
          delta(3, "Hel"),
          delta(4, "lo"),
          completed(5, "Hello"),
          runDone(6, "run.completed"),
        ]),
    });
    await waitFor(() => expect(composer()).toBeEnabled());
    expect(screen.getAllByText("Hello")).toHaveLength(1);
    expect(screen.queryByText(/HelHello|HelHel/)).not.toBeInTheDocument();
    expect(m.api.count("GET /api/runs/run_1/events")).toBe(1);
  });

  it("reconnects a dropped stream with ?after=<last seq> and never duplicates text", async () => {
    const first = liveSse();
    let attempt = 0;
    const { api } = setup({
      "GET /api/conversations/cnv_1": () =>
        detail({ messages: [makeUserMessage()], active_run_id: "run_1" }),
      "GET /api/runs/run_1/events": () => {
        attempt++;
        return attempt === 1
          ? first.response
          : sseResponse([delta(3, "lo"), completed(4, "Hello"), runDone(5, "run.completed")]);
      },
    });
    await screen.findByRole("button", { name: "Stop" });
    first.push(started(1));
    first.push(delta(2, "Hel"));
    await screen.findByText("Hel");
    first.error();
    await waitFor(() => expect(composer()).toBeEnabled(), { timeout: 4000 });
    expect(screen.getAllByText("Hello")).toHaveLength(1);
    const resume = api.calls.filter((c) => c.path === "/api/runs/run_1/events")[1];
    expect(resume.url.searchParams.get("after")).toBe("2");
  });

  it("shows tool activity live from message.tool events", async () => {
    const live = liveSse();
    setup({
      "GET /api/conversations/cnv_1": () =>
        detail({ messages: [makeUserMessage()], active_run_id: "run_1" }),
      "GET /api/runs/run_1/events": () => live.response,
    });
    await screen.findByRole("button", { name: "Stop" });
    const tool = (seq: number, status: string): Ev => ({
      ...base,
      seq,
      type: "message.tool",
      message_id: "m1",
      tool_call: { id: "t1", tool: "read", title: "README.md", status, error: null },
    });
    live.push(started(1));
    live.push(tool(2, "running"));
    expect(await screen.findByRole("button", { name: /Reading\s+README\.md/ })).toBeInTheDocument();
    live.push(tool(3, "completed"));
    live.push(delta(4, "Done reading"));
    expect(await screen.findByRole("button", { name: /Used 1 tool/ })).toBeInTheDocument();
    expect(screen.getByText("Done reading")).toBeInTheDocument();
  });

  it("shows a loading state and then the transcript", async () => {
    setup({
      "GET /api/conversations/cnv_1": () =>
        detail({ messages: [makeUserMessage(), makeMessage({ content: "Welcome aboard" })] }),
    });
    expect(screen.getByText("Loading conversation…")).toBeInTheDocument();
    expect(await screen.findByText("Welcome aboard")).toBeInTheDocument();
    expect(screen.getByRole("heading", { name: "Chat with Passenger" })).toBeInTheDocument();
  });

  it("shows an error with retry when the conversation cannot be loaded", async () => {
    let n = 0;
    setup({
      "GET /api/conversations/cnv_1": () => {
        n++;
        return n === 1 ? errorResponse(500, "runtime_error", "database locked") : detail();
      },
    });
    expect(await screen.findByRole("alert")).toHaveTextContent("database locked");
    await userEvent.click(screen.getByRole("button", { name: "Try again" }));
    expect(await screen.findByText(/No messages yet/)).toBeInTheDocument();
  });

  it("explains a missing group coordinator inside the group chat", async () => {
    setup({
      "GET /api/conversations/cnv_1": () =>
        detail({ conversation: makeConversation({ type: "group", topic: "Delays", participant_ids: ["agt_1", "agt_2"] }) }),
      "GET /api/settings": () => makeSettings({ coordinator_agent_id: null }),
    });
    expect(await screen.findByText(/No coordinator is set/)).toBeInTheDocument();
  });

  it("starts a group discussion from the topic only when asked", async () => {
    const { api } = setup({
      "GET /api/conversations/cnv_1": () =>
        detail({ conversation: makeConversation({ type: "group", topic: "Delays", participant_ids: ["agt_1", "agt_2"] }) }),
      "POST /api/conversations/cnv_1/messages": () => errorResponse(409, "run_active", "busy"),
    });
    await screen.findByText("Delays");
    expect(api.count("POST /api/conversations/cnv_1/messages")).toBe(0);
    await userEvent.click(screen.getByRole("button", { name: "Start discussion" }));
    await waitFor(() =>
      expect(api.calls.find((c) => c.method === "POST")?.body).toEqual({ content: "Delays" }),
    );
  });
});
