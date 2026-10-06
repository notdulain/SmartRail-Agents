import { screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it, vi } from "vitest";
import { makeAgent, makeProviders, makeSettings } from "../test/fixtures";
import { errorResponse, mockFetch } from "../test/mockFetch";
import { renderWithProviders } from "../test/render";
import { SettingsDialog, validateSettings } from "./SettingsDialog";

describe("validateSettings", () => {
  it("accepts valid values", () => {
    expect(validateSettings({ budget: "5", participantTokens: "1024", coordinatorTokens: "2048" })).toEqual({});
    expect(validateSettings({ budget: "0", participantTokens: "1", coordinatorTokens: "1" })).toEqual({});
  });
  it("rejects negative or blank budgets and non-integer token limits", () => {
    const e = validateSettings({ budget: "-1", participantTokens: "0", coordinatorTokens: "1.5" });
    expect(e.budget).toBeTruthy();
    expect(e.participant).toBeTruthy();
    expect(e.coordinator).toBeTruthy();
    expect(validateSettings({ budget: "", participantTokens: "1", coordinatorTokens: "1" }).budget).toBeTruthy();
  });
});

function setup(settings = makeSettings({ coordinator_agent_id: "agt_1" }), patch?: () => unknown) {
  const onClose = vi.fn();
  const api = mockFetch({
    "GET /api/agents": () => [makeAgent(), makeAgent({ id: "agt_2", name: "Driver" }), makeAgent({ id: "agt_3", name: "Retired", archived: true })],
    "GET /api/conversations": () => [],
    "GET /api/settings": () => settings,
    "GET /api/providers": () => makeProviders(),
    "PATCH /api/settings": patch ?? (({ body }) => ({ ...settings, ...(body as object) })),
  });
  renderWithProviders(<SettingsDialog settings={settings} onClose={onClose} />);
  return { api, onClose };
}

describe("SettingsDialog", () => {
  it("shows spend read-only and offers only non-archived agents as coordinator", async () => {
    setup();
    expect(screen.getByLabelText("Spent this month")).toHaveTextContent("$0.42 of $5.00");
    await waitFor(() => expect(screen.getByRole("option", { name: "Driver" })).toBeInTheDocument());
    expect(screen.queryByRole("option", { name: "Retired" })).not.toBeInTheDocument();
  });

  it("blocks invalid numbers with inline errors", async () => {
    const { api } = setup();
    const budget = screen.getByLabelText("OpenRouter monthly budget (USD)");
    await userEvent.clear(budget);
    await userEvent.type(budget, "-3");
    await userEvent.click(screen.getByRole("button", { name: "Save settings" }));
    expect(await screen.findByText("Enter an amount of 0 or more.")).toBeInTheDocument();
    expect(budget).toHaveFocus();
    expect(api.count("PATCH /api/settings")).toBe(0);
  });

  it("patches only changed fields, including the coordinator and budget", async () => {
    const { api, onClose } = setup(makeSettings());
    await waitFor(() => expect(screen.getByRole("option", { name: "Driver" })).toBeInTheDocument());
    await userEvent.selectOptions(screen.getByLabelText("Coordinator agent"), "Driver");
    const budget = screen.getByLabelText("OpenRouter monthly budget (USD)");
    await userEvent.clear(budget);
    await userEvent.type(budget, "7.5");
    await userEvent.click(screen.getByRole("button", { name: "Save settings" }));
    await waitFor(() => expect(onClose).toHaveBeenCalled());
    expect(api.calls.find((c) => c.method === "PATCH")!.body).toEqual({
      coordinator_agent_id: "agt_2",
      openrouter_monthly_budget_usd: 7.5,
    });
  });

  it("shows server validation errors inline", async () => {
    const { onClose } = setup(makeSettings(), () => errorResponse(422, "validation_error", "budget too large"));
    await userEvent.type(screen.getByLabelText("Project brief"), "x");
    await userEvent.click(screen.getByRole("button", { name: "Save settings" }));
    expect(await screen.findByRole("alert")).toHaveTextContent("budget too large");
    expect(onClose).not.toHaveBeenCalled();
  });
});
