import { screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it, vi } from "vitest";
import type { Conversation } from "../api/types";
import { makeAgent, makeConversation, makeProviders, makeSettings } from "../test/fixtures";
import { mockFetch } from "../test/mockFetch";
import { renderWithProviders } from "../test/render";
import { Sidebar } from "./Sidebar";

function setup(conversations: Conversation[] = []) {
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
    "GET /api/conversations": () => conversations,
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
  return { onSelect: noop };
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

describe("Sidebar discussion search", () => {
  const conversations = [
    makeConversation({ id: "c1", title: "Morning commute", topic: null }),
    makeConversation({ id: "c2", title: "Operations", topic: "Signal delays" }),
  ];

  it("filters by title or topic regardless of case, while keeping agents available", async () => {
    const user = userEvent.setup();
    const { onSelect } = setup(conversations);
    const search = screen.getByRole("searchbox", { name: "Search discussions" });
    await screen.findByRole("button", { name: /Morning commute/ });

    await user.type(search, "mOrNiNg");
    await user.click(screen.getByRole("button", { name: /Morning commute/ }));
    expect(onSelect).toHaveBeenCalledWith("c1");
    expect(screen.queryByRole("button", { name: /Operations/ })).not.toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Chat with Plain" })).toBeInTheDocument();

    await user.clear(search);
    await user.type(search, "SIGNAL");
    expect(screen.getByRole("button", { name: /Operations/ })).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: /Morning commute/ })).not.toBeInTheDocument();
  });

  it("shows an empty result and lets the user clear the search", async () => {
    const user = userEvent.setup();
    setup(conversations);
    const search = screen.getByRole("searchbox", { name: "Search discussions" });
    await screen.findByRole("button", { name: /Morning commute/ });

    await user.type(search, "unknown");
    expect(screen.getByRole("status")).toHaveTextContent("No matching discussions.");
    expect(screen.queryByText("No discussions yet.")).not.toBeInTheDocument();

    await user.click(screen.getByRole("button", { name: "Clear discussion search" }));
    expect(search).toHaveValue("");
    expect(screen.getByRole("button", { name: /Morning commute/ })).toBeInTheDocument();
    expect(screen.getByRole("button", { name: /Operations/ })).toBeInTheDocument();
  });
});
