import { ApiError, toApiError, type FetchLike } from "./client";
import { TERMINAL_EVENT_TYPES, type RunEvent } from "./types";

export interface SseFrame {
  id?: string;
  event?: string;
  data: string;
}

/** Incremental text/event-stream parser. Feed it decoded text; it returns completed frames. */
export class SseParser {
  private buffer = "";
  private frame: SseFrame = { data: "" };
  private hasData = false;

  push(chunk: string): SseFrame[] {
    const out: SseFrame[] = [];
    let text = this.buffer + chunk;
    // A trailing \r may be the first half of \r\n: hold it back until the next chunk.
    const heldCr = text.endsWith("\r") ? "\r" : "";
    if (heldCr) text = text.slice(0, -1);
    const lines = text.split(/\r\n|\n|\r/);
    this.buffer = (lines.pop() ?? "") + heldCr;
    for (const line of lines) {
      if (line === "") {
        if (this.hasData) out.push({ ...this.frame });
        this.frame = { data: "" };
        this.hasData = false;
        continue;
      }
      if (line.startsWith(":")) continue; // comment / keep-alive
      const i = line.indexOf(":");
      const field = i === -1 ? line : line.slice(0, i);
      let value = i === -1 ? "" : line.slice(i + 1);
      if (value.startsWith(" ")) value = value.slice(1);
      if (field === "data") {
        this.frame.data = this.hasData ? `${this.frame.data}\n${value}` : value;
        this.hasData = true;
      } else if (field === "id") this.frame.id = value;
      else if (field === "event") this.frame.event = value;
    }
    return out;
  }
}

export interface StreamRunOptions {
  runId: string;
  /** Resume after this seq (0 = from the start). */
  after?: number;
  onEvent: (event: RunEvent) => void;
  /** Called when the connection dropped and a retry is scheduled. */
  onReconnecting?: (attempt: number) => void;
  /** Called once a (re)connection delivers a response. */
  onConnected?: () => void;
  signal?: AbortSignal;
  fetchImpl?: FetchLike;
  sleep?: (ms: number, signal?: AbortSignal) => Promise<void>;
  /** Consecutive attempts without progress before giving up. */
  maxRetries?: number;
  baseDelayMs?: number;
}

export interface StreamRunResult {
  lastSeq: number;
  /** The terminal event, or null when aborted. */
  terminal: RunEvent | null;
}

const defaultSleep = (ms: number, signal?: AbortSignal) =>
  new Promise<void>((resolve) => {
    const t = setTimeout(resolve, ms);
    signal?.addEventListener(
      "abort",
      () => {
        clearTimeout(t);
        resolve();
      },
      { once: true },
    );
  });

/**
 * Streams a run's events. Tracks the last seen `seq`, drops anything at or below it,
 * and after a dropped connection resumes with `?after=<lastSeq>` (and Last-Event-ID), so a
 * reconnect never delivers an event twice. Resolves after the terminal event or on abort.
 */
export async function streamRun(opts: StreamRunOptions): Promise<StreamRunResult> {
  const fetchImpl = opts.fetchImpl ?? ((...a: Parameters<FetchLike>) => fetch(...a));
  const sleep = opts.sleep ?? defaultSleep;
  const maxRetries = opts.maxRetries ?? 8;
  const baseDelay = opts.baseDelayMs ?? 400;
  const { signal } = opts;
  let lastSeq = opts.after ?? 0;
  let failures = 0;

  while (!signal?.aborted) {
    let progressed = false;
    try {
      const headers: Record<string, string> = { Accept: "text/event-stream" };
      if (lastSeq > 0) headers["Last-Event-ID"] = String(lastSeq);
      const res = await fetchImpl(
        `/api/runs/${encodeURIComponent(opts.runId)}/events?after=${lastSeq}`,
        { headers, signal },
      );
      if (!res.ok) {
        const err = await toApiError(res);
        const retryable = res.status >= 500 || res.status === 408 || res.status === 429;
        if (!retryable) throw err;
        throw new RetryableError(err.message);
      }
      if (!res.body) throw new RetryableError("No response body");
      opts.onConnected?.();

      const reader = res.body.getReader();
      const decoder = new TextDecoder();
      const parser = new SseParser();
      let gap = false;
      try {
        read: for (;;) {
          const { done, value } = await reader.read();
          if (done) break;
          for (const frame of parser.push(decoder.decode(value, { stream: true }))) {
            const event = decodeEvent(frame);
            if (!event) continue;
            if (event.seq <= lastSeq) continue; // duplicate
            if (event.seq > lastSeq + 1) {
              gap = true; // missed events: reconnect from lastSeq instead of skipping them
              break read;
            }
            lastSeq = event.seq;
            progressed = true;
            opts.onEvent(event);
            if (TERMINAL_EVENT_TYPES.has(event.type)) {
              void reader.cancel().catch(() => undefined);
              return { lastSeq, terminal: event };
            }
          }
        }
      } finally {
        if (gap) void reader.cancel().catch(() => undefined);
      }
      // Stream ended without a terminal event: fall through to reconnect.
    } catch (e) {
      if (signal?.aborted) break;
      if (e instanceof ApiError) throw e;
      if (!(e instanceof RetryableError) && !(e instanceof TypeError) && !isStreamError(e)) throw e;
    }

    if (signal?.aborted) break;
    failures = progressed ? 1 : failures + 1;
    if (failures > maxRetries) {
      throw new ApiError(0, "network_error", "Lost connection to the run. Reload to try again.");
    }
    opts.onReconnecting?.(failures);
    await sleep(Math.min(baseDelay * 2 ** (failures - 1), 5000), signal);
  }
  return { lastSeq, terminal: null };
}

class RetryableError extends Error {}

function isStreamError(e: unknown): boolean {
  return e instanceof Error && e.name !== "AbortError" && !(e instanceof SyntaxError);
}

function decodeEvent(frame: SseFrame): RunEvent | null {
  if (!frame.data) return null;
  try {
    const parsed = JSON.parse(frame.data) as RunEvent;
    if (typeof parsed?.seq !== "number" || typeof parsed.type !== "string") return null;
    return parsed;
  } catch {
    return null;
  }
}
