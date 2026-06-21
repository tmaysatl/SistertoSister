import { useCallback, useEffect, useState } from "react";
import {
  View, Text, StyleSheet, Pressable, ActivityIndicator, Modal, TextInput, Alert, Platform,
} from "react-native";
import { Ionicons } from "@expo/vector-icons";
import * as Linking from "expo-linking";
import { apiGet, apiPost } from "@/src/api/client";
import { theme } from "@/src/theme";

type MsStatus = {
  configured: boolean;
  connected: boolean;
  connected_email?: string | null;
  email_to?: string | null;
  schedule?: string;
  last_export?: {
    ok: boolean;
    filename?: string;
    size_bytes?: number;
    ran_at?: string;
    share_url?: string;
    onedrive_web_url?: string;
  } | null;
};

function fmtSize(n?: number) {
  if (!n) return "";
  if (n < 1024) return `${n} B`;
  if (n < 1024 * 1024) return `${(n / 1024).toFixed(1)} KB`;
  return `${(n / (1024 * 1024)).toFixed(1)} MB`;
}
function fmtTime(iso?: string | null) {
  if (!iso) return "";
  try { return new Date(iso).toLocaleString(); } catch { return iso || ""; }
}

export function MicrosoftIntegrationCard() {
  const [status, setStatus] = useState<MsStatus | null>(null);
  const [loading, setLoading] = useState(true);
  const [exporting, setExporting] = useState(false);
  const [editEmail, setEditEmail] = useState<string | null>(null);
  const [savingEmail, setSavingEmail] = useState(false);

  const load = useCallback(async () => {
    try {
      const s = await apiGet<MsStatus>("/ms/status");
      setStatus(s);
    } catch { /* ignore */ }
    finally { setLoading(false); }
  }, []);

  useEffect(() => {
    load();
    const sub = Linking.addEventListener("url", () => { load(); });
    return () => sub.remove();
  }, [load]);

  const connect = async () => {
    try {
      const { url } = await apiGet<{ url: string }>("/ms/auth-url");
      if (Platform.OS === "web") {
        if (typeof window !== "undefined") window.open(url, "_blank", "noopener,noreferrer");
      } else {
        await Linking.openURL(url);
      }
      // Re-poll after a few seconds so the card flips to "Connected" when the user comes back
      setTimeout(load, 5000);
    } catch (e: any) {
      const msg = e?.message || "";
      Alert.alert("Microsoft connection failed",
        msg.includes("503") ? "Server is missing Microsoft credentials." : msg);
    }
  };

  const disconnect = async () => {
    const ok = Platform.OS === "web"
      ? (typeof window !== "undefined" && window.confirm("Disconnect Microsoft 365? Monthly audit binders will stop syncing."))
      : await new Promise<boolean>((r) => Alert.alert(
        "Disconnect Microsoft 365?",
        "Monthly audit binders will stop syncing.",
        [{ text: "Cancel", style: "cancel", onPress: () => r(false) },
        { text: "Disconnect", style: "destructive", onPress: () => r(true) }],
      ));
    if (!ok) return;
    await apiPost("/ms/disconnect", {});
    await load();
  };

  const runNow = async () => {
    setExporting(true);
    try {
      await apiPost("/ms/export-now", {});
      await load();
      Alert.alert("Export complete", "The audit binder was uploaded to your OneDrive.");
    } catch (e: any) {
      Alert.alert("Export failed", e?.message || "Try again later.");
    } finally {
      setExporting(false);
    }
  };

  const saveEmail = async () => {
    setSavingEmail(true);
    try {
      await apiPost("/ms/email-recipients", { email_to: editEmail });
      await load();
      setEditEmail(null);
    } finally { setSavingEmail(false); }
  };

  if (loading) {
    return (
      <View style={[styles.card, { alignItems: "center", justifyContent: "center", minHeight: 64 }]}>
        <ActivityIndicator color={theme.colors.brandPrimary} />
      </View>
    );
  }
  if (!status) return null;

  if (!status.configured) {
    return (
      <View style={[styles.card, styles.warnCard]}>
        <Ionicons name="warning-outline" size={20} color={theme.colors.warning} />
        <View style={{ flex: 1 }}>
          <Text style={styles.title}>Microsoft 365 not configured</Text>
          <Text style={styles.sub}>Set MS_TENANT_ID / MS_CLIENT_ID / MS_CLIENT_SECRET in backend .env.</Text>
        </View>
      </View>
    );
  }

  if (!status.connected) {
    return (
      <Pressable testID="ms-connect" onPress={connect} style={[styles.card, styles.ctaCard]}>
        <Ionicons name="cloud-outline" size={22} color="#fff" />
        <View style={{ flex: 1 }}>
          <Text style={[styles.title, { color: "#fff" }]}>Connect Microsoft 365</Text>
          <Text style={[styles.sub, { color: "rgba(255,255,255,0.85)" }]}>
            Auto-export the Audit Binder to OneDrive on the 1st of every month.
          </Text>
        </View>
        <Ionicons name="chevron-forward" size={20} color="#fff" />
      </Pressable>
    );
  }

  return (
    <View style={[styles.card, styles.connectedCard]}>
      <View style={{ flexDirection: "row", alignItems: "center", gap: 10 }}>
        <View style={styles.iconCircle}>
          <Ionicons name="cloud-done-outline" size={18} color={theme.colors.success} />
        </View>
        <View style={{ flex: 1 }}>
          <Text style={styles.title}>Microsoft 365 connected</Text>
          <Text style={styles.sub} numberOfLines={1}>{status.connected_email || ""}</Text>
        </View>
        <Pressable testID="ms-disconnect" onPress={disconnect} hitSlop={10}>
          <Ionicons name="close-circle-outline" size={20} color={theme.colors.muted} />
        </Pressable>
      </View>

      <Text style={styles.subDim}>{status.schedule}</Text>

      {status.last_export?.ran_at && (
        <View style={styles.lastRow}>
          <Ionicons name="checkmark-done" size={14} color={theme.colors.success} />
          <Text style={styles.lastText} numberOfLines={1}>
            Last: {status.last_export.filename} · {fmtSize(status.last_export.size_bytes)} · {fmtTime(status.last_export.ran_at)}
          </Text>
          {!!status.last_export.share_url && (
            <Pressable onPress={() => Linking.openURL(status.last_export!.share_url!)}>
              <Text style={styles.openLink}>Open</Text>
            </Pressable>
          )}
        </View>
      )}

      <View style={{ flexDirection: "row", gap: 8, marginTop: 8 }}>
        <Pressable
          testID="ms-export-now"
          onPress={runNow}
          disabled={exporting}
          style={[styles.btn, { flex: 1, opacity: exporting ? 0.6 : 1 }]}
        >
          {exporting ? <ActivityIndicator color="#fff" /> : (
            <>
              <Ionicons name="cloud-upload-outline" size={14} color="#fff" />
              <Text style={styles.btnText}>Export now</Text>
            </>
          )}
        </Pressable>
        <Pressable
          testID="ms-set-emails"
          onPress={() => setEditEmail(status.email_to || "")}
          style={[styles.btnGhost, { flex: 1 }]}
        >
          <Ionicons name="mail-outline" size={14} color={theme.colors.brandPrimary} />
          <Text style={styles.btnGhostText} numberOfLines={1}>
            {status.email_to ? "Recipients" : "Add email recipients"}
          </Text>
        </Pressable>
      </View>

      {/* Email recipients edit modal */}
      <Modal visible={editEmail !== null} transparent animationType="fade" onRequestClose={() => setEditEmail(null)}>
        <Pressable style={styles.modalBackdrop} onPress={() => setEditEmail(null)}>
          <Pressable style={styles.modalCard} onPress={() => { }}>
            <Text style={styles.title}>Email recipients</Text>
            <Text style={[styles.sub, { marginBottom: 8 }]}>
              {`Comma-separated. They'll receive a link to the binder each month.`}
            </Text>
            <TextInput
              testID="ms-email-input"
              value={editEmail || ""}
              onChangeText={setEditEmail}
              placeholder="natasha@…, owner@…"
              placeholderTextColor={theme.colors.muted}
              autoCapitalize="none"
              keyboardType="email-address"
              style={styles.input}
              multiline
            />
            <View style={{ flexDirection: "row", gap: 8, marginTop: 12 }}>
              <Pressable onPress={() => setEditEmail(null)} style={[styles.btnGhost, { flex: 1 }]}>
                <Text style={styles.btnGhostText}>Cancel</Text>
              </Pressable>
              <Pressable testID="ms-save-emails" onPress={saveEmail} disabled={savingEmail} style={[styles.btn, { flex: 1 }]}>
                {savingEmail ? <ActivityIndicator color="#fff" /> : <Text style={styles.btnText}>Save</Text>}
              </Pressable>
            </View>
          </Pressable>
        </Pressable>
      </Modal>
    </View>
  );
}

const styles = StyleSheet.create({
  card: {
    backgroundColor: theme.colors.surface,
    marginHorizontal: 16,
    marginTop: 8,
    marginBottom: 4,
    padding: 14,
    borderRadius: 14,
    borderWidth: 1,
    borderColor: theme.colors.border,
    gap: 8,
  },
  ctaCard: {
    backgroundColor: theme.colors.brandPrimary,
    borderColor: theme.colors.brandPrimary,
    flexDirection: "row",
    alignItems: "center",
    gap: 10,
  },
  warnCard: { flexDirection: "row", alignItems: "center", gap: 10 },
  connectedCard: { borderColor: theme.colors.border },
  iconCircle: {
    width: 32, height: 32, borderRadius: 16,
    backgroundColor: "#E3EBE6",
    alignItems: "center", justifyContent: "center",
  },
  title: { fontSize: 14, fontWeight: "700", color: theme.colors.onSurface ?? "#1d2421" },
  sub: { fontSize: 12, color: theme.colors.muted },
  subDim: { fontSize: 11, color: theme.colors.muted, marginTop: -4 },
  lastRow: { flexDirection: "row", alignItems: "center", gap: 6, marginTop: 4 },
  lastText: { fontSize: 11, color: theme.colors.muted, flex: 1 },
  openLink: { fontSize: 11, fontWeight: "700", color: theme.colors.brandPrimary },
  btn: {
    flexDirection: "row", alignItems: "center", justifyContent: "center", gap: 6,
    backgroundColor: theme.colors.brandPrimary, paddingVertical: 10, borderRadius: 10,
  },
  btnText: { color: "#fff", fontWeight: "700", fontSize: 13 },
  btnGhost: {
    flexDirection: "row", alignItems: "center", justifyContent: "center", gap: 6,
    paddingVertical: 10, borderRadius: 10, borderWidth: 1, borderColor: theme.colors.border,
    backgroundColor: theme.colors.surfaceSecondary,
  },
  btnGhostText: { color: theme.colors.brandPrimary, fontWeight: "700", fontSize: 13 },
  modalBackdrop: {
    flex: 1, backgroundColor: "rgba(0,0,0,0.4)", alignItems: "center", justifyContent: "center", padding: 24,
  },
  modalCard: {
    width: "100%", maxWidth: 420, backgroundColor: theme.colors.surface,
    borderRadius: 16, padding: 16, gap: 6,
  },
  input: {
    backgroundColor: theme.colors.surfaceSecondary,
    borderRadius: 10, padding: 12, color: theme.colors.onSurface,
    borderWidth: 1, borderColor: theme.colors.border, minHeight: 60,
  },
});
