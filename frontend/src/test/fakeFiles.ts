import type { FileEntry, FileSearchResponse } from "../api/types";
import type { Handler } from "./mockFetch";

export const SAMPLE_FILES: FileEntry[] = [
  { path: "README.md", is_dir: false, size: 1200 },
  { path: "src", is_dir: true, size: null },
  { path: "src/main.ts", is_dir: false, size: 340 },
  { path: "src/app.tsx", is_dir: false, size: 5120 },
  { path: "docs/guide.md", is_dir: false, size: 80 },
];

function subsequence(query: string, path: string): boolean {
  const q = query.toLowerCase();
  const p = path.toLowerCase();
  let i = 0;
  for (const ch of p) if (ch === q[i]) i++;
  return i >= q.length;
}

/** mockFetch handler for GET /api/agents/{id}/files mimicking the backend's subsequence match. */
export function fakeFileSearch(files: FileEntry[] = SAMPLE_FILES, root = "C:\\work"): Handler {
  return ({ url }): FileSearchResponse => {
    const q = url.searchParams.get("q") ?? "";
    const limit = Number(url.searchParams.get("limit") ?? 50);
    const matches = files.filter((f) => subsequence(q, f.path));
    return { root, files: matches.slice(0, limit), truncated: matches.length > limit };
  };
}
