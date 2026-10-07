/** Helpers for displaying absolute paths from the backend (Windows or POSIX style). */

function isWindowsPath(path: string): boolean {
  return /^[a-zA-Z]:/.test(path) || path.startsWith("\\\\") || path.includes("\\");
}

function sameRoot(path: string, root: string, windows: boolean): boolean {
  return windows ? path.toLowerCase().startsWith(root.toLowerCase()) : path.startsWith(root);
}

/** The filesystem root (from `roots`) that `path` lives under, preferring the longest match. */
export function rootOf(path: string, roots: string[]): string | null {
  const windows = isWindowsPath(path);
  let best: string | null = null;
  for (const r of roots) {
    if (sameRoot(path, r, windows) && (!best || r.length > best.length)) best = r;
  }
  return best;
}

export interface Crumb {
  name: string;
  path: string;
}

/** Splits an absolute path into clickable segments: the root, then each folder. */
export function breadcrumbs(path: string, roots: string[] = []): Crumb[] {
  const windows = isWindowsPath(path);
  const sep = windows ? "\\" : "/";
  let root = rootOf(path, roots);
  if (!root) {
    const m = windows ? /^[a-zA-Z]:[\\/]?/.exec(path) : /^\/+/.exec(path);
    root = m ? m[0] : "";
  }
  const rest = path.slice(root.length).split(/[\\/]+/).filter(Boolean);
  const crumbs: Crumb[] = [];
  if (root) crumbs.push({ name: root, path: root });
  let acc = root;
  for (const part of rest) {
    acc = acc && !/[\\/]$/.test(acc) ? `${acc}${sep}${part}` : `${acc}${part}`;
    crumbs.push({ name: part, path: acc });
  }
  return crumbs;
}

/** Last segment of a path ("C:\\Work\\rail" → "rail"; a root returns itself). */
export function basename(path: string): string {
  const parts = path.split(/[\\/]+/).filter(Boolean);
  return parts.length ? parts[parts.length - 1] : path;
}
