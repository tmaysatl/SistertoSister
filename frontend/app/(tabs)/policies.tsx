import { useCallback, useState } from "react";
import {
  View, Text, StyleSheet, Pressable, TextInput, Modal,
  ActivityIndicator, RefreshControl, KeyboardAvoidingView, Platform, FlatList,
} from "react-native";
import { Ionicons } from "@expo/vector-icons";
import { SafeAreaView } from "react-native-safe-area-context";
import * as DocumentPicker from "expo-document-picker";
import { useFocusEffect } from "expo-router";
import { useAuth } from "@/src/context/AuthContext";
import { apiGet, apiPost, apiDelete } from "@/src/api/client";
import { theme } from "@/src/theme";

type DocItem = {
  id: string;
  title: string;
  category: string;
  notes?: string;
  uploaded_at: string;
  seq?: number | null;
  is_template?: boolean;
  file_base64?: string | null;
};

export default function Policies() {
  const { user } = useAuth();
  const isAdmin = user?.role === "admin";

  const [docs, setDocs] = useState<DocItem[]>([]);
  const [loading, setLoading] = useState(false);
  const [showAdd, setShowAdd] = useState(false);
  const [title, setTitle] = useState("");
  const [notes, setNotes] = useState("");
  const [picked, setPicked] = useState<{ base64: string; mime: string; name: string } | null>(null);
  const [submitting, setSubmitting] = useState(false);
  const [seeding, setSeeding] = useState(false);

  const load = useCallback(async () => {
    setLoading(true);
    try {
      const d = await apiGet<DocItem[]>("/documents?category=policy");
      setDocs(d);
    } catch (e) { console.log(e); }
    finally { setLoading(false); }
  }, []);

  useFocusEffect(useCallback(() => { load(); }, [load]));

  const pickFile = async () => {
    const res = await DocumentPicker.getDocumentAsync({ base64: true });
    if (res.canceled || !res.assets?.length) return;
    const a = res.assets[0];
    let base64 = a.base64;
    if (!base64 && a.uri) {
      try {
        const r = await fetch(a.uri); const b = await r.blob();
        base64 = await new Promise<string>((resolve, reject) => {
          const fr = new FileReader();
          fr.onloadend = () => resolve(((fr.result as string) || "").split(",")[1] || "");
          fr.onerror = reject;
          fr.readAsDataURL(b);
        });
      } catch { /* ignore */ }
    }
    if (!base64) return;
    setPicked({ base64, mime: a.mimeType || "application/pdf", name: a.name });
  };

  const submit = async () => {
    if (!title.trim()) return;
    setSubmitting(true);
    try {
      await apiPost("/documents", {
        title: title.trim(),
        category: "policy",
        notes,
        owner_type: "agency",
        file_base64: picked?.base64 || null,
        mime_type: picked?.mime || "application/pdf",
      });
      setShowAdd(false);
      setTitle(""); setNotes(""); setPicked(null);
      load();
    } finally { setSubmitting(false); }
  };

  const seed = async () => {
    setSeeding(true);
    try {
      await apiPost("/documents/seed-templates", {});
      load();
    } finally { setSeeding(false); }
  };

  return (
    <SafeAreaView edges={["top"]} style={styles.root}>
      <View style={styles.header}>
        <View>
          <Text style={styles.title}>Policies & Procedures</Text>
          <Text style={styles.subtitle}>{docs.length} document{docs.length === 1 ? "" : "s"}</Text>
        </View>
        {isAdmin && (
          <View style={{ flexDirection: "row", gap: 8 }}>
            <Pressable
              testID="policies-seed-button"
              onPress={seed}
              disabled={seeding}
              style={styles.seedBtn}
            >
              {seeding ? (
                <ActivityIndicator size="small" color={theme.colors.brandPrimary} />
              ) : (
                <>
                  <Ionicons name="download-outline" size={16} color={theme.colors.brandPrimary} />
                  <Text style={styles.seedBtnText}>Seed Stubs</Text>
                </>
              )}
            </Pressable>
            <Pressable
              testID="add-policy-button"
              onPress={() => setShowAdd(true)}
              style={styles.addBtn}
            >
              <Ionicons name="add" size={22} color="#fff" />
            </Pressable>
          </View>
        )}
      </View>

      <FlatList
        data={docs}
        keyExtractor={(i) => i.id}
        contentContainerStyle={{ padding: 20, gap: 10, paddingBottom: 40 }}
        refreshControl={<RefreshControl refreshing={loading} onRefresh={load} />}
        ListEmptyComponent={
          loading ? null : (
            <View style={styles.empty}>
              <View style={styles.emptyIcon}>
                <Ionicons name="shield-checkmark-outline" size={36} color={theme.colors.brandPrimary} />
              </View>
              <Text style={styles.emptyTitle}>No policies yet</Text>
              <Text style={styles.emptySubtitle}>
                {isAdmin
                  ? 'Tap "Seed Stubs" to load standard P&P titles or "+" to upload your own.'
                  : "Your admin hasn't published policies yet."}
              </Text>
            </View>
          )
        }
        renderItem={({ item }) => (
          <View style={styles.card} testID={`policy-${item.id}`}>
            <View style={styles.cardIcon}>
              <Ionicons name="shield-checkmark-outline" size={20} color={theme.colors.brandPrimary} />
            </View>
            <View style={{ flex: 1 }}>
              <Text style={styles.cardTitle}>{item.title}</Text>
              <Text style={styles.cardMeta}>
                {item.file_base64 ? "PDF attached" : "Pending upload"} · {new Date(item.uploaded_at).toLocaleDateString()}
              </Text>
              {!!item.notes && <Text style={styles.cardNotes} numberOfLines={2}>{item.notes}</Text>}
            </View>
            {isAdmin && (
              <Pressable
                testID={`delete-policy-${item.id}`}
                onPress={() => apiDelete(`/documents/${item.id}`).then(load)}
                hitSlop={10}
              >
                <Ionicons name="trash-outline" size={18} color={theme.colors.error} />
              </Pressable>
            )}
          </View>
        )}
      />

      <Modal visible={showAdd} transparent animationType="slide" onRequestClose={() => setShowAdd(false)}>
        <KeyboardAvoidingView
          behavior={Platform.OS === "ios" ? "padding" : undefined}
          style={{ flex: 1, justifyContent: "flex-end" }}
        >
          <Pressable style={StyleSheet.absoluteFill} onPress={() => setShowAdd(false)} />
          <View style={styles.sheet}>
            <View style={styles.sheetHandle} />
            <Text style={styles.sheetTitle}>New policy</Text>
            <TextInput
              testID="policy-title-input"
              value={title} onChangeText={setTitle}
              placeholder="Policy title (e.g. Bloodborne Pathogens)"
              placeholderTextColor={theme.colors.muted}
              style={styles.input}
            />
            <TextInput
              value={notes} onChangeText={setNotes}
              placeholder="Description / revision date"
              placeholderTextColor={theme.colors.muted}
              multiline
              style={[styles.input, { minHeight: 80, textAlignVertical: "top" }]}
            />
            <Pressable testID="policy-pick-file" onPress={pickFile} style={styles.pickBtn}>
              <Ionicons name="document-attach-outline" size={18} color={theme.colors.brandPrimary} />
              <Text style={styles.pickBtnText}>{picked ? picked.name : "Attach PDF (optional)"}</Text>
            </Pressable>
            <Pressable
              testID="submit-policy-button"
              onPress={submit}
              disabled={submitting || !title.trim()}
              style={[styles.primaryBtn, (!title.trim() || submitting) && { opacity: 0.5 }]}
            >
              {submitting ? <ActivityIndicator color="#fff" /> : <Text style={styles.primaryBtnText}>Publish</Text>}
            </Pressable>
          </View>
        </KeyboardAvoidingView>
      </Modal>
    </SafeAreaView>
  );
}

const styles = StyleSheet.create({
  root: { flex: 1, backgroundColor: theme.colors.surface },
  header: { paddingHorizontal: 20, paddingTop: 8, paddingBottom: 12, flexDirection: "row", justifyContent: "space-between", alignItems: "center" },
  title: { fontSize: 26, fontWeight: "700", color: theme.colors.onSurface },
  subtitle: { fontSize: 12, color: theme.colors.muted, marginTop: 2 },
  addBtn: { width: 40, height: 40, borderRadius: 12, backgroundColor: theme.colors.brandPrimary, alignItems: "center", justifyContent: "center" },
  seedBtn: { flexDirection: "row", alignItems: "center", gap: 4, paddingHorizontal: 12, height: 40, borderRadius: 12, backgroundColor: theme.colors.brandTertiary, borderWidth: 1, borderColor: theme.colors.brandPrimary },
  seedBtnText: { color: theme.colors.brandPrimary, fontWeight: "700", fontSize: 12 },
  card: { flexDirection: "row", gap: 12, alignItems: "center", backgroundColor: theme.colors.surfaceSecondary, padding: 14, borderRadius: 14, borderWidth: 1, borderColor: theme.colors.border },
  cardIcon: { width: 44, height: 44, borderRadius: 12, backgroundColor: theme.colors.brandTertiary, alignItems: "center", justifyContent: "center" },
  cardTitle: { fontSize: 15, fontWeight: "600", color: theme.colors.onSurface },
  cardMeta: { fontSize: 11, color: theme.colors.muted, marginTop: 2, fontWeight: "600", letterSpacing: 0.4 },
  cardNotes: { fontSize: 12, color: theme.colors.onSurfaceTertiary, marginTop: 4 },
  empty: { alignItems: "center", paddingVertical: 80, gap: 8 },
  emptyIcon: { width: 80, height: 80, borderRadius: 24, backgroundColor: theme.colors.brandTertiary, alignItems: "center", justifyContent: "center" },
  emptyTitle: { fontSize: 16, fontWeight: "700", color: theme.colors.onSurface, marginTop: 8 },
  emptySubtitle: { fontSize: 13, color: theme.colors.muted, textAlign: "center", paddingHorizontal: 40 },
  sheet: { backgroundColor: theme.colors.surface, borderTopLeftRadius: 24, borderTopRightRadius: 24, padding: 20, paddingBottom: 32, gap: 12 },
  sheetHandle: { width: 40, height: 4, borderRadius: 2, backgroundColor: theme.colors.border, alignSelf: "center" },
  sheetTitle: { fontSize: 20, fontWeight: "700", color: theme.colors.onSurface },
  input: { backgroundColor: theme.colors.surfaceSecondary, borderWidth: 1, borderColor: theme.colors.border, borderRadius: 12, paddingHorizontal: 14, paddingVertical: 12, fontSize: 15, color: theme.colors.onSurface },
  pickBtn: { flexDirection: "row", alignItems: "center", gap: 8, borderWidth: 1, borderStyle: "dashed", borderColor: theme.colors.brandPrimary, backgroundColor: theme.colors.brandTertiary, padding: 14, borderRadius: 12 },
  pickBtnText: { color: theme.colors.brandPrimary, fontWeight: "600", fontSize: 14, flex: 1 },
  primaryBtn: { backgroundColor: theme.colors.brandPrimary, padding: 16, borderRadius: 12, alignItems: "center", marginTop: 6 },
  primaryBtnText: { color: "#fff", fontWeight: "700", fontSize: 15 },
});
