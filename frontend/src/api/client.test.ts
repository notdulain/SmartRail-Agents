import { describe, expect, it } from "vitest";
import { errorResponse, mockFetch } from "../test/mockFetch";
import { ApiError, createApiClient } from "./client";

describe("api client file routes", () => {
  it("lists directories with and without a path", async () => {
    const m = mockFetch({
      "GET /api/fs/directories": ({ url }) => ({
        path: url.searchParams.get("path") ?? "C:\Users\me",
        parent: null,
        entries: [],
        home: "C:\Users\me",
        roots: ["C:\\"],
      }),
    });
    const api = createApiClient();
    expect((await api.listDirectories()).path).toBe("C:\Users\me");
    expect(m.calls[0].url.search).toBe("");
    expect((await api.listDirectories("D:\Work & Play")).path).toBe("D:\Work & Play");
    expect(m.calls[1].url.searchParams.get("path")).toBe("D:\Work & Play");
  });

  it("searches an agent's files with q and limit", async () => {
    const m = mockFetch({
      "GET /api/agents/agt_1/files": () => ({ root: "/w", files: [], truncated: false }),
    });
    await createApiClient().searchAgentFiles("agt_1", "src/ma", 20);
    expect(m.calls[0].url.searchParams.get("q")).toBe("src/ma");
    expect(m.calls[0].url.searchParams.get("limit")).toBe("20");
  });

  it("surfaces 422 messages as ApiError", async () => {
    mockFetch({
      "GET /api/fs/directories": () => errorResponse(422, "validation_error", "Not a directory"),
    });
    const err = await createApiClient().listDirectories("/nope").catch((e) => e);
    expect(err).toBeInstanceOf(ApiError);
    expect(err.code).toBe("validation_error");
    expect(err.message).toBe("Not a directory");
  });

  it("sends attachments only when there are some", async () => {
    const m = mockFetch({
      "POST /api/conversations/cnv_1/messages": () => ({ run_id: "r", user_message_id: "u" }),
    });
    const api = createApiClient();
    await api.sendMessage("cnv_1", "hi");
    await api.sendMessage("cnv_1", "see", [{ agent_id: "agt_1", path: "a/b.md" }]);
    expect(m.calls[0].body).toEqual({ content: "hi" });
    expect(m.calls[1].body).toEqual({
      content: "see",
      attachments: [{ agent_id: "agt_1", path: "a/b.md" }],
    });
  });
});
