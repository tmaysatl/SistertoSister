import React, { createContext, useContext, useEffect, useState } from "react";
import AsyncStorage from "@react-native-async-storage/async-storage";
import { Platform } from "react-native";
import * as Notifications from "expo-notifications";
import { API_BASE } from "../api/client";

export type Role = "admin" | "caregiver";

export type User = {
  id: string;
  email: string;
  name: string;
  role: Role;
  created_at: string;
};

type AuthState = {
  user: User | null;
  token: string | null;
  loading: boolean;
  login: (email: string, password: string) => Promise<void>;
  register: (email: string, password: string, name: string, role: Role) => Promise<void>;
  logout: () => Promise<void>;
};

const AuthContext = createContext<AuthState | undefined>(undefined);

export const AuthProvider: React.FC<{ children: React.ReactNode }> = ({ children }) => {
  const [user, setUser] = useState<User | null>(null);
  const [token, setToken] = useState<string | null>(null);
  const [loading, setLoading] = useState(true);

  useEffect(() => {
    (async () => {
      try {
        const t = await AsyncStorage.getItem("userToken");
        const u = await AsyncStorage.getItem("userInfo");
        if (t) setToken(t);
        if (u) setUser(JSON.parse(u));
      } finally {
        setLoading(false);
      }
    })();
  }, []);

  const persist = async (t: string, u: User) => {
    await AsyncStorage.setItem("userToken", t);
    await AsyncStorage.setItem("userInfo", JSON.stringify(u));
    setToken(t);
    setUser(u);
    // Register for push (native only; safe no-op in Expo Go without plugin)
    if (Platform.OS !== "web") {
      try {
        const { status } = await Notifications.requestPermissionsAsync();
        if (status === "granted") {
          const tokenResp = await Notifications.getDevicePushTokenAsync();
          await fetch(`${API_BASE}/register-push`, {
            method: "POST",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify({
              user_id: u.id,
              platform: Platform.OS,
              device_token: tokenResp.data,
            }),
          });
        }
      } catch (e) {
        console.log("push register skipped:", e);
      }
    }
  };

  const login = async (email: string, password: string) => {
    const res = await fetch(`${API_BASE}/auth/login`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ email, password }),
    });
    if (!res.ok) {
      const t = await res.text();
      throw new Error(t || "Login failed");
    }
    const data = await res.json();
    await persist(data.access_token, data.user);
  };

  const register = async (email: string, password: string, name: string, role: Role) => {
    const res = await fetch(`${API_BASE}/auth/register`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ email, password, name, role }),
    });
    if (!res.ok) {
      const t = await res.text();
      throw new Error(t || "Register failed");
    }
    const data = await res.json();
    await persist(data.access_token, data.user);
  };

  const logout = async () => {
    await AsyncStorage.multiRemove(["userToken", "userInfo"]);
    setToken(null);
    setUser(null);
  };

  return (
    <AuthContext.Provider value={{ user, token, loading, login, register, logout }}>
      {children}
    </AuthContext.Provider>
  );
};

export const useAuth = (): AuthState => {
  const ctx = useContext(AuthContext);
  if (!ctx) throw new Error("useAuth must be used inside AuthProvider");
  return ctx;
};
