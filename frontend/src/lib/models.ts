import type { ModelInfo, ProviderInfo, ProvidersResponse } from "../api/types";

export interface ModelChoice {
  providerId: string;
  modelId: string;
}

export function formatPrice(model: ModelInfo): string | null {
  const p = model.price;
  if (!p) return null;
  const fmt = (n: number) => `$${n >= 10 ? n.toFixed(0) : n.toFixed(2)}`;
  const parts: string[] = [];
  if (p.input_per_mtok != null) parts.push(`${fmt(p.input_per_mtok)} in`);
  if (p.output_per_mtok != null) parts.push(`${fmt(p.output_per_mtok)} out`);
  return parts.length ? `${parts.join(" · ")} per 1M tokens` : null;
}

/** Connected providers first (stable within each group), then the rest. */
export function sortProviders(providers: ProviderInfo[]): ProviderInfo[] {
  return [...providers].sort((a, b) => Number(b.connected) - Number(a.connected));
}

export function filterProviders(providers: ProviderInfo[], query: string): ProviderInfo[] {
  const q = query.trim().toLowerCase();
  const sorted = sortProviders(providers);
  if (!q) return sorted;
  const terms = q.split(/\s+/);
  const out: ProviderInfo[] = [];
  for (const p of sorted) {
    const providerText = `${p.name} ${p.id}`.toLowerCase();
    const models = p.models.filter((m) => {
      const hay = `${providerText} ${m.name} ${m.id}`.toLowerCase();
      return terms.every((t) => hay.includes(t));
    });
    if (models.length) out.push({ ...p, models });
  }
  return out;
}

export type ChoiceStatus = "ok" | "disconnected" | "missing";

/** Whether a (provider, model) choice can be used right now. */
export function choiceStatus(
  catalog: ProvidersResponse | null,
  choice: ModelChoice | null,
): ChoiceStatus | "unknown" {
  if (!choice || !catalog) return "unknown";
  const provider = catalog.providers.find((p) => p.id === choice.providerId);
  if (!provider) return "missing";
  if (!provider.connected) return "disconnected";
  return provider.models.some((m) => m.id === choice.modelId) ? "ok" : "missing";
}

export function providerName(catalog: ProvidersResponse | null, id: string): string {
  return catalog?.providers.find((p) => p.id === id)?.name ?? id;
}

export function modelName(catalog: ProvidersResponse | null, providerId: string, id: string): string {
  return (
    catalog?.providers.find((p) => p.id === providerId)?.models.find((m) => m.id === id)?.name ?? id
  );
}
