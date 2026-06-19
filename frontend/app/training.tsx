import { useCallback, useState } from "react";
import {
  View, Text, StyleSheet, ScrollView, Pressable, TextInput, Modal,
  ActivityIndicator, RefreshControl, KeyboardAvoidingView, Platform, FlatList,
} from "react-native";
import { Ionicons } from "@expo/vector-icons";
import { SafeAreaView } from "react-native-safe-area-context";
import * as DocumentPicker from "expo-document-picker";
import { useFocusEffect, useRouter } from "expo-router";
import { useAuth } from "@/src/context/AuthContext";
import { apiGet, apiPost, apiDelete } from "@/src/api/client";
import { theme } from "@/src/theme";

type TrainingItem = { id: string; title: string; description?: string; required: boolean; mime_type?: string };
type Completion = { id: string; training_id: string; caregiver_id: string; completed_at: string };

export default function Training() {
  const { user } = useAuth();
  const router = useRouter();
  const isAdmin = user?.role === "admin";

  const [items, setItems] = useState<TrainingItem[]>([]);
  const [completions, setCompletions] = useState<Completion[]>([]);
  const [loading, setLoading] = useState(false);
  const [showAdd, setShowAdd] = useState(false);
  const [title, setTitle] = useState("");
  const [desc, setDesc] = useState("");
  const [picked, setPicked] = useState<{ base64: string; mime: string; name: string } | null>(null);
  const [submitting, setSubmitting] = useState(false);

  const load = useCallback(async () => {
    setLoading(true);
    try {
      const [it, co] = await Promise.all([
        apiGet<TrainingItem[]>("/training"),
        apiGet<Completion[]>("/training/completions"),
      ]);
      setItems(it); setCompletions(co);
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
    setPicked({ base64, mime: a.mimeType || "video/mp4", name: a.name });
  };

  const submit = async () => {
    if (!title.trim()) return;
    setSubmitting(true);
    try {
      await apiPost("/training", {
        title: title.trim(),
        description: desc,
        file_base64: picked?.base64 || null,
        mime_type: picked?.mime || "video/mp4",
        required: true,
      });
      setTitle(""); setDesc(""); setPicked(null); setShowAdd(false);
      load();
    } finally { setSubmitting(false); }
  };

  const completed = (tid: string) => completions.some((c) => c.training_id === tid && (isAdmin ? true : c.caregiver_id === user?.id));

  const markComplete = async (tid: string) => {
    await apiPost(`/training/${tid}/complete`, {});
    load();
  };

  return (
    <SafeAreaView edges={["top"]} style={styles.root}>
      <View style={styles.header}>
        <Pressable testID="training-back" onPress={() => router.back()} hitSlop={10}>
          <Ionicons name="chevron-back" size={26} color={theme.colors.onSurface} />
        </Pressable>
        <View style={{ flex: 1 }}>
          <Text style={styles.title}>Training Library</Text>
          <Text style={styles.subtitle}>
            {isAdmin ? `${items.length} module${items.length === 1 ? "" : "s"}` : "Complete to stay audit-ready"}
          </Text>
        </View>
        {isAdmin && (
          <Pressable testID="add-training-button" onPress={() => setShowAdd(true)} style={styles.addBtn}>
            <Ionicons name="add" size={22} color="#fff" />
          </Pressable>
        )}
      </View>

      <FlatList
        data={items}
        keyExtractor={(i) => i.id}
        contentContainerStyle={{ padding: 20, gap: 10, paddingBottom: 40 }}
        refreshControl={<RefreshControl refreshing={loading} onRefresh={load} />}
        ListEmptyComponent={
          loading ? null : (
            <View style={styles.empty}>
              <View style={styles.emptyIcon}>
                <Ionicons name="school-outline" size={36} color={theme.colors.brandPrimary} />
              </View>
              <Text style={styles.emptyTitle}>No training modules</Text>
              <Text style={styles.emptySub}>
                {isAdmin ? "Tap + to upload your first module" : "Check back soon"}
              </Text>
            </View>
          )
        }
        renderItem={({ item }) => {
          const done = completed(item.id);
          return (
            <View style={styles.card} testID={`training-${item.id}`}>
              <View style={[styles.cardIcon, done && { backgroundColor: theme.colors.success }]}>
                <Ionicons
                  name={done ? "checkmark" : "play"}
                  size={20}
                  color={done ? "#fff" : theme.colors.brandPrimary}
                />
              </View>
              <View style={{ flex: 1 }}>
                <Text style={styles.cardTitle}>{item.title}</Text>
                {!!item.description && <Text style={styles.cardDesc} numberOfLines={2}>{item.description}</Text>}
                <View style={styles.badgeRow}>
                  {item.required && (
                    <View style={[styles.badge, { backgroundColor: theme.colors.warning }]}>
                      <Text style={styles.badgeText}>Required</Text>
                    </View>
                  )}
                  {done && (
                    <View style={[styles.badge, { backgroundColor: theme.colors.success }]}>
                      <Text style={styles.badgeText}>Completed</Text>
                    </View>
                  )}
                </View>
              </View>
              {!isAdmin && !done && (
                <Pressable
                  testID={`complete-${item.id}`}
                  onPress={() => markComplete(item.id)}
                  style={styles.completeBtn}
                >
                  <Text style={styles.completeBtnText}>Mark done</Text>
                </Pressable>
              )}
              {isAdmin && (
                <Pressable
                  testID={`delete-training-${item.id}`}
                  onPress={() => apiDelete(`/training/${item.id}`).then(load)}
                  hitSlop={10}
                >
                  <Ionicons name="trash-outline" size={18} color={theme.colors.error} />
                </Pressable>
              )}
            </View>
          );
        }}
      />

      <Modal visible={showAdd} animationType="slide" transparent onRequestClose={() => setShowAdd(false)}>
        <KeyboardAvoidingView
          behavior={Platform.OS === "ios" ? "padding" : undefined}
          style={{ flex: 1, justifyContent: "flex-end" }}
        >
          <Pressable style={StyleSheet.absoluteFill} onPress={() => setShowAdd(false)} />
          <View style={styles.sheet}>
            <View style={styles.sheetHandle} />
            <Text style={styles.sheetTitle}>New training module</Text>
            <TextInput
              testID="training-title-input"
              value={title} onChangeText={setTitle}
              placeholder="Title (e.g. HIPAA Basics)"
              placeholderTextColor={theme.colors.muted}
              style={styles.input}
            />
            <TextInput
              value={desc} onChangeText={setDesc}
              placeholder="Description"
              placeholderTextColor={theme.colors.muted}
              multiline
              style={[styles.input, { minHeight: 80, textAlignVertical: "top" }]}
            />
            <Pressable testID="training-pick-file" onPress={pickFile} style={styles.pickBtn}>
              <Ionicons name="film-outline" size={18} color={theme.colors.brandPrimary} />
              <Text style={styles.pickBtnText}>{picked ? picked.name : "Attach video/PDF (optional)"}</Text>
            </Pressable>
            <Pressable
              testID="submit-training-button"
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
  header: { paddingHorizontal: 20, paddingTop: 8, paddingBottom: 12, flexDirection: "row", alignItems: "center", gap: 12 },
  title: { fontSize: 22, fontWeight: "700", color: theme.colors.onSurface },
  subtitle: { fontSize: 12, color: theme.colors.muted, marginTop: 2 },
  addBtn: { width: 40, height: 40, borderRadius: 12, backgroundColor: theme.colors.brandPrimary, alignItems: "center", justifyContent: "center" },
  card: { flexDirection: "row", gap: 12, alignItems: "center", backgroundColor: theme.colors.surfaceSecondary, padding: 14, borderRadius: 14, borderWidth: 1, borderColor: theme.colors.border },
  cardIcon: { width: 44, height: 44, borderRadius: 12, backgroundColor: theme.colors.brandTertiary, alignItems: "center", justifyContent: "center" },
  cardTitle: { fontSize: 15, fontWeight: "600", color: theme.colors.onSurface },
  cardDesc: { fontSize: 12, color: theme.colors.muted, marginTop: 2 },
  badgeRow: { flexDirection: "row", gap: 6, marginTop: 6 },
  badge: { paddingHorizontal: 8, paddingVertical: 3, borderRadius: 999 },
  badgeText: { color: "#fff", fontSize: 10, fontWeight: "700", letterSpacing: 0.4 },
  completeBtn: { backgroundColor: theme.colors.brandPrimary, paddingHorizontal: 12, paddingVertical: 8, borderRadius: 999 },
  completeBtnText: { color: "#fff", fontWeight: "700", fontSize: 12 },
  empty: { alignItems: "center", paddingVertical: 80, gap: 8 },
  emptyIcon: { width: 80, height: 80, borderRadius: 24, backgroundColor: theme.colors.brandTertiary, alignItems: "center", justifyContent: "center" },
  emptyTitle: { fontSize: 16, fontWeight: "700", color: theme.colors.onSurface, marginTop: 8 },
  emptySub: { fontSize: 13, color: theme.colors.muted },
  sheet: { backgroundColor: theme.colors.surface, borderTopLeftRadius: 24, borderTopRightRadius: 24, padding: 20, paddingBottom: 32, gap: 12 },
  sheetHandle: { width: 40, height: 4, borderRadius: 2, backgroundColor: theme.colors.border, alignSelf: "center" },
  sheetTitle: { fontSize: 20, fontWeight: "700", color: theme.colors.onSurface },
  input: { backgroundColor: theme.colors.surfaceSecondary, borderWidth: 1, borderColor: theme.colors.border, borderRadius: 12, paddingHorizontal: 14, paddingVertical: 12, fontSize: 15, color: theme.colors.onSurface },
  pickBtn: { flexDirection: "row", alignItems: "center", gap: 8, borderWidth: 1, borderStyle: "dashed", borderColor: theme.colors.brandPrimary, backgroundColor: theme.colors.brandTertiary, padding: 14, borderRadius: 12 },
  pickBtnText: { color: theme.colors.brandPrimary, fontWeight: "600", fontSize: 14, flex: 1 },
  primaryBtn: { backgroundColor: theme.colors.brandPrimary, padding: 16, borderRadius: 12, alignItems: "center", marginTop: 6 },
  primaryBtnText: { color: "#fff", fontWeight: "700", fontSize: 15 },
});
