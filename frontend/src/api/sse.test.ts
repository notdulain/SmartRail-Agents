import { describe, expect, it, vi } from "vitest";
import { ApiError } from "./client";
import { SseParser, streamRun } from "./sse";
import type { RunEvent } from "./types";
import {
  errorResponse,
  liveSse,
  mockFetch,
  sseFrame,
  sseResponse,
} from "../test/mockFetch";

const noSleep = () => Promise.resolve();
const ev = (seq: number, type: string, extra: object = {}) =>
  ({ seq, run_id: "run_1", type, ...extra }) as unknown as RunEvent;
const delta = (seq: number, text: string) => ev(seq, "message.delta", { message_id: "m1", delta: text });

describe("SseParser", () => {
  it("parses frames split across arbitrary chunk boundaries", () => {
    const p = new SseParser();
    const raw = sseFrame({ seq: 1, type: "message.delta" }) + sseFrame({ seq: 2, type: "run.completed" });
    const out = [];
    for (let i = 0; i < raw.length; i += 7) out.push(...p.push(raw.slice(i, i + 7)));
    expect(out.map((f) => f.id)).toEqual(["1", "2"]);
    expect(out[0].event).toBe("message.delta");
    expect(JSON.parse(out[1].data).seq).toBe(2);
  });

  it("handles CRLF split between chunks, comments and multi-line data", () => {
    const p = new SseParser();
    expect(p.push(": ping\r")).toEqual([]);
    expect(p.push("\nid: 5\r")).toEqual([]);
    expect(p.push("\ndata: a\r\ndata: b\r\n\r")).toEqual([]);
    const out = p.push("\n");
    expect(out).toEqual([{ id: "5", data: "a\nb" }]);
  });

  it("ignores blank frames without data", () => {
    expect(new SseParser().push("\n\n: keep-alive\n\n")).toEqual([]);
  });
});

describe("streamRun", () => {
  it("delivers events in order and resolves on the terminal event", async () => {
    mockFetch({
      "GET /api/runs/run_1/events": () =>
        sseResponse([ev(1, "run.started"), delta(2, "Hel"), delta(3, "lo"), ev(4, "run.completed")]),
    });
    const seen: number[] = [];
    const res = await streamRun({ runId: "run_1", onEvent: (e) => seen.push(e.seq), sleep: noSleep });
    expect(seen).toEqual([1, 2, 3, 4]);
    expect(res.lastSeq).toBe(4);
    expect(res.terminal?.type).toBe("run.completed");
  });

  it("drops duplicate seqs (replayed events) so nothing is delivered twice", async () => {
    mockFetch({
      "GET /api/runs/run_1/events": () =>
        sseResponse([delta(1, "a"), delta(1, "a"), delta(2, "b"), delta(2, "b"), ev(3, "run.completed")]),
    });
    const seen: number[] = [];
    await streamRun({ runId: "run_1", onEvent: (e) => seen.push(e.seq), sleep: noSleep });
    expect(seen).toEqual([1, 2, 3]);
  });

  it("resumes after a dropped connection from the last seq without duplicating events", async () => {
    let attempt = 0;
    const m = mockFetch({
      "GET /api/runs/run_1/events": ({ url }) => {
        attempt++;
        if (attempt === 1) return sseResponse([ev(1, "run.started"), delta(2, "Hel")]); // ends: no terminal
        // The server honours ?after: only newer events are sent.
        expect(url.searchParams.get("after")).toBe("2");
        return sseResponse([delta(3, "lo"), ev(4, "run.completed")]);
      },
    });
    const seen: number[] = [];
    const onReconnecting = vi.fn();
    const res = await streamRun({
      runId: "run_1",
      onEvent: (e) => seen.push(e.seq),
      onReconnecting,
      sleep: noSleep,
    });
    expect(seen).toEqual([1, 2, 3, 4]);
    expect(onReconnecting).toHaveBeenCalledTimes(1);
    expect(res.terminal?.type).toBe("run.completed");
    expect(m.calls[1].url.search).toBe("?after=2");
    // Last-Event-ID is sent on the resume request too.
    expect((m.fn.mock.calls[1][1]?.headers as Record<string, string>)["Last-Event-ID"]).toBe("2");
  });

  it("ignores events the server re-sends after a reconnect", async () => {
    let attempt = 0;
    mockFetch({
      "GET /api/runs/run_1/events": () => {
        attempt++;
        return attempt === 1
          ? sseResponse([ev(1, "run.started"), delta(2, "a")])
          : sseResponse([ev(1, "run.started"), delta(2, "a"), delta(3, "b"), ev(4, "run.completed")]);
      },
    });
    const seen: number[] = [];
    await streamRun({ runId: "run_1", onEvent: (e) => seen.push(e.seq), sleep: noSleep });
    expect(seen).toEqual([1, 2, 3, 4]);
  });

  it("reconnects when the stream errors mid-flight", async () => {
    const first = liveSse();
    let attempt = 0;
    mockFetch({
      "GET /api/runs/run_1/events": () => {
        attempt++;
        return attempt === 1 ? first.response : sseResponse([delta(2, "b"), ev(3, "run.completed")]);
      },
    });
    const seen: number[] = [];
    const done = streamRun({ runId: "run_1", onEvent: (e) => seen.push(e.seq), sleep: noSleep });
    first.push(delta(1, "a"));
    await vi.waitFor(() => expect(seen).toEqual([1]));
    first.error();
    await done;
    expect(seen).toEqual([1, 2, 3]);
  });

  it("reconnects from lastSeq when events were skipped (gap)", async () => {
    let attempt = 0;
    const m = mockFetch({
      "GET /api/runs/run_1/events": () => {
        attempt++;
        return attempt === 1
          ? sseResponse([delta(1, "a"), delta(3, "c")]) // seq 2 missing
          : sseResponse([delta(2, "b"), delta(3, "c"), ev(4, "run.completed")]);
      },
    });
    const seen: number[] = [];
    await streamRun({ runId: "run_1", onEvent: (e) => seen.push(e.seq), sleep: noSleep });
    expect(seen).toEqual([1, 2, 3, 4]);
    expect(m.calls[1].url.search).toBe("?after=1");
  });

  it("retries on a network failure with backoff, then succeeds", async () => {
    let attempt = 0;
    mockFetch({
      "GET /api/runs/run_1/events": () => {
        attempt++;
        if (attempt < 3) throw new TypeError("Failed to fetch");
        return sseResponse([ev(1, "run.completed")]);
      },
    });
    const sleep = vi.fn((_ms: number) => Promise.resolve());
    const res = await streamRun({ runId: "run_1", onEvent: () => undefined, sleep, baseDelayMs: 100 });
    expect(res.terminal?.seq).toBe(1);
    expect(sleep.mock.calls.map((c) => c[0])).toEqual([100, 200]);
  });

  it("gives up after maxRetries consecutive failures", async () => {
    mockFetch({ "GET /api/runs/run_1/events": () => errorResponse(503, "runtime_error", "down") });
    await expect(
      streamRun({ runId: "run_1", onEvent: () => undefined, sleep: noSleep, maxRetries: 2 }),
    ).rejects.toMatchObject({ code: "network_error" });
  });

  it("does not retry a 404 and surfaces the ErrorResponse", async () => {
    const m = mockFetch({
      "GET /api/runs/run_1/events": () => errorResponse(404, "not_found", "no such run"),
    });
    await expect(
      streamRun({ runId: "run_1", onEvent: () => undefined, sleep: noSleep }),
    ).rejects.toSatisfy((e) => e instanceof ApiError && e.code === "not_found");
    expect(m.fn).toHaveBeenCalledTimes(1);
  });

  it("resolves quietly with no terminal event when aborted", async () => {
    const live = liveSse();
    mockFetch({ "GET /api/runs/run_1/events": () => live.response });
    const ac = new AbortController();
    const done = streamRun({ runId: "run_1", signal: ac.signal, onEvent: () => undefined, sleep: noSleep });
    live.push(delta(1, "a"));
    ac.abort();
    live.close();
    const res = await done;
    expect(res.terminal).toBeNull();
  });

  it("starts after the given seq", async () => {
    const m = mockFetch({
      "GET /api/runs/run_1/events": () => sseResponse([delta(8, "x"), ev(9, "run.completed")]),
    });
    const seen: number[] = [];
    await streamRun({ runId: "run_1", after: 7, onEvent: (e) => seen.push(e.seq), sleep: noSleep });
    expect(seen).toEqual([8, 9]);
    expect(m.calls[0].url.search).toBe("?after=7");
  });
});
