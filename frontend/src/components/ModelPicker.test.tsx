import { render, screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { useState } from "react";
import { describe, expect, it, vi } from "vitest";
import type { ModelChoice } from "../lib/models";
import { makeProviders } from "../test/fixtures";
import { ModelPicker } from "./ModelPicker";

function Harness({
  onRefresh = vi.fn(),
  initial = null,
  loading = false,
  onChangeSpy = vi.fn(),
}: {
  onRefresh?: () => void;
  initial?: ModelChoice | null;
  loading?: boolean;
  onChangeSpy?: (c: ModelChoice) => void;
}) {
  const [value, setValue] = useState<ModelChoice | null>(initial);
  return (
    <ModelPicker
      providers={makeProviders()}
      loading={loading}
      value={value}
      onChange={(c) => {
        onChangeSpy(c);
        setValue(c);
      }}
      onRefresh={onRefresh}
    />
  );
}

describe("ModelPicker", () => {
  it("lists connected providers before disconnected ones and marks them", () => {
    render(<Harness />);
    const groups = screen.getAllByRole("group").map((g) => g.textContent ?? "");
    expect(groups[0]).toContain("OpenAI");
    expect(groups[1]).toContain("OpenRouter");
    expect(groups[2]).toContain("Anthropic");
    expect(groups[2]).toContain("Not connected");
    expect(within(screen.getAllByRole("group")[0]).getByText("Connected")).toBeInTheDocument();
  });

  it("shows prices per million tokens when present", () => {
    render(<Harness />);
    expect(screen.getByText("$0.30 in · $1.00 out per 1M tokens")).toBeInTheDocument();
    expect(screen.getAllByText("Price not listed").length).toBeGreaterThan(0);
  });

  it("filters by model name, id and provider as you type", async () => {
    render(<Harness />);
    await userEvent.type(screen.getByLabelText("Search providers and models"), "deepseek");
    expect(screen.getByRole("option", { name: /DeepSeek Chat/ })).toBeInTheDocument();
    expect(screen.queryByRole("option", { name: /GPT-6 Sol/ })).not.toBeInTheDocument();

    await userEvent.clear(screen.getByLabelText("Search providers and models"));
    await userEvent.type(screen.getByLabelText("Search providers and models"), "openai mini");
    expect(screen.getAllByRole("option")).toHaveLength(1);
    expect(screen.getByRole("option", { name: /GPT-6 Mini/ })).toBeInTheDocument();
  });

  it("shows an empty message when nothing matches", async () => {
    render(<Harness />);
    await userEvent.type(screen.getByLabelText("Search providers and models"), "zzzz");
    expect(screen.getByText(/No models match/)).toBeInTheDocument();
  });

  it("selects a connected model", async () => {
    const spy = vi.fn();
    render(<Harness onChangeSpy={spy} />);
    await userEvent.click(screen.getByRole("option", { name: /GPT-6 Sol/ }));
    expect(spy).toHaveBeenCalledWith({ providerId: "openai", modelId: "gpt-6-sol" });
    expect(screen.getByRole("option", { name: /GPT-6 Sol/ })).toHaveAttribute("aria-selected", "true");
    expect(screen.getByText(/Selected:/).parentElement).toHaveTextContent("OpenAI / GPT-6 Sol");
  });

  it("does not allow selecting a model of a disconnected provider", async () => {
    const spy = vi.fn();
    render(<Harness onChangeSpy={spy} />);
    const option = screen.getByRole("option", { name: /Claude Sonnet 5.5/ });
    expect(option).toHaveAttribute("aria-disabled", "true");
    await userEvent.click(option);
    expect(spy).not.toHaveBeenCalled();
    expect(screen.getByText(/Connect Anthropic in OpenCode/)).toBeInTheDocument();
  });

  it("warns when the current value belongs to a disconnected provider", () => {
    render(<Harness initial={{ providerId: "anthropic", modelId: "claude-sonnet-5-5" }} />);
    expect(screen.getByText(/This provider is not connected/)).toBeInTheDocument();
  });

  it("warns when the current model is no longer in the catalog", () => {
    render(<Harness initial={{ providerId: "openai", modelId: "gpt-5-retired" }} />);
    expect(screen.getByText(/not in the current catalog/)).toBeInTheDocument();
  });

  it("calls onRefresh when Refresh is pressed and disables it while loading", async () => {
    const onRefresh = vi.fn();
    const { rerender } = render(<Harness onRefresh={onRefresh} />);
    await userEvent.click(screen.getByRole("button", { name: "Refresh providers" }));
    expect(onRefresh).toHaveBeenCalledTimes(1);
    rerender(<Harness onRefresh={onRefresh} loading />);
    expect(screen.getByRole("button", { name: "Refresh providers" })).toBeDisabled();
  });

  it("moves between options with the arrow keys", async () => {
    render(<Harness />);
    await userEvent.click(screen.getByLabelText("Search providers and models"));
    await userEvent.keyboard("{ArrowDown}");
    expect(screen.getByRole("option", { name: /GPT-6 Sol/ })).toHaveFocus();
    await userEvent.keyboard("{ArrowDown}");
    expect(screen.getByRole("option", { name: /GPT-6 Mini/ })).toHaveFocus();
    await userEvent.keyboard("{ArrowUp}{ArrowUp}");
    expect(screen.getByLabelText("Search providers and models")).toHaveFocus();
  });
});
