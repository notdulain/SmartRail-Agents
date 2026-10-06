import {
  createContext,
  useCallback,
  useContext,
  useEffect,
  useMemo,
  useState,
  type ReactNode,
} from "react";
import { api } from "../api/client";
import type { Agent, Conversation, ProvidersResponse, Settings } from "../api/types";
import { useToasts } from "./toast";

interface AppData {
  agents: Agent[];
  conversations: Conversation[];
  settings: Settings | null;
  providers: ProvidersResponse | null;
  providersLoading: boolean;
  loaded: boolean;
  /** Initial load failed (backend down). */
  loadFailed: boolean;
  refreshAgents(): Promise<Agent[]>;
  refreshConversations(): Promise<Conversation[]>;
  refreshSettings(): Promise<Settings | null>;
  loadProviders(refresh?: boolean): Promise<ProvidersResponse | null>;
  retry(): void;
  setSettings(s: Settings): void;
}

const Ctx = createContext<AppData | null>(null);

export function AppDataProvider({ children }: { children: ReactNode }) {
  const toasts = useToasts();
  const [agents, setAgents] = useState<Agent[]>([]);
  const [conversations, setConversations] = useState<Conversation[]>([]);
  const [settings, setSettings] = useState<Settings | null>(null);
  const [providers, setProviders] = useState<ProvidersResponse | null>(null);
  const [providersLoading, setProvidersLoading] = useState(false);
  const [loaded, setLoaded] = useState(false);
  const [loadFailed, setLoadFailed] = useState(false);
  const [attempt, setAttempt] = useState(0);

  const refreshAgents = useCallback(async () => {
    const list = await api.listAgents();
    setAgents(list);
    return list;
  }, []);
  const refreshConversations = useCallback(async () => {
    const list = await api.listConversations();
    setConversations(list);
    return list;
  }, []);
  const refreshSettings = useCallback(async () => {
    try {
      const s = await api.getSettings();
      setSettings(s);
      return s;
    } catch {
      return null;
    }
  }, []);

  const loadProviders = useCallback(
    async (refresh = false) => {
      setProvidersLoading(true);
      try {
        const res = await api.getProviders(refresh);
        setProviders(res);
        return res;
      } catch (err) {
        toasts.error(err);
        return null;
      } finally {
        setProvidersLoading(false);
      }
    },
    [],
  );

  useEffect(() => {
    let alive = true;
    setLoadFailed(false);
    Promise.all([refreshAgents(), refreshConversations(), refreshSettings()])
      .then(() => alive && setLoaded(true))
      .catch(() => {
        if (alive) {
          setLoadFailed(true);
          setLoaded(true);
        }
      });
    void loadProviders(false);
    return () => {
      alive = false;
    };
  }, [attempt, refreshAgents, refreshConversations, refreshSettings, loadProviders]);

  const retry = useCallback(() => setAttempt((n) => n + 1), []);

  const value = useMemo<AppData>(
    () => ({
      agents,
      conversations,
      settings,
      providers,
      providersLoading,
      loaded,
      loadFailed,
      refreshAgents,
      refreshConversations,
      refreshSettings,
      loadProviders,
      retry,
      setSettings,
    }),
    [
      agents,
      conversations,
      settings,
      providers,
      providersLoading,
      loaded,
      loadFailed,
      refreshAgents,
      refreshConversations,
      refreshSettings,
      loadProviders,
      retry,
    ],
  );
  return <Ctx.Provider value={value}>{children}</Ctx.Provider>;
}

export function useAppData(): AppData {
  const ctx = useContext(Ctx);
  if (!ctx) throw new Error("AppDataProvider missing");
  return ctx;
}
