import { Platform, Linking } from "react-native";
import * as FileSystem from "expo-file-system/legacy";
import * as Sharing from "expo-sharing";
import { API_BASE, getAuthToken } from "../api/client";

function safeFileName(s: string): string {
  return s.replace(/[^a-z0-9._-]+/gi, "_").slice(0, 80);
}

async function openWeb(blob: Blob, filename?: string): Promise<void> {
  const url = URL.createObjectURL(blob);
  // 1) Try opening in a new tab via window.open (works in normal browsers)
  let newWin: Window | null = null;
  try {
    newWin = window.open(url, "_blank", "noopener,noreferrer");
  } catch { /* ignore */ }
  if (newWin && !newWin.closed) return;
  // 2) Fallback: anchor click (works around some popup blockers)
  const a = document.createElement("a");
  a.href = url;
  a.target = "_blank";
  a.rel = "noopener noreferrer";
  if (filename) a.download = filename;
  document.body.appendChild(a);
  a.click();
  document.body.removeChild(a);
  // 3) Last-resort: navigate current frame to the blob URL
  setTimeout(() => {
    // If still nothing visible, send the embedding window to the blob URL.
    try {
      const top = window.top || window;
      if (top && top.location) top.location.assign(url);
    } catch {
      window.location.assign(url);
    }
  }, 800);
}

async function openNative(blob: Blob, filename: string): Promise<void> {
  // Read as base64
  const reader = new FileReader();
  const b64 = await new Promise<string>((resolve, reject) => {
    reader.onloadend = () => {
      const s = (reader.result as string) || "";
      resolve(s.split(",")[1] || "");
    };
    reader.onerror = reject;
    reader.readAsDataURL(blob);
  });
  const path = `${FileSystem.cacheDirectory}${safeFileName(filename)}`;
  await FileSystem.writeAsStringAsync(path, b64, { encoding: FileSystem.EncodingType.Base64 });
  if (await Sharing.isAvailableAsync()) {
    await Sharing.shareAsync(path, { mimeType: "application/pdf", dialogTitle: filename });
  } else {
    await Linking.openURL(path);
  }
}

export async function openAuthedFile(path: string, filename = "document.pdf"): Promise<void> {
  const token = await getAuthToken();
  const res = await fetch(`${API_BASE}${path}`, {
    headers: token ? { Authorization: `Bearer ${token}` } : {},
  });
  if (!res.ok) throw new Error(`Open failed: ${res.status}`);
  const blob = await res.blob();
  if (Platform.OS === "web") return openWeb(blob, filename);
  return openNative(blob, filename);
}

export async function openPublicFile(url: string, filename = "document.pdf"): Promise<void> {
  const res = await fetch(url);
  const blob = await res.blob();
  if (Platform.OS === "web") return openWeb(blob, filename);
  return openNative(blob, filename);
}
