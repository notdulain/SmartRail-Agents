import type { DirectoryListing } from "../api/types";
import { errorResponse, type Handler } from "./mockFetch";

const HOME = "C:\\Users\\me";
const ROOTS = ["C:\\", "D:\\"];

/** A tiny Windows-style folder tree: folder path → [parent, sub-folder names]. */
const TREE: Record<string, [string | null, string[]]> = {
  "C:\\": [null, ["Users"]],
  "C:\\Users": ["C:\\", ["me"]],
  "C:\\Users\\me": ["C:\\Users", ["Documents", "Projects"]],
  "C:\\Users\\me\\Documents": ["C:\\Users\\me", []],
  "C:\\Users\\me\\Projects": ["C:\\Users\\me", ["rail"]],
  "C:\\Users\\me\\Projects\\rail": ["C:\\Users\\me\\Projects", []],
  "D:\\": [null, ["Data"]],
  "D:\\Data": ["D:\\", []],
};

function join(dir: string, name: string) {
  return dir.endsWith("\\") ? `${dir}${name}` : `${dir}\\${name}`;
}

export function listing(path: string): DirectoryListing {
  const [parent, names] = TREE[path];
  return {
    path,
    parent,
    entries: names.map((name) => ({ name, path: join(path, name) })),
    home: HOME,
    roots: ROOTS,
  };
}

/** mockFetch handler for GET /api/fs/directories backed by TREE. */
export const fakeDirectories: Handler = ({ url }) => {
  const raw = url.searchParams.get("path");
  const path = raw ? raw.replace(/[\\/]+$/, "") || raw : HOME;
  const key = TREE[path] ? path : TREE[`${path}\\`] ? `${path}\\` : null;
  if (!key) return errorResponse(422, "validation_error", "Not an existing directory");
  return listing(key);
};
