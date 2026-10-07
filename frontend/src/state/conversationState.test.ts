import { describe, expect, it } from "vitest";
import type { ConversationDetail, RunEvent } from "../api/types";
import { makeConversation, makeMessage, makeRun, makeUserMessage } from "../test/fixtures";
import {
  conversationReducer,
  initialConversationState,
  pausedBanner,
  type ConversationAction,
  type ConversationState,
} from "./conversationState";

const run = (e: Record<string, unknown>) => ({ run_id: "run_1", ...e }) as unknown as RunEvent;
const started = (seq: number) =>
  run({ seq, type: "message.started", message: makeMessage({ id: "m1", status: "streaming", content: "" }) });
const delta = (seq: number, d: string) => run({ seq, type: "message.delta", message_id: "m1", delta: d });

function apply(state: ConversationState, ...actions: ConversationAction[]) {
  return actions.reduce(conversationReducer, state);
}
const ev = (event: RunEvent): ConversationAction => ({ type: "event", event });
const detail = (over: Partial<ConversationDetail>): ConversationDetail => ({
  conversation: makeConversation(),
  messages: [],
  active_run_id: null,
  ...over,
});

describe("conversationReducer", () => {
  it("renders deltas live and finalises on completion", () => {
    let s = apply(initialConversationState, { type: "attach", runId: "run_1" });
    s = apply(s, ev(started(1)), ev(delta(2, "Hel")), ev(delta(3, "lo")));
    expect(s.messages[0].content).toBe("Hello");
    expect(s.messages[0].status).toBe("streaming");
    s = apply(
      s,
      ev(run({ seq: 4, type: "message.completed", message: makeMessage({ id: "m1", content: "Hello" }) })),
      ev(run({ seq: 5, type: "run.completed", run: makeRun({ status: "completed" }) })),
    );
    expect(s.messages[0].status).toBe("complete");
    expect(s.activeRunId).toBeNull();
    expect(s.phase).toBe("completed");
  });

  it("ignores events with a seq it has already applied", () => {
    let s = apply(initialConversationState, { type: "attach", runId: "run_1" });
    s = apply(s, ev(started(1)), ev(delta(2, "a")), ev(delta(2, "a")), ev(delta(1, "zzz")), ev(delta(3, "b")));
    expect(s.messages[0].content).toBe("ab");
    expect(s.appliedSeq).toBe(3);
  });

  it("ignores events for a different run", () => {
    const s = apply(
      initialConversationState,
      { type: "attach", runId: "run_1" },
      ev({ ...started(1), run_id: "other" } as RunEvent),
    );
    expect(s.messages).toHaveLength(0);
  });

  it("resumes a half-streamed message from a fresh load without duplicating text", () => {
    // Page reload mid-run: the transcript already holds the partial text "Hel".
    const partial = makeMessage({ id: "m1", status: "streaming", content: "Hel" });
    let s = apply(initialConversationState, {
      type: "loaded",
      detail: detail({ messages: [makeUserMessage(), partial], active_run_id: "run_1" }),
    });
    expect(s.phase).toBe("running");
    // The stream replays from seq 1.
    s = apply(s, { type: "attach", runId: "run_1" }, ev(started(1)), ev(delta(2, "Hel")), ev(delta(3, "lo")));
    expect(s.messages.map((m) => m.content)).toEqual(["Hi there", "Hello"]);
  });

  it("does not corrupt finished messages when an earlier part of the run is replayed", () => {
    const done = makeMessage({ id: "m1", content: "Hello" });
    let s = apply(initialConversationState, {
      type: "loaded",
      detail: detail({ messages: [done], active_run_id: "run_1" }),
    });
    s = apply(
      s,
      { type: "attach", runId: "run_1" },
      ev(started(1)),
      ev(delta(2, "Hello")),
      ev(run({ seq: 3, type: "message.completed", message: done })),
    );
    expect(s.messages).toHaveLength(1);
    expect(s.messages[0].content).toBe("Hello");
  });

  it("records pause reason and failure details", () => {
    let s = apply(initialConversationState, { type: "attach", runId: "run_1" });
    s = apply(
      s,
      ev(run({ seq: 1, type: "run.paused", run: makeRun({ status: "paused" }), reason: "Budget used up" })),
    );
    expect(s.phase).toBe("paused");
    expect(pausedBanner(s)).toBe("Budget used up");
    expect(s.activeRunId).toBeNull();

    let f = apply(initialConversationState, { type: "attach", runId: "run_1" });
    f = apply(
      f,
      ev(
        run({
          seq: 1,
          type: "run.failed",
          run: makeRun({ status: "failed", error: "boom", error_code: "runtime_error" }),
        }),
      ),
    );
    expect(f.phase).toBe("failed");
    expect(f.runError).toEqual({ code: "runtime_error", message: "boom" });
  });

  it("derives a paused banner from a budget failure in the history", () => {
    const s = apply(initialConversationState, {
      type: "loaded",
      detail: detail({
        messages: [
          makeMessage({ status: "failed", error: "Monthly budget used up", error_code: "budget_exhausted" }),
        ],
      }),
    });
    expect(pausedBanner(s)).toBe("Monthly budget used up");
  });

  it("shows no banner while a run is active", () => {
    const s = apply(initialConversationState, {
      type: "loaded",
      detail: detail({
        messages: [makeMessage({ status: "failed", error_code: "budget_exhausted", error: "x" })],
        active_run_id: "run_2",
      }),
    });
    expect(pausedBanner(s)).toBeNull();
  });
});

describe("message.tool events", () => {
  const tool = (seq: number, call: Record<string, unknown>, messageId = "m1") =>
    run({
      seq,
      type: "message.tool",
      message_id: messageId,
      tool_call: { error: null, ...call },
    });

  it("appends new tool calls in order and updates existing ones by id", () => {
    let s = apply(initialConversationState, { type: "attach", runId: "run_1" }, ev(started(1)));
    s = apply(
      s,
      ev(tool(2, { id: "t1", tool: "read", title: "README.md", status: "running" })),
      ev(tool(3, { id: "t2", tool: "grep", title: "TODO", status: "running" })),
      ev(tool(4, { id: "t1", tool: "read", title: "README.md", status: "completed" })),
      ev(tool(5, { id: "t2", tool: "grep", title: "TODO", status: "error", error: "bad pattern" })),
    );
    expect(s.messages[0].tool_calls).toEqual([
      { id: "t1", tool: "read", title: "README.md", status: "completed", error: null },
      { id: "t2", tool: "grep", title: "TODO", status: "error", error: "bad pattern" },
    ]);
  });

  it("keeps streamed text when tool calls arrive", () => {
    let s = apply(initialConversationState, { type: "attach", runId: "run_1" });
    s = apply(
      s,
      ev(started(1)),
      ev(delta(2, "Let me look")),
      ev(tool(3, { id: "t1", tool: "list", title: "src", status: "running" })),
      ev(delta(4, "...")),
    );
    expect(s.messages[0].content).toBe("Let me look...");
    expect(s.messages[0].tool_calls).toHaveLength(1);
  });

  it("ignores tool events for unknown messages and replays idempotently", () => {
    let s = apply(initialConversationState, { type: "attach", runId: "run_1" }, ev(started(1)));
    s = apply(s, ev(tool(2, { id: "t1", tool: "read", title: "a", status: "running" }, "nope")));
    expect(s.messages[0].tool_calls).toEqual([]);
    expect(s.appliedSeq).toBe(2);

    // Reattaching replays from seq 1: the call is rebuilt once, not duplicated.
    const call = tool(3, { id: "t1", tool: "read", title: "a", status: "completed" });
    s = apply(s, ev(call));
    s = apply(s, { type: "attach", runId: "run_1" }, ev(started(1)), ev(tool(2, { id: "x", tool: "read", title: "b", status: "running" }, "nope")), ev(call));
    expect(s.messages[0].tool_calls).toHaveLength(1);
  });

  it("tolerates a message without a tool_calls array", () => {
    let s = apply(initialConversationState, { type: "attach", runId: "run_1" });
    s = apply(
      s,
      ev(run({ seq: 1, type: "message.started", message: { ...makeMessage({ id: "m1" }), tool_calls: undefined } })),
      ev(tool(2, { id: "t1", tool: "read", title: "a", status: "running" })),
    );
    expect(s.messages[0].tool_calls).toHaveLength(1);
  });
});
