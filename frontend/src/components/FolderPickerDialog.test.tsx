import { render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it, vi } from "vitest";
import { breadcrumbs } from "../lib/paths";
import { fakeDirectories } from "../test/fakeFs";
import { mockFetch } from "../test/mockFetch";
import { FolderPickerDialog } from "./FolderPickerDialog";

function setup(initialPath: string | null = null) {
  const api = mockFetch({ "GET /api/fs/directories": fakeDirectories });
  const onSelect = vi.fn();
  const onClose = vi.fn();
  render(
    <FolderPickerDialog open initialPath={initialPath} onSelect={onSelect} onClose={onClose} />,
  );
  return { api, onSelect, onClose };
}

const current = () =>
  within(screen.getByRole("navigation", { name: "Current folder" })).getByText(
    (_, el) => el?.getAttribute("aria-current") === "location",
  );
const folders = () => screen.getByRole("list", { name: "Sub-folders" });
const pathBox = () => screen.getByLabelText("Folder path");

describe("breadcrumbs", () => {
  it("splits Windows and POSIX paths", () => {
    expect(breadcrumbs("C:\\Users\\me", ["C:\\", "D:\\"])).toEqual([
      { name: "C:\\", path: "C:\\" },
      { name: "Users", path: "C:\\Users" },
      { name: "me", path: "C:\\Users\\me" },
    ]);
    expect(breadcrumbs("/home/me", ["/"])).toEqual([
      { name: "/", path: "/" },
      { name: "home", path: "/home" },
      { name: "me", path: "/home/me" },
    ]);
    expect(breadcrumbs("/", ["/"])).toEqual([{ name: "/", path: "/" }]);
  });
});

describe("FolderPickerDialog", () => {
  it("starts in the home folder and enters a sub-folder on click", async () => {
    const { api } = setup();
    await waitFor(() => expect(current()).toHaveTextContent("me"));
    expect(api.calls[0].url.searchParams.has("path")).toBe(false);
    expect(pathBox()).toHaveValue("C:\\Users\\me");
    await userEvent.click(within(folders()).getByRole("button", { name: "Projects" }));
    await waitFor(() => expect(current()).toHaveTextContent("Projects"));
    expect(api.calls[1].url.searchParams.get("path")).toBe("C:\\Users\\me\\Projects");
    expect(within(folders()).getByRole("button", { name: "rail" })).toBeInTheDocument();
  });

  it("goes up, home and through breadcrumbs", async () => {
    setup("C:\\Users\\me\\Projects\\rail");
    await waitFor(() => expect(current()).toHaveTextContent("rail"));
    expect(screen.getByText("No sub-folders here.")).toBeInTheDocument();
    await userEvent.click(screen.getByRole("button", { name: "Up to parent folder" }));
    await waitFor(() => expect(current()).toHaveTextContent("Projects"));
    await userEvent.click(screen.getByRole("button", { name: "Users" }));
    await waitFor(() => expect(current()).toHaveTextContent("Users"));
    await userEvent.click(screen.getByRole("button", { name: "Home" }));
    await waitFor(() => expect(current()).toHaveTextContent("me"));
    expect(screen.getByRole("button", { name: "Home" })).toBeDisabled();
  });

  it("disables Up at a filesystem root and switches drives", async () => {
    setup();
    await waitFor(() => expect(current()).toHaveTextContent("me"));
    const drive = screen.getByLabelText("Drive");
    expect(drive).toHaveValue("C:\\");
    await userEvent.selectOptions(drive, "D:\\");
    await waitFor(() => expect(current()).toHaveTextContent("D:\\"));
    expect(screen.getByRole("button", { name: "Up to parent folder" })).toBeDisabled();
    expect(within(folders()).getByRole("button", { name: "Data" })).toBeInTheDocument();
  });

  it("goes to a typed path and shows an error for a bad one, keeping the last folder", async () => {
    setup();
    await waitFor(() => expect(current()).toHaveTextContent("me"));
    await userEvent.clear(pathBox());
    await userEvent.type(pathBox(), "C:\\Nope{Enter}");
    expect(await screen.findByRole("alert")).toHaveTextContent(
      "Cannot open C:\\Nope: Not an existing directory",
    );
    expect(current()).toHaveTextContent("me");
    await userEvent.clear(pathBox());
    await userEvent.type(pathBox(), "D:\\Data");
    await userEvent.click(screen.getByRole("button", { name: "Go" }));
    await waitFor(() => expect(current()).toHaveTextContent("Data"));
    expect(screen.queryByRole("alert")).not.toBeInTheDocument();
  });

  it("falls back to home when the saved folder no longer exists", async () => {
    setup("C:\\Gone");
    await waitFor(() => expect(current()).toHaveTextContent("me"));
    expect(screen.getByRole("alert")).toHaveTextContent("Cannot open C:\\Gone");
  });

  it("explains when nothing can be listed at all", async () => {
    mockFetch({
      "GET /api/fs/directories": () => new Response("boom", { status: 500 }),
    });
    render(<FolderPickerDialog open onSelect={vi.fn()} onClose={vi.fn()} />);
    expect(await screen.findByRole("alert")).toBeInTheDocument();
    expect(await screen.findByText(/No folder to show/)).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Select this folder" })).toBeDisabled();
  });

  it("moves through folders with the keyboard", async () => {
    setup();
    await waitFor(() => expect(current()).toHaveTextContent("me"));
    const first = within(folders()).getByRole("button", { name: "Documents" });
    first.focus();
    await userEvent.keyboard("{ArrowDown}");
    expect(within(folders()).getByRole("button", { name: "Projects" })).toHaveFocus();
    await userEvent.keyboard("{Enter}");
    await waitFor(() => expect(current()).toHaveTextContent("Projects"));
    within(folders()).getByRole("button", { name: "rail" }).focus();
    await userEvent.keyboard("{Backspace}");
    await waitFor(() => expect(current()).toHaveTextContent("me"));
  });

  it("selects the current folder", async () => {
    const { onSelect } = setup();
    await waitFor(() => expect(current()).toHaveTextContent("me"));
    await userEvent.click(within(folders()).getByRole("button", { name: "Projects" }));
    await waitFor(() => expect(current()).toHaveTextContent("Projects"));
    await userEvent.click(screen.getByRole("button", { name: "Select this folder" }));
    expect(onSelect).toHaveBeenCalledWith("C:\\Users\\me\\Projects");
  });

  it("cancels", async () => {
    const { onClose, onSelect } = setup();
    await waitFor(() => expect(current()).toHaveTextContent("me"));
    await userEvent.click(screen.getByRole("button", { name: "Cancel" }));
    expect(onClose).toHaveBeenCalled();
    expect(onSelect).not.toHaveBeenCalled();
  });
});
