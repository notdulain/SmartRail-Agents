import { render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it } from "vitest";
import App from "./App";
import {
  makeAgent,
  makeConversation,
  makeMessage,
  makeProviders,
  makeSettings,
  makeUserMessage,
} from "./test/fixtures";
import { mockFetch } from "./test/mockFetch";

function routes(extra: Record<string, (r: never) => unknown> = {}) {
  return mockFetch({
    "GET /api/agents": () => [makeAgent(), makeAgent({ id: "agt_old", name: "Archived Ann", archived: true })],
    "GET /api/conversations": () => [
      makeConversation(),
      makeConversation({ id: "cnv_g", type: "group", title: "Delay announcements", topic: "x", participant_ids: ["agt_1", "agt_2"] }),
    ],
    "GET /api/settings": () => makeSettings({ coordinator_agent_id: "agt_1" }),
    "GET /api/providers": () => makeProviders(),
    ...extra,
  } as never);
}

describe("App", () => {
  it("renders the sidebar with agents, discussions (incl. direct chats) and actions", async () => {
    routes();
    render(<App />);
    expect(await screen.findByRole("button", { name: "Chat with Passenger" })).toBeInTheDocument();
    expect(screen.getByRole("button", { name: /Chat with Passenger$/ })).toBeInTheDocument();
    const nav = screen.getByRole("navigation");
    expect(within(nav).getByText("Delay announcements")).toBeInTheDocument();
    expect(within(nav).getByText("Chat with Passenger")).toBeInTheDocument();
    expect(screen.getAllByRole("button", { name: "New agent" }).length).toBeGreaterThan(0);
    expect(screen.getAllByRole("button", { name: /New discussion/ }).length).toBeGreaterThan(0);
    expect(screen.getByRole("button", { name: "Settings" })).toBeInTheDocument();
    // Archived agents are tucked away, not offered for chat.
    expect(screen.getByText("Archived (1)")).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "Chat with Archived Ann" })).not.toBeInTheDocument();
  });

  it("shows the empty state when there are no agents", async () => {
    routes({ "GET /api/agents": () => [], "GET /api/conversations": () => [] });
    render(<App />);
    expect(await screen.findByText("Create your first agent")).toBeInTheDocument();
    expect(screen.getByText("No agents yet. Create one to begin.")).toBeInTheDocument();
  });

  it("shows a retry state when the backend is unreachable", async () => {
    routes({ "GET /api/agents": () => Promise.reject(new TypeError("Failed to fetch")) });
    render(<App />);
    expect(await screen.findByText(/Cannot reach the SmartRail backend/)).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Try again" })).toBeInTheDocument();
  });

  it("opens the agent editor from the Edit agent action on a model_unavailable failure", async () => {
    window.location.hash = "#/c/cnv_1";
    routes({
      "GET /api/conversations/cnv_1": () => ({
        conversation: makeConversation(),
        messages: [
          makeUserMessage(),
          makeMessage({
            status: "failed",
            content: "",
            error: "gpt-6-mini is no longer offered by openai",
            error_code: "model_unavailable",
          }),
        ],
        active_run_id: null,
      }),
    });
    render(<App />);
    await userEvent.click(await screen.findByRole("button", { name: "Edit agent" }));
    const dialog = await screen.findByRole("dialog", { name: "Edit agent" });
    expect(within(dialog).getByLabelText("Name")).toHaveValue("Passenger");
    expect(within(dialog).getByText(/Revision 1/)).toBeInTheDocument();
  });

  it("opens Settings and saves a PATCH with only the changed fields", async () => {
    const api = routes({
      "PATCH /api/settings": ({ body }: { body: object }) => ({ ...makeSettings(), ...body }),
    });
    render(<App />);
    await userEvent.click(await screen.findByRole("button", { name: "Settings" }));
    const dialog = await screen.findByRole("dialog", { name: "Settings" });
    expect(within(dialog).getByLabelText("Spent this month")).toHaveTextContent("$0.42");
    await userEvent.type(within(dialog).getByLabelText("Project brief"), "Regional trains");
    await userEvent.click(within(dialog).getByRole("button", { name: "Save settings" }));
    await waitFor(() => expect(api.calls.some((c) => c.method === "PATCH")).toBe(true));
    expect(api.calls.find((c) => c.method === "PATCH")!.body).toEqual({ project_brief: "Regional trains" });
  });
});
