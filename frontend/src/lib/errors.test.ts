import { describe, expect, it } from "vitest";
import { ApiError } from "../api/client";
import { describeError, needsAgentEdit } from "./errors";

describe("describeError", () => {
  it.each([
    ["provider_unavailable", "Provider unavailable", /Connect the provider/],
    ["model_unavailable", "Model unavailable", /Edit the agent/],
    ["rate_limited", "Rate limited", /Wait a moment/],
    ["budget_exhausted", "Budget used up", /Settings/],
    ["validation_error", "Check your input", /bad value/],
  ] as const)("describes %s", (code, title, text) => {
    const d = describeError(new ApiError(422, code, "bad value"));
    expect(d.title).toBe(title);
    expect(d.message).toMatch(text);
  });

  it("describes network failures and unknown errors", () => {
    expect(describeError(new ApiError(0, "network_error", "Cannot reach")).title).toBe("Backend unreachable");
    expect(describeError(new Error("x")).message).toBe("x");
    expect(describeError("weird").message).toBe("Please try again.");
  });

  it("only offers agent edits for model/provider failures", () => {
    expect(needsAgentEdit("model_unavailable")).toBe(true);
    expect(needsAgentEdit("provider_unavailable")).toBe(true);
    expect(needsAgentEdit("rate_limited")).toBe(false);
    expect(needsAgentEdit(null)).toBe(false);
  });
});
