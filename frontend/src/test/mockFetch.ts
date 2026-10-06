import { vi } from "vitest";

export interface MockRequest {
  url: URL;
  method: string;
  body: unknown;
  headers: Headers;
}
export type Handler = (req: MockRequest) => Response | Promise<Response> | unknown;

export function json(body: unknown, status = 200): Response {
  return new Response(JSON.stringify(body), {
    status,
    headers: { "Content-Type": "application/json" },
  });
}

export function errorResponse(
  status: number,
  code: string,
  message: string,
  agent_id: string | null = null,
): Response {
  return json({ code, message, agent_id }, status);
}

const encoder = new TextEncoder();

/** Encode run events as SSE frames (the shape sse_starlette emits). */
export type TestEvent = { seq: number; type: string; [k: string]: unknown };

export function sseFrame(event: TestEvent): string {
  return `id: ${event.seq}\r\nevent: ${event.type}\r\ndata: ${JSON.stringify(event)}\r\n\r\n`;
}

/** A finite SSE response that ends after the given events. */
export function sseResponse(events: TestEvent[]): Response {
  return new Response(
    new ReadableStream({
      start(controller) {
        for (const e of events) controller.enqueue(encoder.encode(sseFrame(e)));
        controller.close();
      },
    }),
    { status: 200, headers: { "Content-Type": "text/event-stream" } },
  );
}

/** An SSE response the test drives by hand. */
export function liveSse() {
  let controller!: ReadableStreamDefaultController<Uint8Array>;
  const response = new Response(
    new ReadableStream<Uint8Array>({
      start(c) {
        controller = c;
      },
    }),
    { status: 200, headers: { "Content-Type": "text/event-stream" } },
  );
  return {
    response,
    push(event: TestEvent) {
      controller.enqueue(encoder.encode(sseFrame(event)));
    },
    pushRaw(text: string) {
      controller.enqueue(encoder.encode(text));
    },
    close() {
      controller.close();
    },
    error(e: unknown = new TypeError("network error")) {
      controller.error(e);
    },
  };
}

/**
 * Route-based fetch mock. Keys look like "GET /api/agents" (query string ignored).
 * A handler may return a Response or any JSON-serialisable value.
 */
export function mockFetch(routes: Record<string, Handler>) {
  const calls: Array<{ method: string; path: string; body: unknown; url: URL }> = [];
  const fn = vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
    const url = new URL(typeof input === "string" ? input : input.toString(), "http://localhost");
    const method = (init?.method ?? "GET").toUpperCase();
    const body = init?.body ? JSON.parse(String(init.body)) : undefined;
    calls.push({ method, path: url.pathname, body, url });
    const handler = routes[`${method} ${url.pathname}`];
    if (!handler) return errorResponse(404, "not_found", `no mock for ${method} ${url.pathname}`);
    const result = await handler({
      url,
      method,
      body,
      headers: new Headers(init?.headers as HeadersInit | undefined),
    });
    return result instanceof Response ? result : json(result);
  });
  vi.stubGlobal("fetch", fn);
  return { fn, calls, count: (key: string) => calls.filter((c) => `${c.method} ${c.path}` === key).length };
}
