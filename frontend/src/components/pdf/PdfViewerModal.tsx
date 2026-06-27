import { useEffect, useRef, useState } from "react";
import { Modal, View, Text, Pressable, StyleSheet, ActivityIndicator, Platform } from "react-native";
import { WebView } from "react-native-webview";
import SignatureScreen, { SignatureViewRef } from "react-native-signature-canvas";
import { Ionicons } from "@expo/vector-icons";
import { SafeAreaView } from "react-native-safe-area-context";
import { API_BASE, getAuthToken } from "../../api/client";
import { theme } from "../../theme";

type Props = {
  visible: boolean;
  onClose: () => void;
  title: string;
  /** Authed backend GET path that returns the PDF. */
  path?: string | null;
  /** Or a fully built public URL. */
  url?: string | null;
  /** Authed POST path to submit `{ signature_base64 }`. If set, shows Sign action. */
  signPath?: string | null;
  /** Or public POST path (no auth). */
  publicSignPath?: string | null;
  /** Fires after a successful sign so parent can refresh. */
  onSigned?: () => void;
};

export function PdfViewerModal({
  visible, onClose, title, path, url, signPath, publicSignPath, onSigned,
}: Props) {
  const [blobUrl, setBlobUrl] = useState<string | null>(null);
  const [base64, setBase64] = useState<string | null>(null);
  const [loading, setLoading] = useState(false);
  const [signing, setSigning] = useState(false);
  const [submitting, setSubmitting] = useState(false);
  const sigRef = useRef<SignatureViewRef>(null);
  const [refreshNonce, setRefreshNonce] = useState(0);

  const canSign = !!(signPath || publicSignPath);

  useEffect(() => {
    if (!visible) {
      setBlobUrl(null); setBase64(null); setSigning(false); return;
    }
    (async () => {
      setLoading(true);
      try {
        const target = url || `${API_BASE}${path}`;
        const headers: Record<string, string> = {};
        if (path) {
          const t = await getAuthToken();
          if (t) headers.Authorization = `Bearer ${t}`;
        }
        const res = await fetch(target, { headers });
        if (!res.ok) {
          // Surface a useful error instead of rendering an HTML error body
          // as if it were a PDF (that was the source of "wrong document"
          // bug reports — a 401/500 HTML response embedded in the iframe).
          throw new Error(`Document fetch failed: ${res.status}`);
        }
        const ct = res.headers.get("content-type") || "";
        if (!ct.includes("pdf") && !ct.includes("octet-stream")) {
          throw new Error(`Unexpected content-type: ${ct || "unknown"}`);
        }
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
      } catch (e) {
        console.log("pdf modal load failed:", e);
      } finally { setLoading(false); }
    })();
    return () => { if (blobUrl) URL.revokeObjectURL(blobUrl); };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [visible, path, url, refreshNonce]);

  const submitSignature = async (sig: string) => {
    setSubmitting(true);
    try {
      const target = signPath
        ? `${API_BASE}${signPath}`
        : `${API_BASE}${publicSignPath}`;
      const headers: Record<string, string> = { "Content-Type": "application/json" };
      if (signPath) {
        const t = await getAuthToken();
        if (t) headers.Authorization = `Bearer ${t}`;
      }
      const res = await fetch(target!, {
        method: "POST", headers,
        body: JSON.stringify({ signature_base64: sig }),
      });
      if (!res.ok) throw new Error(`Sign failed: ${res.status}`);
      setSigning(false);
      onSigned?.();
      setRefreshNonce((n) => n + 1);
    } catch (e) {
      console.log("sign submit", e);
    } finally {
      setSubmitting(false);
    }
  };

  return (
    <Modal visible={visible} animationType="slide" onRequestClose={onClose}>
      <SafeAreaView edges={["top"]} style={styles.root}>
        <View style={styles.header}>
          <Pressable testID="pdf-close" onPress={onClose} hitSlop={10} style={styles.iconBtn}>
            <Ionicons name="close" size={22} color={theme.colors.onSurface} />
          </Pressable>
          <Text style={styles.title} numberOfLines={1}>{title}</Text>
          <View style={{ flexDirection: "row", gap: 6 }}>
            {canSign && !signing && (
              <Pressable
                testID="pdf-sign-toggle"
                onPress={() => setSigning(true)}
                hitSlop={10}
                style={[styles.iconBtn, { backgroundColor: theme.colors.brandPrimary }]}
              >
                <Ionicons name="create-outline" size={18} color="#fff" />
              </Pressable>
            )}
            {Platform.OS === "web" && blobUrl ? (
              <Pressable
                testID="pdf-open-tab"
                onPress={() => window.open(blobUrl, "_blank")}
                hitSlop={10}
                style={styles.iconBtn}
              >
                <Ionicons name="open-outline" size={18} color={theme.colors.brandPrimary} />
              </Pressable>
            ) : null}
          </View>
        </View>

        {loading && (
          <View style={styles.loadingOverlay}>
            <ActivityIndicator color={theme.colors.brandPrimary} />
            <Text style={styles.loadingText}>Loading document…</Text>
          </View>
        )}

        <View style={{ flex: 1, position: "relative" }}>
          {Platform.OS === "web" && blobUrl && (
            <View style={{ flex: 1, backgroundColor: "#525659" }}>
              {/* @ts-ignore */}
              <iframe
                src={blobUrl}
                title={title}
                style={{
                  position: "absolute", top: 0, left: 0, right: 0, bottom: 0,
                  width: "100%", height: "100%", border: 0,
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
                  <style>html,body{margin:0;background:#525659;height:100%;}embed{display:block;width:100%;height:100%;}</style>
                  </head><body>
                  <embed type="application/pdf" src="data:application/pdf;base64,${base64}"/>
                  </body></html>`,
              }}
              style={{ flex: 1 }}
            />
          )}

          {signing && (
            <View style={styles.signOverlay}>
              <View style={styles.signCard}>
                <Text style={styles.signTitle}>Draw your signature</Text>
                <Text style={styles.signSub}>It will be applied to the last page of the document.</Text>
                <View style={{ flex: 1, borderRadius: 12, overflow: "hidden", borderWidth: 1, borderColor: theme.colors.border, marginTop: 8 }}>
                  <SignatureScreen
                    ref={sigRef}
                    onOK={submitSignature}
                    onEmpty={() => console.log("empty sig")}
                    webStyle={`.m-signature-pad--footer{display:none;}.m-signature-pad{box-shadow:none;border:none;}body,html{background:#fff;}`}
                    descriptionText=""
                  />
                </View>
                <View style={{ flexDirection: "row", gap: 8, marginTop: 10 }}>
                  <Pressable
                    testID="sig-cancel"
                    onPress={() => setSigning(false)}
                    style={[styles.btn, { flex: 1, backgroundColor: theme.colors.surfaceTertiary }]}
                  >
                    <Text style={[styles.btnText, { color: theme.colors.onSurface }]}>Cancel</Text>
                  </Pressable>
                  <Pressable
                    testID="sig-clear"
                    onPress={() => sigRef.current?.clearSignature()}
                    style={[styles.btn, { flex: 1, backgroundColor: theme.colors.surfaceTertiary }]}
                  >
                    <Text style={[styles.btnText, { color: theme.colors.onSurface }]}>Clear</Text>
                  </Pressable>
                  <Pressable
                    testID="sig-submit"
                    onPress={() => sigRef.current?.readSignature()}
                    disabled={submitting}
                    style={[styles.btn, { flex: 1.4, opacity: submitting ? 0.6 : 1 }]}
                  >
                    {submitting ? <ActivityIndicator color="#fff" /> : <Text style={styles.btnText}>Sign & Save</Text>}
                  </Pressable>
                </View>
              </View>
            </View>
          )}
        </View>
      </SafeAreaView>
    </Modal>
  );
}

const styles = StyleSheet.create({
  root: { flex: 1, backgroundColor: theme.colors.surface },
  header: { flexDirection: "row", alignItems: "center", justifyContent: "space-between", paddingHorizontal: 16, paddingVertical: 12, borderBottomWidth: 1, borderBottomColor: theme.colors.divider, backgroundColor: theme.colors.surfaceSecondary, gap: 8 },
  iconBtn: { width: 36, height: 36, borderRadius: 18, backgroundColor: theme.colors.surfaceTertiary, alignItems: "center", justifyContent: "center" },
  title: { flex: 1, textAlign: "center", fontWeight: "700", fontSize: 14, color: theme.colors.onSurface, marginHorizontal: 8 },
  loadingOverlay: { position: "absolute", top: 70, left: 0, right: 0, alignItems: "center", gap: 6, padding: 12, zIndex: 5 },
  loadingText: { color: theme.colors.muted, fontSize: 12 },
  signOverlay: { position: "absolute", left: 0, right: 0, bottom: 0, top: 0, backgroundColor: "rgba(0,0,0,0.55)", justifyContent: "flex-end", padding: 12 },
  signCard: { backgroundColor: theme.colors.surface, borderRadius: 18, padding: 14, height: 380 },
  signTitle: { fontSize: 16, fontWeight: "700", color: theme.colors.onSurface },
  signSub: { fontSize: 12, color: theme.colors.muted, marginTop: 2 },
  btn: { padding: 12, borderRadius: 12, backgroundColor: theme.colors.brandPrimary, alignItems: "center", justifyContent: "center" },
  btnText: { color: "#fff", fontWeight: "700", fontSize: 13 },
});
