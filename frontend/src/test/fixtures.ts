import type {
  Agent,
  Conversation,
  Message,
  ProvidersResponse,
  Run,
  Settings,
} from "../api/types";

const NOW = "2026-10-06T10:00:00Z";

export function makeAgent(over: Partial<Agent> = {}): Agent {
  return {
    id: "agt_1",
    name: "Passenger",
    persona: "You are a passenger.",
    provider_id: "openai",
    model_id: "gpt-6-mini",
    working_directory: null,
    tool_access: "none",
    revision: 1,
    archived: false,
    created_at: NOW,
    updated_at: NOW,
    ...over,
  };
}

export function makeConversation(over: Partial<Conversation> = {}): Conversation {
  return {
    id: "cnv_1",
    type: "direct",
    title: "Chat with Passenger",
    topic: null,
    participant_ids: ["agt_1"],
    created_at: NOW,
    updated_at: NOW,
    ...over,
  };
}

export function makeMessage(over: Partial<Message> = {}): Message {
  return {
    id: "msg_1",
    conversation_id: "cnv_1",
    run_id: "run_1",
    role: "agent",
    speaker_name: "Passenger",
    agent: {
      agent_id: "agt_1",
      name: "Passenger",
      persona: "You are a passenger.",
      provider_id: "openai",
      model_id: "gpt-6-mini",
      revision: 1,
      working_directory: null,
      tool_access: "none",
    },
    provider_id: "openai",
    model_id: "gpt-6-mini",
    content: "Hello",
    status: "complete",
    error: null,
    error_code: null,
    reply_to_id: null,
    attachments: [],
    images: [],
    tool_calls: [],
    stage: null,
    created_at: NOW,
    ...over,
  };
}

export function makeUserMessage(over: Partial<Message> = {}): Message {
  return makeMessage({
    id: "msg_user",
    run_id: null,
    role: "user",
    speaker_name: "You",
    agent: null,
    provider_id: null,
    model_id: null,
    content: "Hi there",
    ...over,
  });
}

export function makeRun(over: Partial<Run> = {}): Run {
  return {
    id: "run_1",
    conversation_id: "cnv_1",
    status: "running",
    participants: [],
    coordinator: null,
    usage: { input_tokens: 0, output_tokens: 0, cost_usd: null },
    error: null,
    error_code: null,
    created_at: NOW,
    finished_at: null,
    ...over,
  };
}

export function makeSettings(over: Partial<Settings> = {}): Settings {
  return {
    project_brief: "",
    coordinator_agent_id: null,
    openrouter_monthly_budget_usd: 5,
    openrouter_spent_usd: 0.42,
    participant_max_tokens: 1024,
    coordinator_max_tokens: 2048,
    ...over,
  };
}

export function makeProviders(): ProvidersResponse {
  return {
    refreshed_at: NOW,
    providers: [
      {
        id: "anthropic",
        name: "Anthropic",
        connected: false,
        models: [{ id: "claude-sonnet-5-5", name: "Claude Sonnet 5.5" }],
      },
      {
        id: "openai",
        name: "OpenAI",
        connected: true,
        models: [
          { id: "gpt-6-sol", name: "GPT-6 Sol" },
          { id: "gpt-6-mini", name: "GPT-6 Mini" },
        ],
      },
      {
        id: "openrouter",
        name: "OpenRouter",
        connected: true,
        models: [
          {
            id: "deepseek/deepseek-chat",
            name: "DeepSeek Chat",
            price: { input_per_mtok: 0.3, output_per_mtok: 1 },
          },
        ],
      },
    ],
  };
}
