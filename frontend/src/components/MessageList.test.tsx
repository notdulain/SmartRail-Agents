import { render, screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it, vi } from "vitest";
import { makeMessage, makeUserMessage } from "../test/fixtures";
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
