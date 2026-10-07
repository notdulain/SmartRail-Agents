import { fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it, vi } from "vitest";
import type { FileRef, ImageAttachment } from "../api/types";
import { fakeFileSearch } from "../test/fakeFiles";
import { errorResponse, mockFetch, type Handler } from "../test/mockFetch";
import { Composer, findMention, type MentionSource } from "./Composer";

const ONE: MentionSource[] = [{ agentId: "agt_1", agentName: "Driver" }];

function setup(
  sources: MentionSource[] = ONE,
  routes: Record<string, Handler> = { "GET /api/agents/agt_1/files": fakeFileSearch() },
  onSend = vi.fn(async (_text: string, _files: FileRef[], _images?: ImageAttachment[]) => true),
  conversationId?: string,
) {
  const api = mockFetch(routes);
  const view = render(
    <Composer
      conversationId={conversationId}
      placeholder="Message Driver"
      disabled={false}
      running={false}
      stopping={false}
      mentionSources={sources}
      onSend={onSend}
      onStop={vi.fn()}
    />,
  );
  return { api, onSend, ...view };
}

const input = () => screen.getByLabelText("Message");
const popover = () => screen.queryByRole("listbox", { name: "Files to attach" });
const fileCalls = (api: ReturnType<typeof mockFetch>) =>
  api.calls.filter((c) => c.path.endsWith("/files"));
const chips = () => screen.queryByRole("list", { name: "Attachments" });

describe("Composer drafts", () => {
  it("restores text only for its conversation and clears it after a successful send", async () => {
    const keyA = "smartrail:draft:cnv_draft_a";
    const keyB = "smartrail:draft:cnv_draft_b";
    localStorage.removeItem(keyA);
    localStorage.removeItem(keyB);
    const first = setup([], {}, undefined, "cnv_draft_a");
    await userEvent.type(input(), "A message in progress");
    expect(localStorage.getItem(keyA)).toBe("A message in progress");
    first.unmount();

    const second = setup([], {}, undefined, "cnv_draft_b");
    expect(input()).toHaveValue("");
    second.unmount();

    const restored = setup([], {}, undefined, "cnv_draft_a");
    expect(input()).toHaveValue("A message in progress");
    await userEvent.click(screen.getByRole("button", { name: "Send" }));
    await waitFor(() => expect(input()).toHaveValue(""));
    expect(localStorage.getItem(keyA)).toBeNull();
    restored.unmount();
  });

  it("keeps the draft after a failed send", async () => {
    const key = "smartrail:draft:cnv_draft_failed";
    localStorage.removeItem(key);
    const onSend = vi.fn(async () => false);
    const view = setup([], {}, onSend, "cnv_draft_failed");
    await userEvent.type(input(), "retry me{Enter}");
    await waitFor(() => expect(onSend).toHaveBeenCalled());
    expect(localStorage.getItem(key)).toBe("retry me");
    view.unmount();
    const restored = setup([], {}, undefined, "cnv_draft_failed");
    expect(input()).toHaveValue("retry me");
    restored.unmount();
    localStorage.removeItem(key);
  });

  it("saves text without persisting pasted image data", async () => {
    const key = "smartrail:draft:cnv_draft_image";
    localStorage.removeItem(key);
    const view = setup([], {}, undefined, "cnv_draft_image");
    await userEvent.type(input(), "Look at this");
    const file = new File(["image"], "shot.png", { type: "image/png" });
    fireEvent.paste(input(), {
      clipboardData: {
        items: [{ kind: "file", type: "image/png", getAsFile: () => file }],
        getData: () => "",
      },
    });
    await screen.findByRole("img", { name: "shot.png" });
    expect(localStorage.getItem(key)).toBe("Look at this");
    view.unmount();
    const restored = setup([], {}, undefined, "cnv_draft_image");
    expect(input()).toHaveValue("Look at this");
    expect(screen.queryByRole("img", { name: "shot.png" })).not.toBeInTheDocument();
    restored.unmount();
    localStorage.removeItem(key);
  });

  it("remains usable when localStorage throws", async () => {
    const read = vi.spyOn(Storage.prototype, "getItem").mockImplementation(() => { throw new Error("blocked"); });
    const write = vi.spyOn(Storage.prototype, "setItem").mockImplementation(() => { throw new Error("blocked"); });
    const remove = vi.spyOn(Storage.prototype, "removeItem").mockImplementation(() => { throw new Error("blocked"); });
    try {
      const { onSend } = setup([], {}, undefined, "cnv_draft_blocked");
      await userEvent.type(input(), "hello{Enter}");
      await waitFor(() => expect(onSend).toHaveBeenCalledWith("hello", [], []));
      await waitFor(() => expect(input()).toHaveValue(""));
    } finally {
      read.mockRestore();
      write.mockRestore();
      remove.mockRestore();
    }
  });
});

describe("findMention", () => {
  it("finds @tokens at the start or after whitespace only", () => {
    expect(findMention("@rea", 4, null)).toEqual({ start: 0, query: "rea" });
    expect(findMention("look at @src/ma", 15, null)).toEqual({ start: 8, query: "src/ma" });
    expect(findMention("mail me@example.com", 19, null)).toBeNull();
    expect(findMention("@done and more", 14, null)).toBeNull();
    // After picking a folder with a space in its name the mention continues across it.
    const anchor = { start: 0, prefix: "My Docs/" };
    expect(findMention("@My Docs/no", 11, anchor)).toEqual({ start: 0, query: "My Docs/no" });
    expect(findMention("@My Docs/no more", 16, anchor)).toBeNull();
  });
});

describe("Composer @ mentions", () => {
  it("opens on @ and searches the working directory once typing pauses", async () => {
    const { api } = setup();
    await userEvent.type(input(), "Check @md");
    const list = await screen.findByRole("listbox", { name: "Files to attach" });
    await waitFor(() => expect(within(list).getAllByRole("option")).toHaveLength(2));
    // Debounced: one request for the final query, none for "" or "m".
    expect(fileCalls(api)).toHaveLength(1);
    expect(fileCalls(api)[0].url.searchParams.get("q")).toBe("md");
    expect(within(list).getAllByRole("option").map((o) => o.textContent)).toEqual([
      "README.md1.2 KB",
      "docs/guide.md80 B",
    ]);
  });

  it("does not open inside a word (e-mail addresses)", async () => {
    const { api } = setup();
    await userEvent.type(input(), "mail me@example.com");
    await new Promise((r) => setTimeout(r, 250));
    expect(popover()).not.toBeInTheDocument();
    expect(fileCalls(api)).toHaveLength(0);
  });

  it("picks with the keyboard, removes the @token and adds a chip", async () => {
    const { onSend } = setup();
    await userEvent.type(input(), "See @md");
    await waitFor(() => expect(within(popover()!).getAllByRole("option")).toHaveLength(2));
    expect(input()).toHaveAttribute("aria-activedescendant");
    await userEvent.keyboard("{ArrowDown}");
    const options = within(popover()!).getAllByRole("option");
    expect(options[1]).toHaveAttribute("aria-selected", "true");
    await userEvent.keyboard("{Enter}");
    expect(onSend).not.toHaveBeenCalled();
    expect(popover()).not.toBeInTheDocument();
    expect(input()).toHaveValue("See ");
    expect(within(chips()!).getByText("guide.md")).toBeInTheDocument();
  });

  it("picks with Tab and with the mouse", async () => {
    setup();
    await userEvent.type(input(), "@read");
    await waitFor(() => expect(within(popover()!).getAllByRole("option")).toHaveLength(1));
    await userEvent.keyboard("{Tab}");
    expect(within(chips()!).getByText("README.md")).toBeInTheDocument();
    expect(input()).toHaveFocus();

    await userEvent.type(input(), "and @gui");
    await waitFor(() => expect(within(popover()!).getAllByRole("option")).toHaveLength(1));
    await userEvent.click(within(popover()!).getByRole("option", { name: /docs\/guide\.md/ }));
    expect(within(chips()!).getAllByRole("listitem")).toHaveLength(2);
    expect(input()).toHaveValue("and ");
  });

  it("closes on Escape and then Enter sends", async () => {
    const { onSend } = setup();
    await userEvent.type(input(), "hi @rea");
    await waitFor(() => expect(popover()).toBeInTheDocument());
    await userEvent.keyboard("{Escape}");
    expect(popover()).not.toBeInTheDocument();
    // Typing on in the same token keeps it closed.
    await userEvent.type(input(), "d");
    expect(popover()).not.toBeInTheDocument();
    await userEvent.keyboard("{Enter}");
    expect(onSend).toHaveBeenCalledWith("hi @read", [], []);
  });

  it("never sends on Enter while the popover is open", async () => {
    const { onSend } = setup();
    await userEvent.type(input(), "hi @zzzz");
    await waitFor(() => expect(within(popover()!).getByText("No matching files.")).toBeInTheDocument());
    await userEvent.keyboard("{Enter}");
    expect(onSend).not.toHaveBeenCalled();
    expect(input()).toHaveValue("hi @zzzz");
  });

  it("removes chips and does not attach the same file twice", async () => {
    setup();
    await userEvent.type(input(), "@read");
    await waitFor(() => expect(popover()).toBeInTheDocument());
    await waitFor(() => expect(within(popover()!).getAllByRole("option")).toHaveLength(1));
    await userEvent.keyboard("{Enter}");
    await userEvent.type(input(), "@read");
    await waitFor(() => expect(within(popover()!).getAllByRole("option")).toHaveLength(1));
    await userEvent.keyboard("{Enter}");
    expect(within(chips()!).getAllByRole("listitem")).toHaveLength(1);
    expect(screen.getByText("README.md is already attached.")).toBeInTheDocument();

    await userEvent.click(screen.getByRole("button", { name: "Remove README.md" }));
    expect(chips()).not.toBeInTheDocument();
  });

  it("opens a folder instead of attaching it and keeps searching inside it", async () => {
    const { api } = setup();
    await userEvent.type(input(), "@sr");
    await waitFor(() =>
      expect(within(popover()!).getByRole("option", { name: /^src\/folder$/ })).toBeInTheDocument(),
    );
    await userEvent.click(within(popover()!).getByRole("option", { name: /^src\/folder$/ }));
    expect(input()).toHaveValue("@src/");
    expect(chips()).not.toBeInTheDocument();
    await waitFor(() => expect(fileCalls(api).at(-1)!.url.searchParams.get("q")).toBe("src/"));
    await waitFor(() => expect(within(popover()!).getByRole("option", { name: /src\/app\.tsx/ })).toBeInTheDocument());
  });

  it("sends attachments and clears them after a successful send", async () => {
    const { onSend } = setup();
    await userEvent.type(input(), "@read");
    await waitFor(() => expect(within(popover()!).getAllByRole("option")).toHaveLength(1));
    await userEvent.keyboard("{Enter}");
    await userEvent.type(input(), "summarise this{Enter}");
    expect(onSend).toHaveBeenCalledWith("summarise this", [{ agent_id: "agt_1", path: "README.md" }], []);
    await waitFor(() => expect(chips()).not.toBeInTheDocument());
    expect(input()).toHaveValue("");
  });

  it("keeps attachments when the send fails", async () => {
    const onSend = vi.fn(async () => false);
    setup(ONE, { "GET /api/agents/agt_1/files": fakeFileSearch() }, onSend);
    await userEvent.type(input(), "@read");
    await waitFor(() => expect(within(popover()!).getAllByRole("option")).toHaveLength(1));
    await userEvent.keyboard("{Enter}");
    await userEvent.type(input(), "go{Enter}");
    await waitFor(() => expect(onSend).toHaveBeenCalled());
    expect(within(chips()!).getByText("README.md")).toBeInTheDocument();
    expect(input()).toHaveValue("go");
  });

  it("pastes an image, previews it, and sends it without text", async () => {
    const { onSend } = setup();
    const file = new File(["image"], "shot.png", { type: "image/png" });
    fireEvent.paste(input(), {
      clipboardData: {
        items: [{ kind: "file", type: "image/png", getAsFile: () => file }],
        getData: () => "",
      },
    });
    await waitFor(() => expect(screen.getByRole("img", { name: "shot.png" })).toBeInTheDocument());
    await userEvent.click(screen.getByRole("button", { name: "Send" }));
    await waitFor(() => expect(onSend).toHaveBeenCalled());
    const [text, files, images] = onSend.mock.calls[0];
    expect(text).toBe("");
    expect(files).toEqual([]);
    expect(images).toEqual([{ filename: "shot.png", data_url: expect.stringMatching(/^data:image\/png;base64,/) }]);
    await waitFor(() => expect(screen.queryByRole("img", { name: "shot.png" })).not.toBeInTheDocument());
  });

  it("groups results by agent in group chats", async () => {
    const { api } = setup(
      [
        { agentId: "agt_1", agentName: "Driver" },
        { agentId: "agt_2", agentName: "Guard" },
      ],
      {
        "GET /api/agents/agt_1/files": fakeFileSearch(),
        "GET /api/agents/agt_2/files": fakeFileSearch([
          { path: "rota.md", is_dir: false, size: 10 },
          { path: "readme.txt", is_dir: false, size: 10 },
        ]),
      },
    );
    await userEvent.type(input(), "@rea");
    await waitFor(() => expect(within(popover()!).getAllByRole("option")).toHaveLength(2));
    const driver = within(popover()!).getByRole("group", { name: "Driver" });
    const guard = within(popover()!).getByRole("group", { name: "Guard" });
    expect(within(driver).getByRole("option", { name: /README\.md/ })).toBeInTheDocument();
    expect(within(guard).getByRole("option", { name: /readme\.txt/ })).toBeInTheDocument();
    expect(fileCalls(api).map((c) => c.url.searchParams.get("limit"))).toEqual(["8", "8"]);

    await userEvent.click(within(guard).getByRole("option", { name: /readme\.txt/ }));
    expect(within(chips()!).getByText("Guard")).toBeInTheDocument();
  });

  it("explains a folder that cannot be searched", async () => {
    setup(ONE, {
      "GET /api/agents/agt_1/files": () =>
        errorResponse(422, "validation_error", "working directory no longer exists"),
    });
    await userEvent.type(input(), "@x");
    expect(await screen.findByText("Could not search the folder.")).toBeInTheDocument();
  });

  it("treats @ as plain text without any working directory", async () => {
    const { api, onSend } = setup([]);
    await userEvent.type(input(), "@team hello");
    await userEvent.keyboard("{Enter}");
    expect(popover()).not.toBeInTheDocument();
    expect(fileCalls(api)).toHaveLength(0);
    expect(onSend).toHaveBeenCalledWith("@team hello", [], []);
    expect(screen.queryByText(/Type @ to attach/)).not.toBeInTheDocument();
  });

  it("limits a message to 20 attachments", async () => {
    const many = Array.from({ length: 21 }, (_, i) => ({
      path: `f${String(i).padStart(2, "0")}.txt`,
      is_dir: false,
      size: 1,
    }));
    setup(ONE, { "GET /api/agents/agt_1/files": fakeFileSearch(many) });
    for (let i = 0; i < 21; i++) {
      const name = `f${String(i).padStart(2, "0")}`;
      await userEvent.type(input(), `@${name}`);
      await waitFor(() =>
        expect(within(popover()!).getByRole("option", { name: new RegExp(name) })).toBeInTheDocument(),
      );
      await userEvent.keyboard("{Enter}");
    }
    expect(within(chips()!).getAllByRole("listitem")).toHaveLength(20);
    expect(screen.getByText("You can attach up to 20 files to one message.")).toBeInTheDocument();
  }, 20000);
});
