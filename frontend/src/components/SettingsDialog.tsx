import { useRef, useState, type FormEvent } from "react";
import { api, ApiError } from "../api/client";
import type { Settings, SettingsUpdate } from "../api/types";
import { describeError } from "../lib/errors";
import { useAppData } from "../state/appData";
import { useToasts } from "../state/toast";
import { Button } from "./ui/Button";
import { Dialog } from "./ui/Dialog";
import { Field, inputClass } from "./ui/Field";

export interface SettingsErrors {
  budget?: string;
  participant?: string;
  coordinator?: string;
}

export function validateSettings(v: {
  budget: string;
  participantTokens: string;
  coordinatorTokens: string;
}): SettingsErrors {
  const errors: SettingsErrors = {};
  const budget = Number(v.budget);
  if (v.budget.trim() === "" || !Number.isFinite(budget) || budget < 0)
    errors.budget = "Enter an amount of 0 or more.";
  const isPosInt = (s: string) => /^\d+$/.test(s.trim()) && Number(s) >= 1;
  if (!isPosInt(v.participantTokens)) errors.participant = "Enter a whole number of 1 or more.";
  if (!isPosInt(v.coordinatorTokens)) errors.coordinator = "Enter a whole number of 1 or more.";
  return errors;
}

export function SettingsDialog({ settings, onClose }: { settings: Settings; onClose(): void }) {
  const { agents, setSettings, refreshSettings } = useAppData();
  const toasts = useToasts();
  const active = agents.filter((a) => !a.archived);

  const [brief, setBrief] = useState(settings.project_brief);
  const [coordinatorId, setCoordinatorId] = useState(settings.coordinator_agent_id ?? "");
  const [budget, setBudget] = useState(String(settings.openrouter_monthly_budget_usd));
  const [participantTokens, setParticipantTokens] = useState(String(settings.participant_max_tokens));
  const [coordinatorTokens, setCoordinatorTokens] = useState(String(settings.coordinator_max_tokens));
  const [errors, setErrors] = useState<SettingsErrors>({});
  const [submitting, setSubmitting] = useState(false);
  const [serverError, setServerError] = useState<ApiError | null>(null);

  const briefRef = useRef<HTMLTextAreaElement>(null);
  const budgetRef = useRef<HTMLInputElement>(null);
  const partRef = useRef<HTMLInputElement>(null);
  const coordRef = useRef<HTMLInputElement>(null);

  // A coordinator that is now archived is shown as "not set" so the user picks a valid one.
  const coordinatorValid = active.some((a) => a.id === coordinatorId);
  const spent = settings.openrouter_spent_usd;
  const limit = settings.openrouter_monthly_budget_usd;

  async function submit(e: FormEvent) {
    e.preventDefault();
    setServerError(null);
    const found = validateSettings({ budget, participantTokens, coordinatorTokens });
    setErrors(found);
    if (found.budget) return budgetRef.current?.focus();
    if (found.participant) return partRef.current?.focus();
    if (found.coordinator) return coordRef.current?.focus();

    const patch: SettingsUpdate = {};
    if (brief !== settings.project_brief) patch.project_brief = brief;
    const nextCoordinator = coordinatorValid ? coordinatorId : null;
    if (nextCoordinator !== settings.coordinator_agent_id)
      patch.coordinator_agent_id = nextCoordinator;
    if (Number(budget) !== settings.openrouter_monthly_budget_usd)
      patch.openrouter_monthly_budget_usd = Number(budget);
    if (Number(participantTokens) !== settings.participant_max_tokens)
      patch.participant_max_tokens = Number(participantTokens);
    if (Number(coordinatorTokens) !== settings.coordinator_max_tokens)
      patch.coordinator_max_tokens = Number(coordinatorTokens);

    if (Object.keys(patch).length === 0) return onClose();
    setSubmitting(true);
    try {
      const saved = await api.updateSettings(patch);
      setSettings(saved);
      void refreshSettings();
      toasts.info("Settings saved");
      onClose();
    } catch (err) {
      setServerError(err instanceof ApiError ? err : new ApiError(0, "unknown", String(err)));
    } finally {
      setSubmitting(false);
    }
  }

  return (
    <Dialog
      open
      onOpenChange={(o) => !o && onClose()}
      title="Settings"
      description="Shared by every agent and discussion."
      size="max-w-xl"
      initialFocus={briefRef}
      footer={
        <>
          <Button variant="ghost" onClick={onClose}>
            Cancel
          </Button>
          <Button variant="primary" type="submit" form="settings-form" disabled={submitting}>
            {submitting ? "Saving…" : "Save settings"}
          </Button>
        </>
      }
    >
      <form id="settings-form" onSubmit={submit} noValidate className="space-y-4">
        {serverError ? (
          <div role="alert" className="rounded-lg bg-danger-soft px-3 py-2 text-sm text-danger">
            <strong className="font-semibold">{describeError(serverError).title}.</strong>{" "}
            {describeError(serverError).message}
          </div>
        ) : null}

        <Field
          label="Project brief"
          hint="Given to every agent: the specification and a short description of the project. Private chats stay separate."
        >
          {(p) => (
            <textarea
              {...p}
              ref={briefRef}
              rows={6}
              value={brief}
              onChange={(e) => setBrief(e.target.value)}
              className={`${inputClass} resize-y`}
            />
          )}
        </Field>

        <Field
          label="Coordinator agent"
          hint="Sets the agenda and writes the summary in group discussions."
        >
          {(p) => (
            <select
              {...p}
              value={coordinatorValid ? coordinatorId : ""}
              onChange={(e) => setCoordinatorId(e.target.value)}
              className={inputClass}
            >
              <option value="">Not set</option>
              {active.map((a) => (
                <option key={a.id} value={a.id}>
                  {a.name}
                </option>
              ))}
            </select>
          )}
        </Field>

        <div className="grid gap-4 sm:grid-cols-2">
          <Field label="OpenRouter monthly budget (USD)" error={errors.budget}>
            {(p) => (
              <input
                {...p}
                ref={budgetRef}
                type="number"
                inputMode="decimal"
                min={0}
                step="0.5"
                value={budget}
                onChange={(e) => setBudget(e.target.value)}
                className={inputClass}
              />
            )}
          </Field>
          <div className="space-y-1.5">
            <div className="text-sm font-medium">Spent this month</div>
            <p
              className="rounded-lg border border-line bg-sunken px-3 py-2 text-sm"
              aria-label="Spent this month"
            >
              ${spent.toFixed(2)} <span className="text-muted">of ${limit.toFixed(2)}</span>
            </p>
            <p className="text-xs text-muted">Read only. Subscription usage is not counted.</p>
          </div>
        </div>

        <div className="grid gap-4 sm:grid-cols-2">
          <Field label="Participant max tokens" error={errors.participant}>
            {(p) => (
              <input
                {...p}
                ref={partRef}
                type="number"
                inputMode="numeric"
                min={1}
                step={1}
                value={participantTokens}
                onChange={(e) => setParticipantTokens(e.target.value)}
                className={inputClass}
              />
            )}
          </Field>
          <Field label="Coordinator max tokens" error={errors.coordinator}>
            {(p) => (
              <input
                {...p}
                ref={coordRef}
                type="number"
                inputMode="numeric"
                min={1}
                step={1}
                value={coordinatorTokens}
                onChange={(e) => setCoordinatorTokens(e.target.value)}
                className={inputClass}
              />
            )}
          </Field>
        </div>
      </form>
    </Dialog>
  );
}
