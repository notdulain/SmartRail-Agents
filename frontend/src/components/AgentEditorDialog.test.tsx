import { screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it, vi } from "vitest";
import { AGENT_TEMPLATES } from "../lib/templates";
import { makeAgent, makeProviders } from "../test/fixtures";
import { errorResponse, mockFetch } from "../test/mockFetch";
import { renderWithProviders } from "../test/render";
import { AgentEditorDialog, validateAgentForm } from "./AgentEditorDialog";
import type { Agent } from "../api/types";

function setup(agent: Agent | null = null, extra: Record<string, () => unknown> = {}) {
  const onClose = vi.fn();
  const api = mockFetch({
    "GET /api/agents": () => [makeAgent()],
    "GET /api/conversations": () => [],
    "GET /api/settings": () => ({ project_brief: "" }),
    "GET /api/providers": () => makeProviders(),
    "POST /api/agents": ({ body }) => ({ ...makeAgent({ id: "agt_new" }), ...(body as object) }),
    "PATCH /api/agents/agt_1": ({ body }) => ({ ...makeAgent(), ...(body as object), revision: 2 }),
    ...extra,
  });
  renderWithProviders(<AgentEditorDialog agent={agent} onClose={onClose} />);
  return { api, onClose };
}

async function waitForCatalog() {
  await screen.findByRole("option", { name: /GPT-6 Sol/ });
}

describe("validateAgentForm", () => {
  const ok = { providerId: "openai", modelId: "gpt-6-sol" };
  it("requires name, persona and a model", () => {
    expect(validateAgentForm({ name: " ", persona: "", choice: null }, makeProviders())).toEqual({
      name: "Enter a name.",
      persona: "Describe how this agent should behave.",
      model: "Choose a provider and model.",
    });
  });
  it("accepts a complete connected choice", () => {
    expect(validateAgentForm({ name: "A", persona: "p", choice: ok }, makeProviders())).toEqual({});
  });
  it("rejects a disconnected provider but tolerates an unchanged saved model", () => {
    const dead = { providerId: "anthropic", modelId: "claude-sonnet-5-5" };
    const values = { name: "A", persona: "p", choice: dead };
    expect(validateAgentForm(values, makeProviders()).model).toMatch(/not connected/);
    expect(validateAgentForm(values, makeProviders(), dead)).toEqual({});
  });
  it("enforces length limits", () => {
    const errs = validateAgentForm(
      { name: "x".repeat(81), persona: "y".repeat(8001), choice: ok },
      makeProviders(),
    );
    expect(errs.name).toMatch(/too long/);
    expect(errs.persona).toMatch(/too long/);
  });
});

describe("AgentEditorDialog", () => {
  it("shows inline validation errors and does not call the API", async () => {
    const { api } = setup();
    await waitForCatalog();
    await userEvent.click(screen.getByRole("button", { name: "Create agent" }));
    expect(await screen.findByText("Enter a name.")).toBeInTheDocument();
    expect(screen.getByText("Describe how this agent should behave.")).toBeInTheDocument();
    expect(screen.getByText("Choose a provider and model.")).toBeInTheDocument();
    expect(screen.getByLabelText("Name")).toHaveAttribute("aria-invalid", "true");
    expect(screen.getByLabelText("Name")).toHaveFocus();
    expect(api.count("POST /api/agents")).toBe(0);
  });

  it("offers all ten templates and fills name and persona from the chosen one", async () => {
    setup();
    await waitForCatalog();
    const select = screen.getByLabelText("Template (optional)");
    const options = Array.from((select as HTMLSelectElement).options).map((o) => o.text);
    expect(options).toEqual([
      "No template",
      "Passenger",
      "Driver",
      "Station manager",
      "Railway controller",
      "Customer-service representative",
      "Catering provider",
      "Security engineer",
      "QA engineer",
      "Accessibility specialist",
      "Product owner",
    ]);
    expect(AGENT_TEMPLATES).toHaveLength(10);
    await userEvent.selectOptions(select, "Railway controller");
    expect(screen.getByLabelText("Name")).toHaveValue("Railway controller");
    expect(screen.getByLabelText("Persona instructions")).toHaveValue(
      AGENT_TEMPLATES.find((t) => t.id === "railway-controller")!.persona,
    );
  });

  it("creates an agent with the four fields", async () => {
    const { api, onClose } = setup();
    await waitForCatalog();
    await userEvent.selectOptions(screen.getByLabelText("Template (optional)"), "Driver");
    await userEvent.click(screen.getByRole("option", { name: /DeepSeek Chat/ }));
    await userEvent.click(screen.getByRole("button", { name: "Create agent" }));
    await waitFor(() => expect(onClose).toHaveBeenCalled());
    const post = api.calls.find((c) => c.method === "POST" && c.path === "/api/agents")!;
    expect(post.body).toEqual({
      name: "Driver",
      persona: AGENT_TEMPLATES.find((t) => t.id === "driver")!.persona,
      provider_id: "openrouter",
      model_id: "deepseek/deepseek-chat",
      tool_access: "none",
    });
  });

  it("will not save a model from a disconnected provider", async () => {
    const { api } = setup();
    await waitForCatalog();
    await userEvent.type(screen.getByLabelText("Name"), "Claude agent");
    await userEvent.type(screen.getByLabelText("Persona instructions"), "Be thoughtful");
    await userEvent.click(screen.getByRole("option", { name: /Claude Sonnet 5.5/ }));
    await userEvent.click(screen.getByRole("button", { name: "Create agent" }));
    expect(await screen.findByText("Choose a provider and model.")).toBeInTheDocument();
    expect(api.count("POST /api/agents")).toBe(0);
  });

  it("edits: shows the revision and the next-run note, and sends only changed fields", async () => {
    const { api, onClose } = setup(makeAgent({ revision: 3 }));
    await waitForCatalog();
    expect(screen.getByText(/Revision 3/)).toBeInTheDocument();
    expect(screen.getByText(/apply to the next run/)).toBeInTheDocument();
    const name = screen.getByLabelText("Name");
    await userEvent.clear(name);
    await userEvent.type(name, "Commuter");
    await userEvent.click(screen.getByRole("button", { name: "Save changes" }));
    await waitFor(() => expect(onClose).toHaveBeenCalled());
    const patch = api.calls.find((c) => c.method === "PATCH")!;
    expect(patch.body).toEqual({ name: "Commuter" });
  });

  it("lets an unchanged agent on a now-disconnected provider be renamed", async () => {
    const stale = makeAgent({ provider_id: "anthropic", model_id: "claude-sonnet-5-5" });
    const { api, onClose } = setup(stale, {
      "PATCH /api/agents/agt_1": () => ({ ...stale, name: "Renamed" }),
    });
    await waitForCatalog();
    expect(screen.getByText(/This provider is not connected/)).toBeInTheDocument();
    const name = screen.getByLabelText("Name");
    await userEvent.clear(name);
    await userEvent.type(name, "Renamed");
    await userEvent.click(screen.getByRole("button", { name: "Save changes" }));
    await waitFor(() => expect(onClose).toHaveBeenCalled());
    expect(api.calls.find((c) => c.method === "PATCH")!.body).toEqual({ name: "Renamed" });
  });

  it("archives after confirmation", async () => {
    const { api, onClose } = setup(makeAgent());
    await waitForCatalog();
    await userEvent.click(screen.getByRole("button", { name: "Archive" }));
    expect(api.count("PATCH /api/agents/agt_1")).toBe(0);
    await userEvent.click(screen.getByRole("button", { name: "Confirm archive" }));
    await waitFor(() => expect(onClose).toHaveBeenCalled());
    expect(api.calls.find((c) => c.method === "PATCH")!.body).toEqual({ archived: true });
  });

  it("shows a server error inline", async () => {
    const { onClose } = setup(null, {
      "POST /api/agents": () => errorResponse(422, "validation_error", "name: too short"),
    });
    await waitForCatalog();
    await userEvent.type(screen.getByLabelText("Name"), "X");
    await userEvent.type(screen.getByLabelText("Persona instructions"), "Y");
    await userEvent.click(screen.getByRole("option", { name: /GPT-6 Sol/ }));
    await userEvent.click(screen.getByRole("button", { name: "Create agent" }));
    expect(await screen.findByRole("alert")).toHaveTextContent("name: too short");
    expect(onClose).not.toHaveBeenCalled();
  });

  it("refreshes the catalog with ?refresh=true", async () => {
    const { api } = setup();
    await waitForCatalog();
    await userEvent.click(screen.getByRole("button", { name: "Refresh providers" }));
    await waitFor(() =>
      expect(api.calls.some((c) => c.path === "/api/providers" && c.url.searchParams.get("refresh") === "true")).toBe(true),
    );
  });
});
