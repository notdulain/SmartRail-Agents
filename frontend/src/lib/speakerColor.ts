import type { CSSProperties } from "react";

/** Restrained per-speaker colours. Index is stable per agent id; colours live in index.css. */
export const SPEAKER_COUNT = 8;

export function speakerIndex(key: string): number {
  let h = 0;
  for (let i = 0; i < key.length; i++) h = (h * 31 + key.charCodeAt(i)) >>> 0;
  return h % SPEAKER_COUNT;
}

export function speakerStyle(key: string | null | undefined): CSSProperties {
  const n = key ? speakerIndex(key) : 0;
  return { "--speaker": `var(--sp-${n})`, "--speaker-soft": `var(--sp-${n}-soft)` } as CSSProperties;
}

export function initials(name: string): string {
  const words = name.trim().split(/\s+/).filter(Boolean);
  if (words.length === 0) return "?";
  if (words.length === 1) return words[0].slice(0, 2).toUpperCase();
  return (words[0][0] + words[1][0]).toUpperCase();
}
