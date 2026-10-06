import { useCallback, useEffect, useReducer, useRef, useState } from "react";
import { api, ApiError } from "../api/client";
import { streamRun } from "../api/sse";
import type { Conversation, RunEvent } from "../api/types";
import { isApiCode } from "../lib/errors";
import {
  conversationReducer,
  initialConversationState,
  type ConversationState,
} from "./conversationState";

export type Connection = "idle" | "live" | "reconnecting";

export interface UseConversation {
  conversation: Conversation | null;
  state: ConversationState;
  loading: boolean;
  loadError: ApiError | null;
  connection: Connection;
  /** The stream gave up after repeated failures; the user can retry with reload(). */
  streamLost: boolean;
  sending: boolean;
  stopping: boolean;
  /** Inline message for a failed send (also surfaced by the caller as a toast). */
  sendError: ApiError | null;
  send(content: string): Promise<boolean>;
  stop(): Promise<void>;
  reload(): Promise<void>;
}

interface Options {
  /** Called when a run reaches a terminal event (refresh sidebar order, spend, ...). */
  onRunEnded?: (event: RunEvent) => void;
  onError?: (err: unknown) => void;
}

export function useConversation(id: string | null, options: Options = {}): UseConversation {
  const [conversation, setConversation] = useState<Conversation | null>(null);
  const [state, dispatch] = useReducer(conversationReducer, initialConversationState);
  const [loading, setLoading] = useState(false);
  const [loadError, setLoadError] = useState<ApiError | null>(null);
  const [connection, setConnection] = useState<Connection>("idle");
  const [streamLost, setStreamLost] = useState(false);
  const [sending, setSending] = useState(false);
  const [stopping, setStopping] = useState(false);
  const [sendError, setSendError] = useState<ApiError | null>(null);

  const abortRef = useRef<AbortController | null>(null);
  const generation = useRef(0);
  const stateRef = useRef(state);
  stateRef.current = state;
  const optionsRef = useRef(options);
  optionsRef.current = options;
  const idRef = useRef(id);
  idRef.current = id;

  const closeStream = useCallback(() => {
    abortRef.current?.abort();
    abortRef.current = null;
    setConnection("idle");
  }, []);

  const attach = useCallback(
    (runId: string) => {
      closeStream();
      const ac = new AbortController();
      abortRef.current = ac;
      setStreamLost(false);
      dispatch({ type: "attach", runId });
      streamRun({
        runId,
        after: 0,
        signal: ac.signal,
        onEvent: (event) => dispatch({ type: "event", event }),
        onConnected: () => !ac.signal.aborted && setConnection("live"),
        onReconnecting: () => !ac.signal.aborted && setConnection("reconnecting"),
      })
        .then((res) => {
          if (ac.signal.aborted) return;
          setConnection("idle");
          if (res.terminal) optionsRef.current.onRunEnded?.(res.terminal);
        })
        .catch((err) => {
          if (ac.signal.aborted) return;
          setConnection("idle");
          setStreamLost(true);
          optionsRef.current.onError?.(err);
        });
    },
    [closeStream],
  );

  /** Fetch the transcript; if a run is active, resume streaming it. Returns false if stale. */
  const load = useCallback(
    async (convId: string, gen: number): Promise<boolean> => {
      const detail = await api.getConversation(convId);
      if (gen !== generation.current) return false;
      setConversation(detail.conversation);
      dispatch({ type: "loaded", detail });
      if (detail.active_run_id) attach(detail.active_run_id);
      else closeStream();
      return true;
    },
    [attach, closeStream],
  );

  useEffect(() => {
    const gen = ++generation.current;
    closeStream();
    setStopping(false);
    setSendError(null);
    setLoadError(null);
    setStreamLost(false);
    if (!id) {
      setConversation(null);
      dispatch({ type: "reset" });
      setLoading(false);
      return;
    }
    setConversation(null);
    dispatch({ type: "reset" });
    setLoading(true);
    load(id, gen)
      .catch((err) => {
        if (gen !== generation.current) return;
        setLoadError(err instanceof ApiError ? err : new ApiError(0, "unknown", String(err)));
      })
      .finally(() => {
        if (gen === generation.current) setLoading(false);
      });
    return () => {
      generation.current++;
      closeStream();
    };
  }, [id, load, closeStream]);

  // Leaving the running state clears the "stopping" label.
  useEffect(() => {
    if (!state.activeRunId) setStopping(false);
  }, [state.activeRunId]);

  const reload = useCallback(async () => {
    const convId = idRef.current;
    if (!convId) return;
    const gen = generation.current;
    try {
      setLoadError(null);
      await load(convId, gen);
    } catch (err) {
      if (gen === generation.current) {
        setLoadError(err instanceof ApiError ? err : new ApiError(0, "unknown", String(err)));
      }
    }
  }, [load]);

  const send = useCallback(
    async (content: string): Promise<boolean> => {
      const convId = idRef.current;
      if (!convId || stateRef.current.activeRunId) return false;
      const gen = generation.current;
      setSending(true);
      setSendError(null);
      try {
        const res = await api.sendMessage(convId, content);
        if (gen !== generation.current) return true;
        // Lock the composer right away; then load the transcript (now holding the user's message)
        // and stream the run from its start.
        dispatch({ type: "attach", runId: res.run_id });
        await load(convId, gen);
        return true;
      } catch (err) {
        if (gen !== generation.current) return false;
        const apiErr = err instanceof ApiError ? err : new ApiError(0, "unknown", String(err));
        setSendError(apiErr);
        optionsRef.current.onError?.(apiErr);
        if (isApiCode(apiErr, "run_active")) {
          // Someone (another tab) started a run: pick it up instead of failing.
          void reload();
        }
        return false;
      } finally {
        setSending(false);
      }
    },
    [load, reload],
  );

  const stop = useCallback(async () => {
    const runId = stateRef.current.activeRunId;
    if (!runId) return;
    setStopping(true);
    try {
      await api.stopRun(runId);
      // The stream delivers the cancellation. If it does not arrive, resync from the server.
      setTimeout(() => {
        if (stateRef.current.activeRunId === runId && idRef.current) void reload();
      }, 4000);
    } catch (err) {
      setStopping(false);
      optionsRef.current.onError?.(err);
    }
  }, [reload]);

  return {
    conversation,
    state,
    loading,
    loadError,
    connection,
    streamLost,
    sending,
    stopping,
    sendError,
    send,
    stop,
    reload,
  };
}
