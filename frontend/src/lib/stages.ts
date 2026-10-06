export const STAGE_LABELS: Record<number, string> = {
  0: "Agenda",
  1: "Round 1",
  2: "Replies",
  3: "Summary",
};

export function stageLabel(stage: number | null | undefined): string | null {
  if (stage === null || stage === undefined) return null;
  return STAGE_LABELS[stage] ?? `Stage ${stage}`;
}
