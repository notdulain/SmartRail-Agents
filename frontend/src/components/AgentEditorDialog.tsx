import { useEffect, useId, useMemo, useRef, useState, type FormEvent } from "react";
import { api, ApiError } from "../api/client";
import type { Agent, AgentCreate, AgentUpdate, ToolAccess } from "../api/types";
import { describeError } from "../lib/errors";
import { choiceStatus, type ModelChoice } from "../lib/models";
import { AGENT_TEMPLATES, findTemplate } from "../lib/templates";
import { useAppData } from "../state/appData";
import { useToasts } from "../state/toast";
import { FolderPickerDialog } from "./FolderPickerDialog";
import { ModelPicker } from "./ModelPicker";
import { Button } from "./ui/Button";
import { Dialog } from "./ui/Dialog";
import { Field, inputClass } from "./ui/Field";
import { AlertIcon, CloseIcon, FolderIcon } from "./ui/icons";

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

export const TOOL_ACCESS_OPTIONS: ReadonlyArray<{
  value: ToolAccess;
  label: string;
  hint: string;
}> = [
  { value: "none", label: "No file access", hint: "Chat only. The agent cannot see any files." },
  { value: "read_only", label: "Read only", hint: "Can list, search and read files in the folder." },
  {
    value: "read_write",
    label: "Read & write",
    hint: "Can also create and edit files in the folder.",
  },
];

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
  const [directory, setDirectory] = useState(agent?.working_directory ?? "");
  const [access, setAccess] = useState<ToolAccess>(agent?.tool_access ?? "none");
  const [pickerOpen, setPickerOpen] = useState(false);
  const hasDirectory = directory.trim().length > 0;

  const nameRef = useRef<HTMLInputElement>(null);
  const personaRef = useRef<HTMLTextAreaElement>(null);
  const modelRef = useRef<HTMLDivElement>(null);
  const alertRef = useRef<HTMLDivElement>(null);
  const accessName = useId();

  useEffect(() => {
    if (serverError) alertRef.current?.scrollIntoView({ block: "nearest" });
  }, [serverError]);

  /** Choosing a folder where there was none starts at Read only; clearing it turns tools off. */
  function changeDirectory(next: string) {
    const had = directory.trim().length > 0;
    const has = next.trim().length > 0;
    setDirectory(next);
    if (!has) setAccess("none");
    else if (!had && access === "none") setAccess("read_only");
  }

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

    const dir = directory.trim();
    const toolAccess: ToolAccess = dir ? access : "none";
    setSubmitting(true);
    try {
      if (agent) {
        const patch: AgentUpdate = {};
        if (name.trim() !== agent.name) patch.name = name.trim();
        if (persona !== agent.persona) patch.persona = persona;
        if (choice.providerId !== agent.provider_id) patch.provider_id = choice.providerId;
        if (choice.modelId !== agent.model_id) patch.model_id = choice.modelId;
        // "" clears the folder; the server wants tool_access "none" with it, which clearing
        // the folder guarantees.
        if (dir !== (agent.working_directory ?? "")) patch.working_directory = dir;
        if (toolAccess !== agent.tool_access) patch.tool_access = toolAccess;
        if (Object.keys(patch).length > 0) await api.updateAgent(agent.id, patch);
      } else {
        const body: AgentCreate = {
          name: name.trim(),
          persona,
          provider_id: choice.providerId,
          model_id: choice.modelId,
          tool_access: toolAccess,
        };
        if (dir) body.working_directory = dir;
        await api.createAgent(body);
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
          <div
            ref={alertRef}
            role="alert"
            className="rounded-lg bg-danger-soft px-3 py-2 text-sm text-danger"
          >
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
              rows={5}
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

        <Field
          label="Working directory (optional)"
          hint="A folder on this computer for the agent. You can attach its files to messages with @."
        >
          {(p) => (
            <div className="flex flex-wrap gap-2">
              <div className="relative min-w-48 flex-1">
                <FolderIcon
                  width={16}
                  height={16}
                  className="pointer-events-none absolute left-2.5 top-1/2 -translate-y-1/2 text-muted"
                />
                <input
                  {...p}
                  value={directory}
                  onChange={(e) => changeDirectory(e.target.value)}
                  placeholder="No folder"
                  autoComplete="off"
                  spellCheck={false}
                  className={`${inputClass} pl-8 font-mono text-[0.8rem]`}
                />
              </div>
              <Button onClick={() => setPickerOpen(true)}>Browse…</Button>
              {hasDirectory ? (
                <Button variant="ghost" onClick={() => changeDirectory("")}>
                  <CloseIcon width={16} height={16} />
                  Clear
                </Button>
              ) : null}
            </div>
          )}
        </Field>

        <fieldset disabled={!hasDirectory} aria-describedby={`${accessName}-hint`} className="space-y-1.5">
          <legend className="mb-1.5 text-sm font-medium">File access</legend>
          <div className="grid gap-2 sm:grid-cols-3">
            {TOOL_ACCESS_OPTIONS.map((o) => {
              const checked = (hasDirectory ? access : "none") === o.value;
              return (
                <label
                  key={o.value}
                  className={`flex gap-2 rounded-lg border px-3 py-2 text-sm has-focus-visible:outline-2 has-focus-visible:outline-accent ${
                    checked ? "border-accent bg-accent-soft" : "border-line-strong bg-surface"
                  } ${hasDirectory ? "cursor-pointer hover:border-accent" : "cursor-not-allowed opacity-60"}`}
                >
                  <input
                    type="radio"
                    name={accessName}
                    value={o.value}
                    checked={checked}
                    onChange={() => setAccess(o.value)}
                    className="mt-1 shrink-0 accent-accent"
                  />
                  <span>
                    <span className="block font-medium">{o.label}</span>
                    <span className="block text-xs text-muted">{o.hint}</span>
                  </span>
                </label>
              );
            })}
          </div>
          <p id={`${accessName}-hint`} className="text-xs text-muted">
            {hasDirectory
              ? "Tools stay inside the working directory. Shell, web and other tools are never available."
              : "Choose a working directory to enable file tools."}
          </p>
          {hasDirectory && access === "read_write" ? (
            <p className="flex items-start gap-2 rounded-lg bg-warn-soft px-3 py-2 text-xs text-warn">
              <AlertIcon width={16} height={16} className="shrink-0" />
              This agent can create and modify files in this folder.
            </p>
          ) : null}
        </fieldset>
      </form>
      <FolderPickerDialog
        open={pickerOpen}
        initialPath={directory.trim() || null}
        onClose={() => setPickerOpen(false)}
        onSelect={(path) => {
          changeDirectory(path);
          setPickerOpen(false);
        }}
      />
    </Dialog>
  );
}
