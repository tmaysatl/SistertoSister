import { useEffect, useState } from "react";
import {
  View, Text, StyleSheet, Pressable, ActivityIndicator, ScrollView, Modal, Platform, Linking,
} from "react-native";
import { Image } from "expo-image";
import { Ionicons } from "@expo/vector-icons";
import { SafeAreaView } from "react-native-safe-area-context";
import { useLocalSearchParams } from "expo-router";
import SignatureScreen, { SignatureViewRef } from "react-native-signature-canvas";
import { useRef } from "react";
import { API_BASE } from "@/src/api/client";
import { theme, BRAND_NAME, LOGO_URL } from "@/src/theme";
import { PdfViewerModal } from "@/src/components/pdf/PdfViewerModal";

type Doc = { id: string; title: string; seq: number; is_template: boolean };
type Packet = {
  packet: {
    recipient_name: string;
    category: string;
    signed_ids: string[];
    completed_at?: string | null;
  };
  documents: Doc[];
};

export default function PacketView() {
  const { token } = useLocalSearchParams<{ token: string }>();
  const [data, setData] = useState<Packet | null>(null);
  const [loading, setLoading] = useState(true);
  const [signDoc, setSignDoc] = useState<Doc | null>(null);
  const sigRef = useRef<SignatureViewRef>(null);

  const [viewerDoc, setViewerDoc] = useState<Doc | null>(null);

  const load = async () => {
    setLoading(true);
    try {
      const res = await fetch(`${API_BASE}/packets/${token}`);
      if (res.ok) setData(await res.json());
    } finally { setLoading(false); }
  };

  useEffect(() => { if (token) load(); }, [token]);

  const open = (doc: Doc) => setViewerDoc(doc);

  const sign = async (sigB64: string) => {
    if (!signDoc) return;
    await fetch(`${API_BASE}/packets/${token}/sign/${signDoc.id}`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ signature_base64: sigB64 }),
    });
    setSignDoc(null);
    await load();
  };

  if (loading) {
    return (
      <SafeAreaView style={styles.center}>
        <ActivityIndicator color={theme.colors.brandPrimary} />
      </SafeAreaView>
    );
  }

  if (!data) {
    return (
      <SafeAreaView style={styles.center}>
        <Ionicons name="alert-circle-outline" size={48} color={theme.colors.error} />
        <Text style={styles.errTitle}>Packet not found</Text>
        <Text style={styles.errSub}>This link may have expired or been revoked.</Text>
      </SafeAreaView>
    );
  }

  const { packet, documents } = data;
  const signedSet = new Set(packet.signed_ids);
  const total = documents.length;
  const done = documents.filter((d) => signedSet.has(d.id)).length;

  return (
    <SafeAreaView style={styles.root}>
      <ScrollView contentContainerStyle={{ paddingBottom: 40 }}>
        <View style={styles.hero}>
          <Image source={{ uri: LOGO_URL }} style={styles.logo} contentFit="contain" />
          <Text style={styles.brand}>{BRAND_NAME}</Text>
          <Text style={styles.welcome}>Welcome, {packet.recipient_name}</Text>
          <Text style={styles.welcomeSub}>
            Please review and sign each of the {total} forms below.
          </Text>
          <View style={styles.progressTrack}>
            <View style={[styles.progressFill, { width: `${(done / total) * 100}%` }]} />
          </View>
          <Text style={styles.progressText}>
            {done} of {total} signed
          </Text>
        </View>

        {documents.map((d) => {
          const signed = signedSet.has(d.id);
          return (
            <View key={d.id} style={styles.row} testID={`packet-doc-${d.id}`}>
              <View style={[styles.numBadge, signed && { backgroundColor: theme.colors.success }]}>
                <Text style={styles.numText}>{String(d.seq).padStart(2, "0")}</Text>
              </View>
              <View style={{ flex: 1 }}>
                <Text style={styles.docTitle} numberOfLines={2}>
                  {d.title.replace(/^\d{2} - /, "")}
                </Text>
                <Text style={[styles.docStatus, { color: signed ? theme.colors.success : theme.colors.muted }]}>
                  {signed ? "✓ Signed" : "Awaiting signature"}
                </Text>
              </View>
              <Pressable onPress={() => open(d)} style={styles.iconBtn}>
                <Ionicons name="eye-outline" size={18} color={theme.colors.brandPrimary} />
              </Pressable>
              {!signed && (
                <Pressable
                  onPress={() => setSignDoc(d)}
                  style={[styles.iconBtn, { backgroundColor: theme.colors.brandPrimary }]}
                  testID={`packet-sign-${d.id}`}
                >
                  <Ionicons name="create-outline" size={18} color="#fff" />
                </Pressable>
              )}
            </View>
          );
        })}

        {done === total && (
          <View style={styles.doneCard}>
            <Ionicons name="checkmark-circle" size={48} color={theme.colors.success} />
            <Text style={styles.doneTitle}>All set!</Text>
            <Text style={styles.doneSub}>You've signed every form in the packet.</Text>
          </View>
        )}
      </ScrollView>

      <PdfViewerModal
        visible={!!viewerDoc}
        onClose={() => setViewerDoc(null)}
        title={viewerDoc?.title?.replace(/^\d{2} - /, "") || ""}
        url={viewerDoc ? `${API_BASE}/packets/${token}/document/${viewerDoc.id}` : null}
        publicSignPath={viewerDoc ? `/packets/${token}/sign/${viewerDoc.id}` : null}
        onSigned={load}
      />

      <Modal visible={!!signDoc} transparent animationType="slide" onRequestClose={() => setSignDoc(null)}>
        <View style={styles.modalRoot}>
          <Pressable style={styles.backdrop} onPress={() => setSignDoc(null)} />
          <View style={styles.sheet}>
            <View style={styles.sheetHandle} />
            <Text style={styles.sheetTitle} numberOfLines={1}>
              Sign: {signDoc?.title.replace(/^\d{2} - /, "")}
            </Text>
            <View style={{ flex: 1, borderWidth: 1, borderColor: theme.colors.border, borderRadius: 12, overflow: "hidden" }}>
              <SignatureScreen
                ref={sigRef}
                onOK={sign}
                webStyle={`.m-signature-pad--footer {display: none;} .m-signature-pad {box-shadow:none; border:none;} body,html { background:#fff; }`}
                descriptionText=""
              />
            </View>
            <View style={{ flexDirection: "row", gap: 10, marginTop: 12 }}>
              <Pressable
                onPress={() => sigRef.current?.clearSignature()}
                style={[styles.btn, { flex: 1, backgroundColor: theme.colors.surfaceTertiary }]}
              >
                <Text style={[styles.btnText, { color: theme.colors.onSurface }]}>Clear</Text>
              </Pressable>
              <Pressable
                onPress={() => sigRef.current?.readSignature()}
                style={[styles.btn, { flex: 1 }]}
              >
                <Text style={styles.btnText}>Sign & Submit</Text>
              </Pressable>
            </View>
          </View>
        </View>
      </Modal>
    </SafeAreaView>
  );
}

const styles = StyleSheet.create({
  root: { flex: 1, backgroundColor: theme.colors.surface },
  center: { flex: 1, alignItems: "center", justifyContent: "center", gap: 8 },
  errTitle: { fontSize: 20, fontWeight: "700", color: theme.colors.onSurface, marginTop: 8 },
  errSub: { fontSize: 13, color: theme.colors.muted },
  hero: { padding: 24, gap: 6, alignItems: "flex-start", backgroundColor: theme.colors.surfaceSecondary, borderBottomWidth: 1, borderBottomColor: theme.colors.divider },
  logo: { width: 80, height: 80, marginLeft: -8 },
  brand: { fontSize: 18, fontWeight: "700", color: theme.colors.brandPrimary },
  welcome: { fontSize: 26, fontWeight: "800", color: theme.colors.onSurface, marginTop: 8, letterSpacing: -0.5 },
  welcomeSub: { fontSize: 14, color: theme.colors.muted },
  progressTrack: { height: 6, backgroundColor: theme.colors.surfaceTertiary, borderRadius: 999, marginTop: 14, alignSelf: "stretch", overflow: "hidden" },
  progressFill: { height: "100%", backgroundColor: theme.colors.success, borderRadius: 999 },
  progressText: { fontSize: 12, color: theme.colors.muted, marginTop: 6, fontWeight: "600" },
  row: { flexDirection: "row", alignItems: "center", gap: 10, padding: 14, marginHorizontal: 16, marginTop: 10, backgroundColor: theme.colors.surfaceSecondary, borderRadius: 14, borderWidth: 1, borderColor: theme.colors.border },
  numBadge: { width: 36, height: 36, borderRadius: 10, backgroundColor: theme.colors.brandTertiary, alignItems: "center", justifyContent: "center" },
  numText: { fontWeight: "800", color: theme.colors.onSurface, fontSize: 13 },
  docTitle: { fontSize: 14, fontWeight: "600", color: theme.colors.onSurface },
  docStatus: { fontSize: 11, fontWeight: "600", marginTop: 2 },
  iconBtn: { width: 36, height: 36, borderRadius: 10, alignItems: "center", justifyContent: "center", backgroundColor: theme.colors.brandTertiary },
  doneCard: { margin: 16, padding: 24, alignItems: "center", backgroundColor: theme.colors.brandTertiary, borderRadius: 16, gap: 6 },
  doneTitle: { fontSize: 20, fontWeight: "700", color: theme.colors.brandPrimary, marginTop: 6 },
  doneSub: { fontSize: 13, color: theme.colors.muted },
  modalRoot: { flex: 1, justifyContent: "flex-end" },
  backdrop: { ...StyleSheet.absoluteFillObject, backgroundColor: "rgba(0,0,0,0.5)" },
  sheet: { backgroundColor: theme.colors.surface, borderTopLeftRadius: 24, borderTopRightRadius: 24, padding: 20, paddingBottom: 32, gap: 10, height: 460 },
  sheetHandle: { width: 40, height: 4, borderRadius: 2, backgroundColor: theme.colors.border, alignSelf: "center" },
  sheetTitle: { fontSize: 18, fontWeight: "700", color: theme.colors.onSurface },
  btn: { backgroundColor: theme.colors.brandPrimary, padding: 14, borderRadius: 12, alignItems: "center" },
  btnText: { color: "#fff", fontWeight: "700", fontSize: 14 },
});
