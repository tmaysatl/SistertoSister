import React, { createContext, useContext, useEffect, useState } from "react";
import AsyncStorage from "@react-native-async-storage/async-storage";
import { Platform } from "react-native";
import * as Notifications from "expo-notifications";
import { API_BASE } from "../api/client";
import { supabase, SUPABASE_CONFIGURED } from "../lib/supabase";

export type Role = "admin" | "caregiver";

export type User = {
  id: string;
  email: string;
  name: string;
  role: Role;
  created_at: string;
};

type AuthMode = "supabase" | "legacy";

type AuthState = {
  user: User | null;
  token: string | null;
  loading: boolean;
  mode: AuthMode;
  setMode: (m: AuthMode) => Promise<void>;
  login: (email: string, password: string) => Promise<void>;
  register: (email: string, password: string, name: string, role: Role) => Promise<void>;
  logout: () => Promise<void>;
};

const AuthContext = createContext<AuthState | undefined>(undefined);

const MODE_KEY = "authMode";

export const AuthProvider: React.FC<{ children: React.ReactNode }> = ({ children }) => {
  const [user, setUser] = useState<User | null>(null);
  const [token, setToken] = useState<string | null>(null);
  const [loading, setLoading] = useState(true);
  // Phase 6 -> Aug 2026: the Supabase project auto-paused on the free tier,
  // making Supabase Auth unreachable. Falling back to Legacy JWT as the
  // default. Users can flip back to Supabase via the toggle pill on the
  // login screen once the project is resumed. The dual-mode auth verifier
  // on the backend still accepts either token type — nothing else changes.
  const [mode, setModeState] = useState<AuthMode>("legacy");

  // Bootstrap: detect persisted mode + restore Supabase session OR legacy creds.
  useEffect(() => {
    (async () => {
      try {
        const storedMode = (await AsyncStorage.getItem(MODE_KEY)) as AuthMode | null;
        // Force legacy as the effective mode until the Supabase project is
        // unpaused (see the header comment on the useState line). We
        // deliberately IGNORE any previously stored preference during this
        // outage window — users who previously signed in via Supabase would
        // otherwise be stuck on the login screen with an unreachable server.
        // TODO(post-outage): revert to `storedMode ?? (SUPABASE_CONFIGURED ? "supabase" : "legacy")`.
        void storedMode; // kept for a future toggle re-read
        const effectiveMode: AuthMode = "legacy";
        setModeState(effectiveMode);

        if (effectiveMode === "supabase" && SUPABASE_CONFIGURED) {
          // Show cached user immediately for snappy cold-start UX
          try {
            const cached = await AsyncStorage.getItem("sbUserInfo");
            if (cached) setUser(JSON.parse(cached));
          } catch { /* ignore */ }

          const { data } = await supabase.auth.getSession();
          const session = data.session;
          if (session?.access_token) {
            setToken(session.access_token);
            // Pull merged profile from /api/supabase/me (Mongo bridge + Postgres profile)
            try {
              const res = await fetch(`${API_BASE}/supabase/me`, {
                headers: { Authorization: `Bearer ${session.access_token}` },
              });
              if (res.ok) {
                const body = await res.json();
                setUser(body.user as User);
                await AsyncStorage.setItem("sbUserInfo", JSON.stringify(body.user));
              }
            } catch { /* ignore */ }
          } else {
            // No session but cached user from a prior login — drop it
            setUser(null);
            await AsyncStorage.removeItem("sbUserInfo");
          }
        } else {
          const t = await AsyncStorage.getItem("userToken");
          const u = await AsyncStorage.getItem("userInfo");
          if (t) setToken(t);
          if (u) setUser(JSON.parse(u));
        }
      } finally {
        setLoading(false);
      }
    })();
  }, []);

  // Keep token in sync with Supabase auth state changes.
  useEffect(() => {
    if (mode !== "supabase" || !SUPABASE_CONFIGURED) return;
    const { data: sub } = supabase.auth.onAuthStateChange((_event, session) => {
      setToken(session?.access_token ?? null);
      if (!session) setUser(null);
    });
    return () => { sub.subscription?.unsubscribe(); };
  }, [mode]);

  const setMode = async (m: AuthMode) => {
    await AsyncStorage.setItem(MODE_KEY, m);
    setModeState(m);
  };

  const persistLegacy = async (t: string, u: User) => {
    await AsyncStorage.setItem("userToken", t);
    await AsyncStorage.setItem("userInfo", JSON.stringify(u));
    setToken(t);
    setUser(u);
    if (Platform.OS !== "web") {
      try {
        const { status } = await Notifications.requestPermissionsAsync();
        if (status === "granted") {
          const tokenResp = await Notifications.getDevicePushTokenAsync();
          await fetch(`${API_BASE}/register-push`, {
            method: "POST",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify({ user_id: u.id, platform: Platform.OS, device_token: tokenResp.data }),
          });
        }
      } catch (e) {
        console.log("push register skipped:", e);
      }
    }
  };

  const loginSupabase = async (email: string, password: string) => {
    const { data, error } = await supabase.auth.signInWithPassword({ email, password });
    if (error) throw new Error(error.message);
    if (!data.session?.access_token) throw new Error("No session returned");
    setToken(data.session.access_token);
    // Resolve user via backend bridge so we get the Mongo UserPublic.
    const res = await fetch(`${API_BASE}/supabase/me`, {
      headers: { Authorization: `Bearer ${data.session.access_token}` },
    });
    if (!res.ok) {
      const t = await res.text();
      throw new Error(t || "Profile lookup failed");
    }
    const body = await res.json();
    setUser(body.user as User);
    try { await AsyncStorage.setItem("sbUserInfo", JSON.stringify(body.user)); } catch { /* ignore */ }
  };

  const loginLegacy = async (email: string, password: string) => {
    const res = await fetch(`${API_BASE}/auth/login`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ email, password }),
    });
    if (!res.ok) throw new Error((await res.text()) || "Login failed");
    const data = await res.json();
    await persistLegacy(data.access_token, data.user);
  };

  const login = async (email: string, password: string) => {
    if (mode === "supabase" && SUPABASE_CONFIGURED) {
      await loginSupabase(email.trim(), password);
    } else {
      await loginLegacy(email.trim(), password);
    }
  };

  const register = async (email: string, password: string, name: string, role: Role) => {
    if (mode === "supabase" && SUPABASE_CONFIGURED) {
      const { data, error } = await supabase.auth.signUp({
        email,
        password,
        options: { data: { name, role } },
      });
      if (error) throw new Error(error.message);
      if (data.session?.access_token) {
        setToken(data.session.access_token);
        const res = await fetch(`${API_BASE}/supabase/me`, {
          headers: { Authorization: `Bearer ${data.session.access_token}` },
        });
        if (res.ok) {
          const body = await res.json();
          setUser(body.user as User);
        }
      }
      return;
    }
    const res = await fetch(`${API_BASE}/auth/register`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ email, password, name, role }),
    });
    if (!res.ok) throw new Error((await res.text()) || "Register failed");
    const data = await res.json();
    await persistLegacy(data.access_token, data.user);
  };

  const logout = async () => {
    // Phase 6: always sign out of Supabase to avoid stale sessions when users
    // switch between modes. Safe to call even when no session exists.
    if (SUPABASE_CONFIGURED) {
      try { await supabase.auth.signOut(); } catch { /* ignore */ }
    }
    await AsyncStorage.multiRemove(["userToken", "userInfo", "sbUserInfo"]);
    setToken(null);
    setUser(null);
  };

  return (
    <AuthContext.Provider value={{ user, token, loading, mode, setMode, login, register, logout }}>
      {children}
    </AuthContext.Provider>
  );
};

export const useAuth = (): AuthState => {
  const ctx = useContext(AuthContext);
  if (!ctx) throw new Error("useAuth must be used inside AuthProvider");
  return ctx;
};
