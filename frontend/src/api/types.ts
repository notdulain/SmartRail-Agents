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
export type DirectoryEntry = S["DirectoryEntry"];
export type DirectoryListing = S["DirectoryListing"];
export type ErrorCode = S["ErrorCode"];
export type FileEntry = S["FileEntry"];
export type FileRef = S["FileRef"];
export type FileSearchResponse = S["FileSearchResponse"];
export type Message = S["Message"];
export type MessageStatus = S["MessageStatus"];
export type ModelInfo = S["ModelInfo"];
export type ProviderInfo = S["ProviderInfo"];
export type ProvidersResponse = S["ProvidersResponse"];
export type Run = S["Run"];
export type SendMessageResponse = S["SendMessageResponse"];
export type Settings = S["Settings"];
export type SettingsUpdate = S["SettingsUpdate"];
export type ToolAccess = S["ToolAccess"];
export type ToolCall = S["ToolCall"];
export type ToolCallStatus = S["ToolCallStatus"];

/** Contract limit on files attached to one message. */
export const MAX_ATTACHMENTS = 20;

export type RunEvent = S["RunEvent"];

export type RunEventType = RunEvent["type"];

export const TERMINAL_EVENT_TYPES: ReadonlySet<string> = new Set([
  "run.completed",
  "run.failed",
  "run.cancelled",
  "run.paused",
]);
