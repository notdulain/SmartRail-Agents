import { render, screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it, vi } from "vitest";
import { makeMessage, makeUserMessage } from "../test/fixtures";
import type { ToolCall } from "../api/types";
import { MessageList } from "./MessageList";

describe("MessageList", () => {
  it("labels each message with the speaker and model stored on the message, not the current agent", () => {
    // The agent was later renamed / switched model; history keeps the original labels.
    render(
      <MessageList
        messages={[
          makeMessage({
            id: "a",
            speaker_name: "Old Name",
            provider_id: "openrouter",
            model_id: "deepseek/deepseek-chat",
            content: "From the past",
            agent: {
              agent_id: "agt_1",
              name: "Old Name",
              persona: "",
              provider_id: "openrouter",
              model_id: "deepseek/deepseek-chat",
              revision: 1,
              working_directory: null,
              tool_access: "none",
            },
          }),
        ]}
      />,
    );
    const msg = screen.getByRole("article", { name: "Old Name message" });
    expect(within(msg).getByText("Old Name")).toBeInTheDocument();
    expect(within(msg).getByText("openrouter / deepseek/deepseek-chat")).toBeInTheDocument();
    expect(within(msg).getByText("From the past")).toBeInTheDocument();
  });

  it("renders user and agent messages and updates text as deltas arrive", () => {
    const streaming = makeMessage({ id: "s", status: "streaming", content: "Hel" });
    const { rerender } = render(<MessageList messages={[makeUserMessage(), streaming]} />);
    expect(screen.getByText("Hi there")).toBeInTheDocument();
    expect(screen.getByText("Hel")).toBeInTheDocument();
    expect(screen.getByRole("status")).toHaveTextContent("Passenger is replying");

    rerender(<MessageList messages={[makeUserMessage(), { ...streaming, content: "Hello wor" }]} />);
    expect(screen.getByText("Hello wor")).toBeInTheDocument();

    rerender(
      <MessageList
        messages={[makeUserMessage(), { ...streaming, content: "Hello world", status: "complete" }]}
      />,
    );
    expect(screen.getByText("Hello world")).toBeInTheDocument();
    expect(screen.getByRole("status")).toBeEmptyDOMElement();
  });

  it("shows a waiting indicator before the first delta", () => {
    render(<MessageList messages={[makeMessage({ status: "streaming", content: "" })]} />);
    expect(screen.getByLabelText("Waiting for the first words")).toBeInTheDocument();
  });

  it("shows stage labels once per stage and reply targets for group discussions", () => {
    render(
      <MessageList
        messages={[
          makeUserMessage(),
          makeMessage({ id: "c0", speaker_name: "Coordinator", stage: 0, content: "Agenda text" }),
          makeMessage({ id: "p1", speaker_name: "Driver", stage: 1, content: "Driver view" }),
          makeMessage({ id: "p2", speaker_name: "Passenger", stage: 1, content: "Passenger view" }),
          makeMessage({
            id: "r1",
            speaker_name: "Passenger",
            stage: 2,
            reply_to_id: "p1",
            content: "I agree",
          }),
          makeMessage({ id: "c3", speaker_name: "Coordinator", stage: 3, content: "Summary text" }),
        ]}
      />,
    );
    const labels = screen.getAllByRole("separator").map((s) => s.getAttribute("aria-label"));
    expect(labels).toEqual(["Agenda", "Round 1", "Replies", "Summary"]);
    expect(screen.getByText("Replying to Driver")).toBeInTheDocument();
  });

  it("marks cancelled and failed messages with chips", () => {
    render(
      <MessageList
        messages={[
          makeMessage({ id: "c", speaker_name: "Cancelled One", status: "cancelled", content: "part" }),
          makeMessage({
            id: "f",
            speaker_name: "Failed One",
            status: "failed",
            content: "",
            error: "Provider said no",
            error_code: "runtime_error",
          }),
        ]}
      />,
    );
    expect(screen.getByText("Stopped")).toBeInTheDocument();
    expect(screen.getByText("Failed")).toBeInTheDocument();
    expect(screen.getByRole("alert")).toHaveTextContent("Provider said no");
    expect(screen.queryByRole("button", { name: "Edit agent" })).not.toBeInTheDocument();
  });

  it.each(["model_unavailable", "provider_unavailable"] as const)(
    "offers Edit agent for a %s failure, using the agent stored on the message",
    async (code) => {
      const onEditAgent = vi.fn();
      render(
        <MessageList
          onEditAgent={onEditAgent}
          messages={[
            makeMessage({
              status: "failed",
              content: "",
              error: "gpt-6-mini is not offered",
              error_code: code,
            }),
          ]}
        />,
      );
      await userEvent.click(screen.getByRole("button", { name: "Edit agent" }));
      expect(onEditAgent).toHaveBeenCalledWith("agt_1");
    },
  );

  it("does not offer Edit agent for rate limits or on messages without an agent", () => {
    render(
      <MessageList
        messages={[
          makeMessage({ id: "1", status: "failed", error: "slow down", error_code: "rate_limited" }),
          makeMessage({
            id: "2",
            status: "failed",
            error: "gone",
            error_code: "model_unavailable",
            agent: null,
          }),
        ]}
      />,
    );
    expect(screen.queryByRole("button", { name: "Edit agent" })).not.toBeInTheDocument();
  });
});

describe("MessageList attachments and tool calls", () => {
  const call = (over: Partial<ToolCall>): ToolCall => ({
    id: "t1",
    tool: "read",
    title: "docs/README.md",
    status: "completed",
    error: null,
    ...over,
  });

  it("shows files attached to user messages", () => {
    render(
      <MessageList
        messages={[
          makeUserMessage({
            attachments: [
              { agent_id: "agt_1", path: "src/main.ts" },
              { agent_id: "agt_2", path: "notes.md" },
            ],
          }),
        ]}
      />,
    );
    const list = screen.getByRole("list", { name: "Attached files" });
    const items = within(list).getAllByRole("listitem");
    expect(items).toHaveLength(2);
    expect(items[0]).toHaveTextContent("src/main.ts");
    expect(items[1]).toHaveTextContent("notes.md");
    expect(within(list).queryByRole("button")).not.toBeInTheDocument();
  });

  it("labels attachments with the agent name in group chats", () => {
    render(
      <MessageList
        attachmentAgentName={(id) => ({ agt_1: "Driver", agt_2: "Guard" })[id]}
        messages={[
          makeUserMessage({
            attachments: [
              { agent_id: "agt_1", path: "a.md" },
              { agent_id: "agt_2", path: "b.md" },
            ],
          }),
        ]}
      />,
    );
    const items = within(screen.getByRole("list", { name: "Attached files" })).getAllByRole("listitem");
    expect(items[0]).toHaveTextContent("Drivera.md");
    expect(items[1]).toHaveTextContent("Guardb.md");
  });

  it("collapses finished tool calls into a summary that expands to the list", async () => {
    render(
      <MessageList
        messages={[
          makeMessage({
            tool_calls: [
              call({ id: "t1" }),
              call({ id: "t2", tool: "grep", title: "TODO" }),
              call({ id: "t3", tool: "edit", title: "src/a.ts", status: "error", error: "File is read-only" }),
            ],
          }),
        ]}
      />,
    );
    const toggle = screen.getByRole("button", { name: /Used 3 tools\s*· 1 failed/ });
    expect(toggle).toHaveAttribute("aria-expanded", "false");
    expect(screen.queryByRole("list", { name: "Tool calls" })).not.toBeInTheDocument();

    await userEvent.click(toggle);
    expect(toggle).toHaveAttribute("aria-expanded", "true");
    const items = within(screen.getByRole("list", { name: "Tool calls" })).getAllByRole("listitem");
    expect(items.map((i) => i.textContent)).toEqual([
      "Done:read·docs/README.md",
      "Done:grep·TODO",
      "Failed:edit·src/a.tsFile is read-only",
    ]);

    await userEvent.click(toggle);
    expect(screen.queryByRole("list", { name: "Tool calls" })).not.toBeInTheDocument();
  });

  it("shows the running tool live while the message streams", async () => {
    const streaming = makeMessage({
      status: "streaming",
      content: "",
      tool_calls: [call({ id: "t1" }), call({ id: "t2", tool: "grep", title: "delay", status: "running" })],
    });
    const { rerender } = render(<MessageList messages={[streaming]} />);
    const toggle = screen.getByRole("button", { name: /Searching\s+delay\s*\(2 tools\)/ });
    await userEvent.click(toggle);
    expect(screen.getByText("Running:")).toBeInTheDocument();

    rerender(
      <MessageList
        messages={[
          {
            ...streaming,
            status: "complete",
            content: "Found it",
            tool_calls: [call({ id: "t1" }), call({ id: "t2", tool: "grep", title: "delay" })],
          },
        ]}
      />,
    );
    expect(screen.getByRole("button", { name: /Used 2 tools/ })).toBeInTheDocument();
    expect(screen.queryByText("Running:")).not.toBeInTheDocument();
  });

  it("marks calls left running in a stopped message as unfinished", async () => {
    render(
      <MessageList
        messages={[
          makeMessage({ status: "cancelled", tool_calls: [call({ status: "running" })] }),
        ]}
      />,
    );
    await userEvent.click(screen.getByRole("button", { name: /Used 1 tool$/ }));
    expect(screen.getByText("Did not finish:")).toBeInTheDocument();
  });

  it("renders nothing extra for messages without tool calls", () => {
    render(<MessageList messages={[makeMessage({ tool_calls: [] })]} />);
    expect(screen.queryByRole("button", { name: /Used/ })).not.toBeInTheDocument();
  });
});
