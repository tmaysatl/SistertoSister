import AsyncStorage from "@react-native-async-storage/async-storage";
import { supabase, SUPABASE_CONFIGURED } from "../lib/supabase";

const BASE = process.env.EXPO_PUBLIC_BACKEND_URL;

export const API_BASE = `${BASE}/api`;

async function authHeaders(): Promise<Record<string, string>> {
  const h: Record<string, string> = { "Content-Type": "application/json" };
  // Prefer the live Supabase access token if a session is active.
  if (SUPABASE_CONFIGURED) {
    try {
      const { data } = await supabase.auth.getSession();
      const sbToken = data.session?.access_token;
      if (sbToken) {
        h.Authorization = `Bearer ${sbToken}`;
        return h;
      }
    } catch {
      /* ignore — fall through to legacy token */
    }
  }
  const token = await AsyncStorage.getItem("userToken");
  if (token) h.Authorization = `Bearer ${token}`;
  return h;
}

/**
 * Returns the current Bearer token (Supabase access token if a session is
 * live, otherwise the legacy `userToken`). Returns null if the caller is
 * unauthenticated. Use this in places that need just the token string (e.g.
 * to authenticate a fetch that streams binary like PDF blobs).
 */
export async function getAuthToken(): Promise<string | null> {
  if (SUPABASE_CONFIGURED) {
    try {
      const { data } = await supabase.auth.getSession();
      const sbToken = data.session?.access_token;
      if (sbToken) return sbToken;
    } catch {
      /* fall through */
    }
  }
  return AsyncStorage.getItem("userToken");
}

/**
 * Returns the full headers map (Content-Type JSON + Authorization Bearer)
 * for an authenticated request. Use this instead of building headers
 * manually from AsyncStorage.getItem("userToken") — that pattern misses
 * the Supabase session and causes silent 401s in Supabase mode.
 */
export async function getAuthHeaders(): Promise<Record<string, string>> {
  return authHeaders();
}

export async function apiGet<T>(path: string): Promise<T> {
  const res = await fetch(`${API_BASE}${path}`, { headers: await authHeaders() });
  if (!res.ok) throw new Error(`GET ${path} failed: ${res.status}`);
  return res.json();
}

export async function apiPost<T>(path: string, body: any): Promise<T> {
  const res = await fetch(`${API_BASE}${path}`, {
    method: "POST",
    headers: await authHeaders(),
    body: JSON.stringify(body),
  });
  if (!res.ok) {
    const t = await res.text();
    throw new Error(`POST ${path} failed: ${res.status} ${t}`);
  }
  return res.json();
}

export async function apiDelete<T>(path: string): Promise<T> {
  const res = await fetch(`${API_BASE}${path}`, {
    method: "DELETE",
    headers: await authHeaders(),
  });
  if (!res.ok) throw new Error(`DELETE ${path} failed: ${res.status}`);
  return res.json();
}

export async function apiPut<T>(path: string, body: any): Promise<T> {
  const res = await fetch(`${API_BASE}${path}`, {
    method: "PUT",
    headers: await authHeaders(),
    body: JSON.stringify(body),
  });
  if (!res.ok) {
    const t = await res.text();
    throw new Error(`PUT ${path} failed: ${res.status} ${t}`);
  }
  return res.json();
}
