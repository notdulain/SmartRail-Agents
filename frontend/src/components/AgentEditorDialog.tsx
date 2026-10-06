import { useMemo, useRef, useState, type FormEvent } from "react";
import { api, ApiError } from "../api/client";
import type { Agent, AgentUpdate } from "../api/types";
import { describeError } from "../lib/errors";
import { choiceStatus, type ModelChoice } from "../lib/models";
import { AGENT_TEMPLATES, findTemplate } from "../lib/templates";
import { useAppData } from "../state/appData";
import { useToasts } from "../state/toast";
import { ModelPicker } from "./ModelPicker";
import { Button } from "./ui/Button";
import { Dialog } from "./ui/Dialog";
import { Field, inputClass } from "./ui/Field";

export const NAME_MAX = 80;
export const PERSONA_MAX = 8000;

export interface AgentFormValues {
  name: string;
  persona: string;
  choice: ModelChoice | null;
}

export interface AgentFormErrors {
  name?: string;
  persona?: string;
  model?: string;
}

/** Pure validation, shared by the dialog and its tests. `original` allows saving an unchanged model. */
export function validateAgentForm(
  values: AgentFormValues,
  catalog: Parameters<typeof choiceStatus>[0],
  original?: ModelChoice | null,
): AgentFormErrors {
  const errors: AgentFormErrors = {};
  const name = values.name.trim();
  if (!name) errors.name = "Enter a name.";
  else if (name.length > NAME_MAX) errors.name = `Name is too long (max ${NAME_MAX} characters).`;
  if (!values.persona.trim()) errors.persona = "Describe how this agent should behave.";
  else if (values.persona.length > PERSONA_MAX)
    errors.persona = `Persona is too long (max ${PERSONA_MAX} characters).`;
  if (!values.choice) errors.model = "Choose a provider and model.";
  else {
    const unchanged =
      original &&
      original.providerId === values.choice.providerId &&
      original.modelId === values.choice.modelId;
    const status = choiceStatus(catalog, values.choice);
    if (!unchanged && status !== "ok") {
      errors.model =
        status === "disconnected"
          ? "That provider is not connected. Choose a model from a connected provider."
          : "Choose a model from the list.";
    }
  }
  return errors;
}

interface Props {
  agent: Agent | null;
  onClose(): void;
}

export function AgentEditorDialog({ agent, onClose }: Props) {
  const { providers, providersLoading, loadProviders, refreshAgents } = useAppData();
  const toasts = useToasts();
  const editing = agent !== null;
  const original = useMemo<ModelChoice | null>(
    () => (agent ? { providerId: agent.provider_id, modelId: agent.model_id } : null),
    [agent],
  );

  const [name, setName] = useState(agent?.name ?? "");
  const [persona, setPersona] = useState(agent?.persona ?? "");
  const [choice, setChoice] = useState<ModelChoice | null>(original);
  const [template, setTemplate] = useState("");
  const [errors, setErrors] = useState<AgentFormErrors>({});
  const [submitting, setSubmitting] = useState(false);
  const [serverError, setServerError] = useState<ApiError | null>(null);
  const [confirmArchive, setConfirmArchive] = useState(false);

  const nameRef = useRef<HTMLInputElement>(null);
  const personaRef = useRef<HTMLTextAreaElement>(null);
  const modelRef = useRef<HTMLDivElement>(null);

  function applyTemplate(id: string) {
    setTemplate(id);
    const t = findTemplate(id);
    if (!t) return;
    setName(t.name);
    setPersona(t.persona);
    setErrors((e) => ({ ...e, name: undefined, persona: undefined }));
  }

  async function submit(e: FormEvent) {
    e.preventDefault();
    setServerError(null);
    const found = validateAgentForm({ name, persona, choice }, providers, original);
    setErrors(found);
    if (found.name) return nameRef.current?.focus();
    if (found.persona) return personaRef.current?.focus();
    if (found.model) return modelRef.current?.querySelector<HTMLElement>("input")?.focus();
    if (!choice) return;

    setSubmitting(true);
    try {
      if (agent) {
        const patch: AgentUpdate = {};
        if (name.trim() !== agent.name) patch.name = name.trim();
        if (persona !== agent.persona) patch.persona = persona;
        if (choice.providerId !== agent.provider_id) patch.provider_id = choice.providerId;
        if (choice.modelId !== agent.model_id) patch.model_id = choice.modelId;
        if (Object.keys(patch).length > 0) await api.updateAgent(agent.id, patch);
      } else {
        await api.createAgent({
          name: name.trim(),
          persona,
          provider_id: choice.providerId,
          model_id: choice.modelId,
        });
      }
      await refreshAgents();
      toasts.info(
        editing ? "Agent updated" : "Agent created",
        editing ? "Changes apply to the next run." : undefined,
      );
      onClose();
    } catch (err) {
      setServerError(err instanceof ApiError ? err : new ApiError(0, "unknown", String(err)));
    } finally {
      setSubmitting(false);
    }
  }

  async function setArchived(archived: boolean) {
    if (!agent) return;
    setSubmitting(true);
    setServerError(null);
    try {
      await api.updateAgent(agent.id, { archived });
      await refreshAgents();
      toasts.info(archived ? "Agent archived" : "Agent restored");
      onClose();
    } catch (err) {
      setServerError(err instanceof ApiError ? err : new ApiError(0, "unknown", String(err)));
    } finally {
      setSubmitting(false);
      setConfirmArchive(false);
    }
  }

  const description = editing ? (
    <>
      Revision {agent.revision}. Edits apply to the next run; a discussion already running keeps its
      starting setup.
    </>
  ) : (
    "Pick a role, a persona and the model that plays it."
  );

  return (
    <Dialog
      open
      onOpenChange={(o) => !o && onClose()}
      title={editing ? "Edit agent" : "New agent"}
      description={description}
      size="max-w-2xl"
      initialFocus={nameRef}
      footer={
        <>
          {editing ? (
            <div className="mr-auto flex items-center gap-2">
              {agent.archived ? (
                <Button onClick={() => setArchived(false)} disabled={submitting}>
                  Restore agent
                </Button>
              ) : confirmArchive ? (
                <>
                  <Button variant="danger" onClick={() => setArchived(true)} disabled={submitting}>
                    Confirm archive
                  </Button>
                  <Button variant="ghost" onClick={() => setConfirmArchive(false)}>
                    Keep
                  </Button>
                </>
              ) : (
                <Button variant="danger" onClick={() => setConfirmArchive(true)}>
                  Archive
                </Button>
              )}
            </div>
          ) : null}
          <Button variant="ghost" onClick={onClose}>
            Cancel
          </Button>
          <Button variant="primary" type="submit" form="agent-form" disabled={submitting}>
            {submitting ? "Saving…" : editing ? "Save changes" : "Create agent"}
          </Button>
        </>
      }
    >
      <form id="agent-form" onSubmit={submit} noValidate className="space-y-4">
        {serverError ? (
          <div role="alert" className="rounded-lg bg-danger-soft px-3 py-2 text-sm text-danger">
            <strong className="font-semibold">{describeError(serverError).title}.</strong>{" "}
            {describeError(serverError).message}
          </div>
        ) : null}
        {editing && agent.archived ? (
          <p className="rounded-lg bg-warn-soft px-3 py-2 text-sm text-warn">
            This agent is archived. It no longer appears in new discussions; its transcripts stay
            readable.
          </p>
        ) : null}

        <Field label="Template (optional)" hint="Fills the name and persona. You can edit both afterwards.">
          {(p) => (
            <select
              {...p}
              value={template}
              onChange={(e) => applyTemplate(e.target.value)}
              className={inputClass}
            >
              <option value="">No template</option>
              {AGENT_TEMPLATES.map((t) => (
                <option key={t.id} value={t.id}>
                  {t.name}
                </option>
              ))}
            </select>
          )}
        </Field>

        <Field label="Name" error={errors.name}>
          {(p) => (
            <input
              {...p}
              ref={nameRef}
              value={name}
              onChange={(e) => setName(e.target.value)}
              maxLength={NAME_MAX}
              autoComplete="off"
              className={inputClass}
            />
          )}
        </Field>

        <Field
          label="Persona instructions"
          error={errors.persona}
          hint="How this agent should think, speak and what it cares about."
        >
          {(p) => (
            <textarea
              {...p}
              ref={personaRef}
              value={persona}
              onChange={(e) => setPersona(e.target.value)}
              rows={6}
              className={`${inputClass} resize-y`}
            />
          )}
        </Field>

        <div className="space-y-1.5" ref={modelRef}>
          <div className="text-sm font-medium">
            Provider and model
          </div>
          {errors.model ? (
            <p id="model-error" className="text-xs font-medium text-danger">
              {errors.model}
            </p>
          ) : null}
          <ModelPicker
            providers={providers}
            loading={providersLoading}
            value={choice}
            onChange={(c) => {
              setChoice(c);
              setErrors((e) => ({ ...e, model: undefined }));
            }}
            onRefresh={() => void loadProviders(true)}
            invalid={!!errors.model}
            describedBy={errors.model ? "model-error" : undefined}
          />
        </div>
      </form>
    </Dialog>
  );
}
