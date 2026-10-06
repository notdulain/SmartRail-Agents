import type { ConversationDetail, ErrorCode, Message, RunEvent } from "../api/types";

export type RunPhase = "idle" | "running" | "completed" | "failed" | "cancelled" | "paused";

export interface ConversationState {
  messages: Message[];
  activeRunId: string | null;
  /** Highest event seq applied for the attached run. Events at or below it are ignored. */
  appliedSeq: number;
  phase: RunPhase;
  pausedReason: string | null;
  runError: { code: ErrorCode | null; message: string | null } | null;
}

export const initialConversationState: ConversationState = {
  messages: [],
  activeRunId: null,
  appliedSeq: 0,
  phase: "idle",
  pausedReason: null,
  runError: null,
};

export type ConversationAction =
  | { type: "reset" }
  | { type: "loaded"; detail: ConversationDetail }
  | { type: "attach"; runId: string }
  | { type: "event"; event: RunEvent };

function upsert(messages: Message[], message: Message): Message[] {
  const i = messages.findIndex((m) => m.id === message.id);
  if (i === -1) return [...messages, message];
  const next = messages.slice();
  next[i] = message;
  return next;
}

export function conversationReducer(
  state: ConversationState,
  action: ConversationAction,
): ConversationState {
  switch (action.type) {
    case "reset":
      return initialConversationState;

    case "loaded": {
      const { messages } = action.detail;
      const active_run_id = action.detail.active_run_id ?? null;
      return {
        ...state,
        messages,
        activeRunId: active_run_id,
        appliedSeq: 0,
        phase: active_run_id ? "running" : state.phase === "running" ? "idle" : state.phase,
        pausedReason: active_run_id ? null : state.pausedReason,
        runError: active_run_id ? null : state.runError,
      };
    }

    case "attach":
      // The stream replays from seq 1; message.started resets a message, so replay is idempotent.
      return {
        ...state,
        activeRunId: action.runId,
        appliedSeq: 0,
        phase: "running",
        pausedReason: null,
        runError: null,
      };

    case "event": {
      const { event } = action;
      if (event.run_id !== state.activeRunId) return state;
      if (event.seq <= state.appliedSeq) return state;
      const base = { ...state, appliedSeq: event.seq };
      switch (event.type) {
        case "run.started":
          return { ...base, phase: "running" };
        case "message.started":
          // Reset content: on replay the deltas that follow rebuild it exactly once.
          return { ...base, messages: upsert(state.messages, { ...event.message, content: "" }) };
        case "message.delta": {
          const i = state.messages.findIndex((m) => m.id === event.message_id);
          if (i === -1) return base;
          const messages = state.messages.slice();
          messages[i] = { ...messages[i], content: messages[i].content + event.delta };
          return { ...base, messages };
        }
        case "message.completed":
          return { ...base, messages: upsert(state.messages, event.message) };
        case "run.completed":
          return { ...base, activeRunId: null, phase: "completed" };
        case "run.cancelled":
          return { ...base, activeRunId: null, phase: "cancelled" };
        case "run.failed":
          return {
            ...base,
            activeRunId: null,
            phase: "failed",
            runError: { code: event.run.error_code ?? null, message: event.run.error ?? null },
          };
        case "run.paused":
          return {
            ...base,
            activeRunId: null,
            phase: "paused",
            pausedReason: event.reason,
          };
        default:
          return base;
      }
    }
  }
}

/** Reason to show in the paused banner: live run state, else the last budget failure in history. */
export function pausedBanner(state: ConversationState): string | null {
  if (state.activeRunId) return null;
  if (state.phase === "paused") return state.pausedReason ?? "The run was paused.";
  if (state.phase !== "idle") return null;
  const last = [...state.messages].reverse().find((m) => m.role === "agent");
  if (last?.status === "failed" && last.error_code === "budget_exhausted") {
    return last.error ?? "The budget was exhausted.";
  }
  return null;
}
