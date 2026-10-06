import { screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it, vi } from "vitest";
import { makeAgent, makeConversation, makeProviders, makeSettings } from "../test/fixtures";
import { mockFetch } from "../test/mockFetch";
import { renderWithProviders } from "../test/render";
import { NewDiscussionDialog, hasCoordinator, validateDiscussion } from "./NewDiscussionDialog";

const agents = [
  makeAgent({ id: "a1", name: "Passenger" }),
  makeAgent({ id: "a2", name: "Driver" }),
  makeAgent({ id: "a3", name: "Station manager" }),
  makeAgent({ id: "a4", name: "Retired", archived: true }),
];

function setup(settings = makeSettings({ coordinator_agent_id: "a1" })) {
  const props = {
    onClose: vi.fn(),
    onCreated: vi.fn(),
    onOpenSettings: vi.fn(),
    onNewAgent: vi.fn(),
  };
  const api = mockFetch({
    "GET /api/agents": () => agents,
    "GET /api/conversations": () => [],
    "GET /api/settings": () => settings,
    "GET /api/providers": () => makeProviders(),
    "POST /api/conversations": ({ body }) => makeConversation({ id: "cnv_new", ...(body as object) }),
  });
  renderWithProviders(<NewDiscussionDialog {...props} />);
  return { props, api };
}

describe("validateDiscussion", () => {
  it("requires an agent for direct chats", () => {
    expect(validateDiscussion({ type: "direct", agentId: "", groupIds: [], topic: "" }).agent).toBeTruthy();
    expect(validateDiscussion({ type: "direct", agentId: "a1", groupIds: [], topic: "" })).toEqual({});
  });
  it("requires two agents and a topic for groups, with no upper limit", () => {
    const e = validateDiscussion({ type: "group", agentId: "", groupIds: ["a1"], topic: "  " });
    expect(e.group).toBeTruthy();
    expect(e.topic).toBeTruthy();
    const many = Array.from({ length: 25 }, (_, i) => `a${i}`);
    expect(validateDiscussion({ type: "group", agentId: "", groupIds: many, topic: "x" })).toEqual({});
  });
});

describe("hasCoordinator", () => {
  it("is false when unset, missing or archived", () => {
    expect(hasCoordinator(makeSettings(), agents)).toBe(false);
    expect(hasCoordinator(makeSettings({ coordinator_agent_id: "nope" }), agents)).toBe(false);
    expect(hasCoordinator(makeSettings({ coordinator_agent_id: "a4" }), agents)).toBe(false);
    expect(hasCoordinator(makeSettings({ coordinator_agent_id: "a2" }), agents)).toBe(true);
  });
});

describe("NewDiscussionDialog", () => {
  it("starts a direct chat with the chosen agent and hides archived agents", async () => {
    const { props, api } = setup();
    const select = await screen.findByLabelText("Agent");
    await waitFor(() => expect(screen.getByRole("option", { name: /Passenger/ })).toBeInTheDocument());
    expect(screen.queryByRole("option", { name: /Retired/ })).not.toBeInTheDocument();
    await userEvent.click(screen.getByRole("button", { name: "Start chat" }));
    expect(await screen.findByText("Choose an agent to chat with.")).toBeInTheDocument();
    await userEvent.selectOptions(select, "a2");
    await userEvent.click(screen.getByRole("button", { name: "Start chat" }));
    await waitFor(() => expect(props.onCreated).toHaveBeenCalled());
    expect(api.calls.find((c) => c.method === "POST")!.body).toEqual({
      type: "direct",
      participant_ids: ["a2"],
    });
  });

  it("validates group size and topic, then creates the group", async () => {
    const { props, api } = setup();
    await userEvent.click(await screen.findByRole("radio", { name: /Group discussion/ }));
    await screen.findByRole("checkbox", { name: /Passenger/ });
    await userEvent.click(screen.getByRole("button", { name: "Create discussion" }));
    expect(await screen.findByText("Choose at least two agents.")).toBeInTheDocument();
    expect(screen.getByText("Enter the topic to discuss.")).toBeInTheDocument();
    expect(api.count("POST /api/conversations")).toBe(0);

    await userEvent.click(screen.getByRole("checkbox", { name: /Passenger/ }));
    await userEvent.click(screen.getByRole("button", { name: "Create discussion" }));
    expect(await screen.findByText("Choose at least two agents.")).toBeInTheDocument(); // still one

    await userEvent.click(screen.getByRole("checkbox", { name: /Driver/ }));
    await userEvent.type(screen.getByLabelText("Topic"), "  Delay announcements  ");
    await userEvent.click(screen.getByRole("button", { name: "Create discussion" }));
    await waitFor(() => expect(props.onCreated).toHaveBeenCalled());
    expect(api.calls.find((c) => c.method === "POST")!.body).toEqual({
      type: "group",
      participant_ids: ["a1", "a2"],
      topic: "Delay announcements",
    });
  });

  it("warns when no coordinator is configured and links to Settings", async () => {
    const { props } = setup(makeSettings({ coordinator_agent_id: null }));
    await userEvent.click(await screen.findByRole("radio", { name: /Group discussion/ }));
    const note = await screen.findByRole("note");
    expect(note).toHaveTextContent(/need a coordinator/);
    await userEvent.click(screen.getByRole("button", { name: "Open Settings" }));
    expect(props.onOpenSettings).toHaveBeenCalled();
  });

  it("does not show the coordinator warning when one is configured", async () => {
    setup();
    await userEvent.click(await screen.findByRole("radio", { name: /Group discussion/ }));
    await screen.findByRole("checkbox", { name: /Passenger/ });
    expect(screen.queryByRole("note")).not.toBeInTheDocument();
  });
});
