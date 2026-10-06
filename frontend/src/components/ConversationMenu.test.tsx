import { screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it, vi } from "vitest";
import { errorResponse, mockFetch } from "../test/mockFetch";
import { renderWithProviders } from "../test/render";
import { ConversationMenu } from "./ConversationMenu";

describe("ConversationMenu", () => {
  it("exports only when Export transcript is clicked and triggers a download", async () => {
    const created: Blob[] = [];
    const urlApi = URL as unknown as Record<string, unknown>;
    urlApi.createObjectURL = vi.fn((b: Blob) => (created.push(b), "blob:fake"));
    urlApi.revokeObjectURL = vi.fn();
    const clicked: string[] = [];
    const orig = HTMLAnchorElement.prototype.click;
    HTMLAnchorElement.prototype.click = function (this: HTMLAnchorElement) {
      clicked.push(this.download);
    };
    const api = mockFetch({
      "GET /api/agents": () => [],
      "GET /api/conversations": () => [],
      "GET /api/settings": () => ({}),
      "GET /api/providers": () => ({ providers: [], refreshed_at: "" }),
      "GET /api/conversations/cnv_1/export": () =>
        new Response("# Transcript", {
          headers: {
            "Content-Type": "text/markdown",
            "Content-Disposition": 'attachment; filename="chat-with-passenger.md"',
          },
        }),
    });
    try {
      renderWithProviders(<ConversationMenu conversationId="cnv_1" />);
      await userEvent.click(screen.getByRole("button", { name: "Conversation menu" }));
      // Opening the menu alone must not generate a transcript.
      expect(api.count("GET /api/conversations/cnv_1/export")).toBe(0);
      await userEvent.click(await screen.findByRole("menuitem", { name: "Export transcript" }));
      await waitFor(() => expect(clicked).toEqual(["chat-with-passenger.md"]));
      expect(api.count("GET /api/conversations/cnv_1/export")).toBe(1);
      expect(created).toHaveLength(1);
    } finally {
      HTMLAnchorElement.prototype.click = orig;
    }
  });

  it("shows an error toast when export fails", async () => {
    mockFetch({
      "GET /api/agents": () => [],
      "GET /api/conversations": () => [],
      "GET /api/settings": () => ({}),
      "GET /api/providers": () => ({ providers: [], refreshed_at: "" }),
      "GET /api/conversations/cnv_1/export": () => errorResponse(404, "not_found", "Conversation not found"),
    });
    renderWithProviders(<ConversationMenu conversationId="cnv_1" />);
    await userEvent.click(screen.getByRole("button", { name: "Conversation menu" }));
    await userEvent.click(await screen.findByRole("menuitem", { name: "Export transcript" }));
    expect(await screen.findByRole("alert")).toHaveTextContent("Conversation not found");
  });
});
