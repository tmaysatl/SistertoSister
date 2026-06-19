import { useCallback, useMemo, useState } from "react";
import {
  View, Text, StyleSheet, ScrollView, Pressable, TextInput, Modal,
  ActivityIndicator, RefreshControl, KeyboardAvoidingView, Platform, FlatList,
} from "react-native";
import { Ionicons } from "@expo/vector-icons";
import { SafeAreaView } from "react-native-safe-area-context";
import * as DocumentPicker from "expo-document-picker";
import { useFocusEffect } from "expo-router";
import { useAuth } from "@/src/context/AuthContext";
import { apiGet, apiPost, apiDelete } from "@/src/api/client";
import { theme } from "@/src/theme";

type DocCategory = "client" | "caregiver" | "onboarding" | "training" | "policy";

type DocItem = {
  id: string;
  title: string;
  category: DocCategory;
  notes?: string;
  uploaded_at: string;
  file_base64?: string | null;
  mime_type?: string;
};

const CATEGORIES: { key: DocCategory | "all"; label: string; icon: any }[] = [
  { key: "all", label: "All", icon: "albums-outline" },
  { key: "client", label: "Clients", icon: "people-outline" },
  { key: "caregiver", label: "Caregivers", icon: "medkit-outline" },
  { key: "onboarding", label: "Onboarding", icon: "clipboard-outline" },
  { key: "training", label: "Training", icon: "school-outline" },
  { key: "policy", label: "Policies", icon: "shield-checkmark-outline" },
];

export default function Documents() {
  const { user } = useAuth();
  const [docs, setDocs] = useState<DocItem[]>([]);
  const [filter, setFilter] = useState<DocCategory | "all">("all");
  const [loading, setLoading] = useState(false);
  const [showAdd, setShowAdd] = useState(false);
  const [title, setTitle] = useState("");
  const [notes, setNotes] = useState("");
  const [category, setCategory] = useState<DocCategory>("policy");
  const [pickedFile, setPickedFile] = useState<{ base64: string; mime: string; name: string } | null>(null);
  const [submitting, setSubmitting] = useState(false);

  const load = useCallback(async () => {
    setLoading(true);
    try {
      const d = await apiGet<DocItem[]>("/documents");
      setDocs(d);
    } catch (e) {
      console.log(e);
    } finally {
      setLoading(false);
    }
  }, []);

  useFocusEffect(useCallback(() => { load(); }, [load]));

  const filtered = useMemo(
    () => (filter === "all" ? docs : docs.filter((d) => d.category === filter)),
    [docs, filter]
  );

  const pickFile = async () => {
    const res = await DocumentPicker.getDocumentAsync({ base64: true, copyToCacheDirectory: true });
    if (res.canceled || !res.assets?.length) return;
    const a = res.assets[0];
    let base64 = a.base64;
    if (!base64 && a.uri) {
      try {
        const r = await fetch(a.uri);
        const b = await r.blob();
        base64 = await new Promise<string>((resolve, reject) => {
          const fr = new FileReader();
          fr.onloadend = () => {
            const result = (fr.result as string) || "";
            resolve(result.split(",")[1] || "");
          };
          fr.onerror = reject;
          fr.readAsDataURL(b);
        });
      } catch (e) { console.log("read error", e); }
    }
    if (!base64) return;
    setPickedFile({ base64, mime: a.mimeType || "application/octet-stream", name: a.name });
  };

  const submit = async () => {
    if (!title.trim()) return;
    setSubmitting(true);
    try {
      await apiPost("/documents", {
        title: title.trim(),
        category,
        notes,
        owner_type: "agency",
        file_base64: pickedFile?.base64 || null,
        mime_type: pickedFile?.mime || "application/pdf",
      });
      setShowAdd(false);
      setTitle(""); setNotes(""); setPickedFile(null); setCategory("policy");
      await load();
    } catch (e) {
      console.log(e);
    } finally {
      setSubmitting(false);
    }
  };

  const removeDoc = async (id: string) => {
    await apiDelete(`/documents/${id}`);
    load();
  };

  return (
    <SafeAreaView edges={["top"]} style={styles.root}>
      <View style={styles.header}>
        <View>
          <Text style={styles.title}>Document Vault</Text>
          <Text style={styles.subtitle}>{docs.length} item{docs.length === 1 ? "" : "s"}</Text>
        </View>
        {user?.role === "admin" && (
          <Pressable testID="add-document-button" onPress={() => setShowAdd(true)} style={styles.addBtn}>
            <Ionicons name="add" size={22} color="#fff" />
          </Pressable>
        )}
      </View>

      <View style={styles.chipsWrap}>
        <ScrollView
          horizontal showsHorizontalScrollIndicator={false}
          contentContainerStyle={styles.chipsRow}
        >
          {CATEGORIES.map((c) => {
            const active = filter === c.key;
            return (
              <Pressable
                key={c.key}
                testID={`chip-${c.key}`}
                onPress={() => setFilter(c.key)}
                style={[styles.chip, active && styles.chipActive]}
              >
                <Ionicons
                  name={c.icon}
                  size={14}
                  color={active ? "#fff" : theme.colors.onSurfaceTertiary}
                />
                <Text style={[styles.chipText, active && styles.chipTextActive]}>{c.label}</Text>
              </Pressable>
            );
          })}
        </ScrollView>
      </View>

      <FlatList
        data={filtered}
        keyExtractor={(i) => i.id}
        contentContainerStyle={{ paddingHorizontal: 20, paddingTop: 8, paddingBottom: 32 }}
        refreshControl={<RefreshControl refreshing={loading} onRefresh={load} />}
        ItemSeparatorComponent={() => <View style={{ height: 10 }} />}
        ListEmptyComponent={
          loading ? null : (
            <View style={styles.empty}>
              <View style={styles.emptyIcon}>
                <Ionicons name="folder-open-outline" size={40} color={theme.colors.brandPrimary} />
              </View>
              <Text style={styles.emptyTitle}>No documents yet</Text>
              <Text style={styles.emptySubtitle}>
                {user?.role === "admin"
                  ? "Tap + to upload your first compliance document"
                  : "Your admin will share documents here"}
              </Text>
            </View>
          )
        }
        renderItem={({ item }) => (
          <View style={styles.docCard} testID={`doc-${item.id}`}>
            <View style={styles.docIcon}>
              <Ionicons name="document-text-outline" size={20} color={theme.colors.brandPrimary} />
            </View>
            <View style={{ flex: 1 }}>
              <Text style={styles.docTitle} numberOfLines={1}>{item.title}</Text>
              <Text style={styles.docMeta}>
                {item.category.toUpperCase()} · {new Date(item.uploaded_at).toLocaleDateString()}
              </Text>
              {!!item.notes && <Text style={styles.docNotes} numberOfLines={2}>{item.notes}</Text>}
            </View>
            {user?.role === "admin" && (
              <Pressable
                testID={`delete-doc-${item.id}`}
                onPress={() => removeDoc(item.id)}
                hitSlop={10}
              >
                <Ionicons name="trash-outline" size={18} color={theme.colors.error} />
              </Pressable>
            )}
          </View>
        )}
      />

      <Modal visible={showAdd} animationType="slide" transparent onRequestClose={() => setShowAdd(false)}>
        <KeyboardAvoidingView
          behavior={Platform.OS === "ios" ? "padding" : undefined}
          style={styles.modalRoot}
        >
          <Pressable style={styles.backdrop} onPress={() => setShowAdd(false)} />
          <View style={styles.sheet}>
            <View style={styles.sheetHandle} />
            <Text style={styles.sheetTitle}>Upload document</Text>

            <Text style={styles.fieldLabel}>Title</Text>
            <TextInput
              testID="doc-title-input"
              value={title} onChangeText={setTitle}
              placeholder="e.g. Background check - John Doe"
              placeholderTextColor={theme.colors.muted}
              style={styles.input}
            />

            <Text style={styles.fieldLabel}>Category</Text>
            <ScrollView horizontal showsHorizontalScrollIndicator={false} contentContainerStyle={{ gap: 8 }}>
              {CATEGORIES.filter((c) => c.key !== "all").map((c) => {
                const active = category === c.key;
                return (
                  <Pressable
                    key={c.key}
                    onPress={() => setCategory(c.key as DocCategory)}
                    style={[styles.chip, active && styles.chipActive]}
                    testID={`pick-cat-${c.key}`}
                  >
                    <Text style={[styles.chipText, active && styles.chipTextActive]}>{c.label}</Text>
                  </Pressable>
                );
              })}
            </ScrollView>

            <Text style={styles.fieldLabel}>Notes (optional)</Text>
            <TextInput
              value={notes} onChangeText={setNotes}
              placeholder="Add context or expiration date"
              placeholderTextColor={theme.colors.muted}
              multiline
              style={[styles.input, { minHeight: 64, textAlignVertical: "top" }]}
            />

            <Pressable testID="doc-pick-file" onPress={pickFile} style={styles.pickBtn}>
              <Ionicons name="cloud-upload-outline" size={18} color={theme.colors.brandPrimary} />
              <Text style={styles.pickBtnText}>
                {pickedFile ? pickedFile.name : "Attach file (optional)"}
              </Text>
            </Pressable>

            <Pressable
              testID="submit-doc-button"
              onPress={submit}
              disabled={submitting || !title.trim()}
              style={[styles.primaryBtn, (!title.trim() || submitting) && { opacity: 0.6 }]}
            >
              {submitting ? <ActivityIndicator color="#fff" /> : <Text style={styles.primaryBtnText}>Save</Text>}
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
  chipsWrap: { height: 56, justifyContent: "center" },
  chipsRow: { paddingHorizontal: 20, gap: 8, alignItems: "center" },
  chip: {
    height: 36, paddingHorizontal: 12,
    borderRadius: 999, backgroundColor: theme.colors.surfaceSecondary,
    borderWidth: 1, borderColor: theme.colors.border,
    flexDirection: "row", alignItems: "center", gap: 6,
    flexShrink: 0,
  },
  chipActive: { backgroundColor: theme.colors.brandPrimary, borderColor: theme.colors.brandPrimary },
  chipText: { fontSize: 13, fontWeight: "600", color: theme.colors.onSurfaceTertiary },
  chipTextActive: { color: "#fff" },
  docCard: {
    flexDirection: "row", alignItems: "center", gap: 12,
    backgroundColor: theme.colors.surfaceSecondary, padding: 14, borderRadius: 14,
    borderWidth: 1, borderColor: theme.colors.border,
  },
  docIcon: {
    width: 44, height: 44, borderRadius: 10,
    backgroundColor: theme.colors.brandTertiary,
    alignItems: "center", justifyContent: "center",
  },
  docTitle: { fontSize: 15, fontWeight: "600", color: theme.colors.onSurface },
  docMeta: { fontSize: 11, color: theme.colors.muted, marginTop: 2, fontWeight: "600", letterSpacing: 0.5 },
  docNotes: { fontSize: 12, color: theme.colors.onSurfaceTertiary, marginTop: 4 },
  empty: { alignItems: "center", paddingVertical: 80, gap: 8 },
  emptyIcon: { width: 80, height: 80, borderRadius: 24, backgroundColor: theme.colors.brandTertiary, alignItems: "center", justifyContent: "center" },
  emptyTitle: { fontSize: 16, fontWeight: "700", color: theme.colors.onSurface, marginTop: 12 },
  emptySubtitle: { fontSize: 13, color: theme.colors.muted, textAlign: "center", paddingHorizontal: 40 },
  modalRoot: { flex: 1, justifyContent: "flex-end" },
  backdrop: { ...StyleSheet.absoluteFillObject, backgroundColor: "rgba(0,0,0,0.4)" },
  sheet: {
    backgroundColor: theme.colors.surface,
    borderTopLeftRadius: 24, borderTopRightRadius: 24,
    padding: 20, paddingBottom: 32, gap: 10,
  },
  sheetHandle: { width: 40, height: 4, borderRadius: 2, backgroundColor: theme.colors.border, alignSelf: "center", marginBottom: 8 },
  sheetTitle: { fontSize: 20, fontWeight: "700", color: theme.colors.onSurface },
  fieldLabel: { fontSize: 12, fontWeight: "700", color: theme.colors.muted, textTransform: "uppercase", letterSpacing: 0.8, marginTop: 6 },
  input: {
    backgroundColor: theme.colors.surfaceSecondary,
    borderWidth: 1, borderColor: theme.colors.border,
    borderRadius: 12, paddingHorizontal: 14, paddingVertical: 12,
    fontSize: 15, color: theme.colors.onSurface,
  },
  pickBtn: {
    flexDirection: "row", alignItems: "center", gap: 8,
    borderWidth: 1, borderStyle: "dashed", borderColor: theme.colors.brandPrimary,
    backgroundColor: theme.colors.brandTertiary, padding: 14, borderRadius: 12, marginTop: 6,
  },
  pickBtnText: { color: theme.colors.brandPrimary, fontWeight: "600", fontSize: 14, flex: 1 },
  primaryBtn: {
    backgroundColor: theme.colors.brandPrimary, padding: 16,
    borderRadius: 12, alignItems: "center", marginTop: 10,
  },
  primaryBtnText: { color: "#fff", fontWeight: "700", fontSize: 15 },
});
