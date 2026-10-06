import { ApiError } from "../api/client";
import type { ErrorCode } from "../api/types";

export interface ErrorDescription {
  title: string;
  message: string;
}

type Code = ErrorCode | "network_error" | "unknown";

const TITLES: Record<Code, string> = {
  not_found: "Not found",
  validation_error: "Check your input",
  provider_unavailable: "Provider unavailable",
  model_unavailable: "Model unavailable",
  rate_limited: "Rate limited",
  budget_exhausted: "Budget used up",
  run_active: "A reply is already in progress",
  runtime_error: "Something went wrong",
  network_error: "Backend unreachable",
  unknown: "Something went wrong",
};

const HINTS: Partial<Record<Code, string>> = {
  provider_unavailable:
    "Connect the provider in OpenCode and refresh the model list, or pick another model.",
  model_unavailable: "The provider no longer offers this model. Edit the agent to pick another.",
  rate_limited: "The provider is limiting requests. Wait a moment, then try again.",
  budget_exhausted: "Raise the OpenRouter monthly budget in Settings, or wait until next month.",
  run_active: "Wait for it to finish, or press Stop.",
};

export function describeCode(code: Code, message?: string | null): ErrorDescription {
  const detail = message?.trim();
  const parts = [detail, HINTS[code]].filter((p): p is string => !!p);
  return { title: TITLES[code], message: parts.length ? parts.join(" ") : "Please try again." };
}

export function describeError(err: unknown): ErrorDescription {
  if (err instanceof ApiError) return describeCode(err.code, err.message);
  if (err instanceof Error) return describeCode("unknown", err.message);
  return describeCode("unknown");
}

/** Failed messages with these codes offer the Edit agent action. */
export function needsAgentEdit(code: ErrorCode | null | undefined): boolean {
  return code === "model_unavailable" || code === "provider_unavailable";
}

export function isApiCode(err: unknown, code: ErrorCode): boolean {
  return err instanceof ApiError && err.code === code;
}
