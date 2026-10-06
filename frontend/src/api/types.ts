import type { components } from "./schema";

type S = components["schemas"];

export type Agent = S["Agent"];
export type AgentCreate = S["AgentCreate"];
export type AgentUpdate = S["AgentUpdate"];
export type AgentSnapshot = S["AgentSnapshot"];
export type Conversation = S["Conversation"];
export type ConversationCreate = S["ConversationCreate"];
export type ConversationDetail = S["ConversationDetail"];
export type ConversationType = S["ConversationType"];
export type ErrorCode = S["ErrorCode"];
export type Message = S["Message"];
export type MessageStatus = S["MessageStatus"];
export type ModelInfo = S["ModelInfo"];
export type ProviderInfo = S["ProviderInfo"];
export type ProvidersResponse = S["ProvidersResponse"];
export type Run = S["Run"];
export type SendMessageResponse = S["SendMessageResponse"];
export type Settings = S["Settings"];
export type SettingsUpdate = S["SettingsUpdate"];

export type RunEvent = S["RunEvent"];

export type RunEventType = RunEvent["type"];

export const TERMINAL_EVENT_TYPES: ReadonlySet<string> = new Set([
  "run.completed",
  "run.failed",
  "run.cancelled",
  "run.paused",
]);
