import { screen } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";
import { makeAgent, makeProviders, makeSettings } from "../test/fixtures";
import { mockFetch } from "../test/mockFetch";
import { renderWithProviders } from "../test/render";
import { Sidebar } from "./Sidebar";

function setup() {
  mockFetch({
    "GET /api/agents": () => [
      makeAgent({ id: "a1", name: "Plain" }),
      makeAgent({
        id: "a2",
        name: "Reader",
        working_directory: "C:\\Users\\me\\rail",
        tool_access: "read_only",
      }),
      makeAgent({
        id: "a3",
        name: "Writer",
        working_directory: "/home/me/docs",
        tool_access: "read_write",
      }),
    ],
    "GET /api/conversations": () => [],
    "GET /api/settings": () => makeSettings(),
    "GET /api/providers": () => makeProviders(),
  });
  const noop = vi.fn();
  renderWithProviders(
    <Sidebar
      open
      selectedId={null}
      onClose={noop}
      onSelect={noop}
      onNewAgent={noop}
      onNewDiscussion={noop}
      onEditAgent={noop}
      onChatWithAgent={noop}
      onOpenSettings={noop}
    />,
  );
}

describe("Sidebar agent folders", () => {
  it("marks agents with a working directory and their access level", async () => {
    setup();
    const reader = await screen.findByRole("button", { name: "Chat with Reader" });
    expect(reader).toHaveAccessibleDescription("Works in rail, read only");
    expect(reader.querySelector("[title]")?.getAttribute("title")).toContain("C:\\Users\\me\\rail");

    const writer = screen.getByRole("button", { name: "Chat with Writer" });
    expect(writer).toHaveAccessibleDescription("Works in docs, read & write");
    expect(writer).toHaveTextContent("write");

    expect(screen.getByRole("button", { name: "Chat with Plain" })).not.toHaveAccessibleDescription(/Works in/);
  });
});
