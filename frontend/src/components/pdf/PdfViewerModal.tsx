import { useEffect, useState } from "react";
import { Modal, View, Text, Pressable, StyleSheet, ActivityIndicator, Platform } from "react-native";
import { WebView } from "react-native-webview";
import { Ionicons } from "@expo/vector-icons";
import { SafeAreaView } from "react-native-safe-area-context";
import AsyncStorage from "@react-native-async-storage/async-storage";
import { API_BASE } from "../../api/client";
import { theme } from "../../theme";

type Props = {
  visible: boolean;
  onClose: () => void;
  title: string;
  /** Authed backend path, e.g. /documents/abc/stamped */
  path?: string | null;
  /** Or a fully built public URL */
  url?: string | null;
};

export function PdfViewerModal({ visible, onClose, title, path, url }: Props) {
  const [blobUrl, setBlobUrl] = useState<string | null>(null);
  const [base64, setBase64] = useState<string | null>(null);
  const [loading, setLoading] = useState(false);

  useEffect(() => {
    if (!visible) {
      setBlobUrl(null); setBase64(null); return;
    }
    (async () => {
      setLoading(true);
      try {
        const target = url || `${API_BASE}${path}`;
        const headers: Record<string, string> = {};
        if (path) {
          const t = await AsyncStorage.getItem("userToken");
          if (t) headers.Authorization = `Bearer ${t}`;
        }
        const res = await fetch(target, { headers });
        const blob = await res.blob();
        if (Platform.OS === "web") {
          setBlobUrl(URL.createObjectURL(blob));
        } else {
          const r = new FileReader();
          r.onloadend = () => {
            const s = (r.result as string) || "";
            setBase64(s.split(",")[1] || null);
          };
          r.readAsDataURL(blob);
        }
      } catch (e) { console.log("pdf modal load", e); }
      finally { setLoading(false); }
    })();
    return () => {
      if (blobUrl) URL.revokeObjectURL(blobUrl);
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [visible, path, url]);

  return (
    <Modal visible={visible} animationType="slide" onRequestClose={onClose}>
      <SafeAreaView edges={["top"]} style={styles.root}>
        <View style={styles.header}>
          <Pressable testID="pdf-close" onPress={onClose} hitSlop={10} style={styles.closeBtn}>
            <Ionicons name="close" size={22} color={theme.colors.onSurface} />
          </Pressable>
          <Text style={styles.title} numberOfLines={1}>{title}</Text>
          {Platform.OS === "web" && blobUrl ? (
            <Pressable
              testID="pdf-open-tab"
              onPress={() => window.open(blobUrl, "_blank")}
              hitSlop={10}
              style={styles.closeBtn}
            >
              <Ionicons name="open-outline" size={18} color={theme.colors.brandPrimary} />
            </Pressable>
          ) : (
            <View style={{ width: 36 }} />
          )}
        </View>

        {loading && (
          <View style={styles.loadingOverlay}>
            <ActivityIndicator color={theme.colors.brandPrimary} />
            <Text style={styles.loadingText}>Loading document…</Text>
          </View>
        )}

        {Platform.OS === "web" && blobUrl && (
          <View style={{ flex: 1, backgroundColor: "#525659" }}>
            {/* @ts-ignore — iframe is web-only */}
            <iframe
              src={blobUrl}
              title={title}
              style={{
                position: "absolute",
                top: 0, left: 0, right: 0, bottom: 0,
                width: "100%", height: "100%",
                border: 0,
              }}
            />
          </View>
        )}

        {Platform.OS !== "web" && base64 && (
          <WebView
            originWhitelist={["*"]}
            source={{
              html: `
                <!doctype html><html><head>
                <meta name="viewport" content="width=device-width, initial-scale=1, maximum-scale=5">
                <style>html,body{margin:0;background:#222;height:100%;}embed{display:block;width:100%;height:100%;}</style>
                </head><body>
                <embed type="application/pdf" src="data:application/pdf;base64,${base64}"/>
                </body></html>`,
            }}
            style={{ flex: 1 }}
          />
        )}
      </SafeAreaView>
    </Modal>
  );
}

const styles = StyleSheet.create({
  root: { flex: 1, backgroundColor: theme.colors.surface },
  header: {
    flexDirection: "row", alignItems: "center", justifyContent: "space-between",
    paddingHorizontal: 16, paddingVertical: 12, borderBottomWidth: 1,
    borderBottomColor: theme.colors.divider, backgroundColor: theme.colors.surfaceSecondary,
  },
  closeBtn: { width: 36, height: 36, borderRadius: 18, backgroundColor: theme.colors.surfaceTertiary, alignItems: "center", justifyContent: "center" },
  title: { flex: 1, textAlign: "center", fontWeight: "700", fontSize: 14, color: theme.colors.onSurface, marginHorizontal: 8 },
  loadingOverlay: { position: "absolute", top: 70, left: 0, right: 0, alignItems: "center", gap: 6, padding: 12 },
  loadingText: { color: theme.colors.muted, fontSize: 12 },
});
