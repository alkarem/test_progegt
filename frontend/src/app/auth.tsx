import { createContext, useCallback, useContext, useEffect, useState, type ReactNode } from "react";
import { api, onAuthLost, setAccessToken, tryRestoreSession } from "@/api/client";

export type Me = {
  id: string; username: string; full_name: string; roles: string[]; permissions: string[];
  scopes: Record<string, string[]>; must_change_password: boolean;
};

type AuthState = {
  me: Me | null; loading: boolean;
  login: (u: string, p: string) => Promise<void>;
  logout: () => Promise<void>;
  reload: () => Promise<void>;
  can: (perm: string) => boolean;
};

const Ctx = createContext<AuthState>(null as unknown as AuthState);
export const useAuth = () => useContext(Ctx);

export function AuthProvider({ children }: { children: ReactNode }) {
  const [me, setMe] = useState<Me | null>(null);
  const [loading, setLoading] = useState(true);

  const reload = useCallback(async () => {
    try { setMe(await api<Me>("/auth/me")); } catch { setMe(null); }
  }, []);

  useEffect(() => {
    onAuthLost(() => setMe(null));
    (async () => {
      if (await tryRestoreSession()) await reload();
      setLoading(false);
    })();
  }, [reload]);

  const login = async (username: string, password: string) => {
    const t = await api<{ access_token: string }>("/auth/login", { body: { username, password } });
    setAccessToken(t.access_token);
    await reload();
  };

  const logout = async () => {
    try { await api("/auth/logout", { method: "POST" }); } finally { setAccessToken(null); setMe(null); }
  };

  const can = (perm: string) => !!me?.permissions.includes(perm);
  return <Ctx.Provider value={{ me, loading, login, logout, reload, can }}>{children}</Ctx.Provider>;
}
