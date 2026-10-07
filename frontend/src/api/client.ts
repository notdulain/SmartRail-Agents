import type {
  Agent,
  AgentCreate,
  AgentUpdate,
  Conversation,
  ConversationCreate,
  ConversationDetail,
  DirectoryListing,
  ErrorCode,
  FileRef,
  FileSearchResponse,
  ProvidersResponse,
  Run,
  SendMessageResponse,
  Settings,
  SettingsUpdate,
} from "./types";

/** Error carrying the backend's ErrorResponse fields (or a synthetic one for network failures). */
export class ApiError extends Error {
  readonly status: number;
  readonly code: ErrorCode | "network_error" | "unknown";
  readonly agentId: string | null;

  constructor(
    status: number,
    code: ApiError["code"],
    message: string,
    agentId: string | null = null,
  ) {
    super(message);
    this.name = "ApiError";
    this.status = status;
    this.code = code;
    this.agentId = agentId;
  }
}

const KNOWN_CODES = new Set<string>([
  "not_found",
  "validation_error",
  "provider_unavailable",
  "model_unavailable",
  "rate_limited",
  "budget_exhausted",
  "run_active",
  "runtime_error",
]);

export async function toApiError(res: Response): Promise<ApiError> {
  let body: unknown = null;
  try {
    body = await res.json();
  } catch {
    // not JSON
  }
  if (body && typeof body === "object" && "code" in body && "message" in body) {
    const b = body as { code: string; message: string; agent_id?: string | null };
    const code = KNOWN_CODES.has(b.code) ? (b.code as ErrorCode) : "unknown";
    return new ApiError(res.status, code, String(b.message), b.agent_id ?? null);
  }
  return new ApiError(res.status, "unknown", res.statusText || `Request failed (${res.status})`);
}

export type FetchLike = typeof fetch;

export interface ApiClient {
  listAgents(): Promise<Agent[]>;
  createAgent(body: AgentCreate): Promise<Agent>;
  updateAgent(id: string, body: AgentUpdate): Promise<Agent>;
  getProviders(refresh?: boolean): Promise<ProvidersResponse>;
  listConversations(): Promise<Conversation[]>;
  createConversation(body: ConversationCreate): Promise<Conversation>;
  getConversation(id: string): Promise<ConversationDetail>;
  sendMessage(
    conversationId: string,
    content: string,
    attachments?: FileRef[],
  ): Promise<SendMessageResponse>;
  stopRun(runId: string): Promise<Run>;
  getSettings(): Promise<Settings>;
  updateSettings(body: SettingsUpdate): Promise<Settings>;
  exportConversation(id: string): Promise<{ filename: string; blob: Blob }>;
  /** Sub-directories of `path` (default: the user's home), for the working-directory picker. */
  listDirectories(path?: string, signal?: AbortSignal): Promise<DirectoryListing>;
  /** Files in an agent's working directory matching `q`, for `@` mentions. */
  searchAgentFiles(
    agentId: string,
    q: string,
    limit?: number,
    signal?: AbortSignal,
  ): Promise<FileSearchResponse>;
}

export function createApiClient(fetchImpl: FetchLike = (...a) => fetch(...a)): ApiClient {
  async function request<T>(path: string, init?: RequestInit): Promise<T> {
    let res: Response;
    try {
      res = await fetchImpl(`/api${path}`, {
        ...init,
        headers: {
          Accept: "application/json",
          ...(init?.body ? { "Content-Type": "application/json" } : {}),
          ...init?.headers,
        },
      });
    } catch (e) {
      if (e instanceof DOMException && e.name === "AbortError") throw e;
      throw new ApiError(0, "network_error", "Cannot reach the SmartRail backend.");
    }
    if (!res.ok) throw await toApiError(res);
    return (await res.json()) as T;
  }
  const json = (method: string, body: unknown): RequestInit => ({
    method,
    body: JSON.stringify(body),
  });

  return {
    listAgents: () => request("/agents?include_archived=true"),
    createAgent: (b) => request("/agents", json("POST", b)),
    updateAgent: (id, b) => request(`/agents/${encodeURIComponent(id)}`, json("PATCH", b)),
    getProviders: (refresh = false) => request(`/providers${refresh ? "?refresh=true" : ""}`),
    listConversations: () => request("/conversations"),
    createConversation: (b) => request("/conversations", json("POST", b)),
    getConversation: (id) => request(`/conversations/${encodeURIComponent(id)}`),
    sendMessage: (id, content, attachments = []) =>
      request(
        `/conversations/${encodeURIComponent(id)}/messages`,
        json("POST", attachments.length > 0 ? { content, attachments } : { content }),
      ),
    stopRun: (id) => request(`/runs/${encodeURIComponent(id)}/stop`, { method: "POST" }),
    getSettings: () => request("/settings"),
    updateSettings: (b) => request("/settings", json("PATCH", b)),
    async exportConversation(id) {
      let res: Response;
      try {
        res = await fetchImpl(`/api/conversations/${encodeURIComponent(id)}/export`);
      } catch {
        throw new ApiError(0, "network_error", "Cannot reach the SmartRail backend.");
      }
      if (!res.ok) throw await toApiError(res);
      const disposition = res.headers.get("Content-Disposition") ?? "";
      const match = /filename\*?=(?:UTF-8'')?"?([^";]+)"?/i.exec(disposition);
      const filename = match ? decodeURIComponent(match[1]) : "transcript.md";
      return { filename, blob: await res.blob() };
    },
    listDirectories: (path, signal) =>
      request(`/fs/directories${path ? `?${new URLSearchParams({ path })}` : ""}`, { signal }),
    searchAgentFiles: (agentId, q, limit, signal) => {
      const params = new URLSearchParams({ q });
      if (limit !== undefined) params.set("limit", String(limit));
      return request(`/agents/${encodeURIComponent(agentId)}/files?${params}`, { signal });
    },
  };
}

export const api: ApiClient = createApiClient();
