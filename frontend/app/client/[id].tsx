import { useCallback, useEffect, useState } from "react";
import {
  View, Text, StyleSheet, ScrollView, Pressable, ActivityIndicator,
  RefreshControl, Platform,
} from "react-native";
import { Image } from "expo-image";
import { Ionicons } from "@expo/vector-icons";
import { SafeAreaView } from "react-native-safe-area-context";
import { useFocusEffect, useLocalSearchParams, useRouter } from "expo-router";
import * as ImagePicker from "expo-image-picker";
import AsyncStorage from "@react-native-async-storage/async-storage";
import { apiGet, apiPost, API_BASE, getAuthToken } from "@/src/api/client";
import { useAuth } from "@/src/context/AuthContext";
import { PdfViewerModal } from "@/src/components/pdf/PdfViewerModal";
import { theme, BRAND_NAME } from "@/src/theme";

function AssignCaregiverPicker({ clientId, assignedIds, onAssigned }: { clientId: string; assignedIds: string[]; onAssigned: () => void }) {
  const [caregivers, setCaregivers] = useState<{ id: string; name: string; photo_base64?: string }[]>([]);
  const [busy, setBusy] = useState<string | null>(null);
  useEffect(() => {
    apiGet<any[]>("/caregivers").then(setCaregivers).catch(() => { });
  }, []);
  const available = caregivers.filter((c) => !assignedIds.includes(c.id));
  if (available.length === 0) {
    return <Text style={{ fontSize: 12, color: theme.colors.muted, marginTop: 6 }}>All caregivers already assigned.</Text>;
  }
  return (
    <View style={{ flexDirection: "row", flexWrap: "wrap", gap: 8, marginTop: 8 }}>
      {available.map((c) => (
        <Pressable
          key={c.id}
          testID={`assign-cg-${c.id}`}
          disabled={busy === c.id}
          onPress={async () => {
            setBusy(c.id);
            try {
              await apiPost("/assignments", { caregiver_id: c.id, client_id: clientId });
              onAssigned();
            } finally { setBusy(null); }
          }}
          style={{ flexDirection: "row", alignItems: "center", gap: 6, paddingHorizontal: 10, paddingVertical: 8, borderRadius: 999, backgroundColor: theme.colors.brandPrimary, opacity: busy === c.id ? 0.5 : 1 }}
        >
          <Ionicons name="add" size={14} color="#fff" />
          <Text style={{ color: "#fff", fontWeight: "700", fontSize: 12 }}>{c.name}</Text>
        </Pressable>
      ))}
    </View>
  );
}

type Detail = {
  client: { id: string; name: string; address?: string; phone?: string; photo_base64?: string };
  tasks: { id: string; title: string; seq: number; completed: boolean }[];
  caregivers: { id: string; name: string; email: string; photo_base64?: string }[];
  shifts: { id: string; caregiver_id: string; kind: string; date?: string; weekdays?: string[]; start_time: string; end_time: string; notes?: string }[];
};

export default function ClientDetail() {
  const { id } = useLocalSearchParams<{ id: string }>();
  const router = useRouter();
  const [data, setData] = useState<Detail | null>(null);
  const [loading, setLoading] = useState(false);
  const [tab, setTab] = useState<"tasks" | "schedule" | "team">("tasks");
  const [showBinder, setShowBinder] = useState(false);

  const load = useCallback(async () => {
    if (!id) return;
    setLoading(true);
    try {
      const d = await apiGet<Detail>(`/clients/${id}/detail`);
      setData(d);
    } catch (e) { console.log(e); }
    finally { setLoading(false); }
  }, [id]);

  useFocusEffect(useCallback(() => { load(); }, [load]));

  const pickPhoto = async () => {
    const perm = await ImagePicker.requestMediaLibraryPermissionsAsync();
    if (!perm.granted) return;
    const res = await ImagePicker.launchImageLibraryAsync({
      mediaTypes: ImagePicker.MediaTypeOptions.Images,
      base64: true, quality: 0.6,
    });
    if (res.canceled || !res.assets?.length) return;
    const b64 = res.assets[0].base64;
    if (!b64) return;
    const token = await getAuthToken();
    await fetch(`${API_BASE}/clients/${id}/photo`, {
      method: "PUT",
      headers: { "Content-Type": "application/json", Authorization: `Bearer ${token}` },
      body: JSON.stringify({ photo_base64: b64 }),
    });
    load();
  };

  const downloadBinder = () => setShowBinder(true);

  const toggleTask = async (tid: string) => {
    await apiPost(`/client-tasks/${tid}/toggle`, {});
    load();
  };

  const isAdmin = useAuth().user?.role === "admin";

  if (loading && !data) {
    return <SafeAreaView style={styles.center}><ActivityIndicator color={theme.colors.brandPrimary} /></SafeAreaView>;
  }
  if (!data) {
    return <SafeAreaView style={styles.center}><Text>Not found</Text></SafeAreaView>;
  }

  const { client, tasks, caregivers, shifts } = data;
  const done = tasks.filter((t) => t.completed).length;

  return (
    <SafeAreaView edges={["top"]} style={styles.root}>
      <ScrollView
        contentContainerStyle={{ paddingBottom: 40 }}
        refreshControl={<RefreshControl refreshing={loading} onRefresh={load} />}
      >
        <View style={styles.header}>
          <Pressable testID="back-btn" onPress={() => router.back()} hitSlop={10}>
            <Ionicons name="chevron-back" size={26} color={theme.colors.onSurface} />
          </Pressable>
          <Text style={styles.brand}>{BRAND_NAME}</Text>
          <Pressable testID="binder-btn" onPress={downloadBinder} hitSlop={10}>
            <Ionicons name="download-outline" size={22} color={theme.colors.brandPrimary} />
          </Pressable>
        </View>

        <View style={styles.hero}>
          <Pressable testID="photo-pick" onPress={pickPhoto} style={styles.avatarWrap}>
            {client.photo_base64 ? (
              <Image source={{ uri: `data:image/jpeg;base64,${client.photo_base64}` }} style={styles.avatar} contentFit="cover" />
            ) : (
              <View style={[styles.avatar, styles.avatarPlaceholder]}>
                <Ionicons name="person" size={36} color={theme.colors.brandPrimary} />
              </View>
            )}
            <View style={styles.cameraBadge}>
              <Ionicons name="camera" size={12} color="#fff" />
            </View>
          </Pressable>
          <Text style={styles.name}>{client.name}</Text>
          {!!client.address && <Text style={styles.meta}>{client.address}</Text>}
          {!!client.phone && <Text style={styles.meta}>{client.phone}</Text>}
          <View style={styles.progress}>
            <Text style={styles.progressText}>
              {done}/{tasks.length} onboarding complete
            </Text>
            <View style={styles.bar}>
              <View style={[styles.barFill, { width: tasks.length ? `${(done / tasks.length) * 100}%` : "0%" }]} />
            </View>
          </View>
        </View>

        <View style={styles.tabsRow}>
          {(["tasks", "schedule", "team"] as const).map((t) => (
            <Pressable key={t} testID={`tab-${t}`} onPress={() => setTab(t)} style={[styles.tab, tab === t && styles.tabActive]}>
              <Text style={[styles.tabText, tab === t && styles.tabTextActive]}>
                {t === "tasks" ? `Tasks (${tasks.length})` : t === "schedule" ? `Schedule (${shifts.length})` : `Care Team (${caregivers.length})`}
              </Text>
            </Pressable>
          ))}
        </View>

        {tab === "tasks" && (
          <View style={{ padding: 16, gap: 8 }}>
            {tasks.length === 0 && <Text style={styles.empty}>No tasks. Use "Assign Packet" on the client list.</Text>}
            {tasks.map((t) => (
              <Pressable key={t.id} onPress={() => toggleTask(t.id)} style={styles.task}>
                <View style={[styles.checkbox, t.completed && styles.checkboxOn]}>
                  {t.completed && <Ionicons name="checkmark" size={16} color="#fff" />}
                </View>
                <Text style={[styles.taskText, t.completed && { textDecorationLine: "line-through", color: theme.colors.muted }]} numberOfLines={2}>
                  {t.title}
                </Text>
              </Pressable>
            ))}
          </View>
        )}

        {tab === "team" && (
          <View style={{ padding: 16, gap: 10 }}>
            {isAdmin && (
              <View>
                <Text style={[styles.empty, { textAlign: "left", paddingVertical: 0, fontSize: 12, fontWeight: "700", color: theme.colors.muted }]}>ASSIGN A CAREGIVER</Text>
                <AssignCaregiverPicker
                  clientId={id as string}
                  assignedIds={caregivers.map((c) => c.id)}
                  onAssigned={load}
                />
              </View>
            )}
            {caregivers.length === 0 && <Text style={styles.empty}>No caregivers assigned yet.</Text>}
            {caregivers.map((c) => (
              <Pressable key={c.id} onPress={() => router.push(`/caregiver/${c.id}`)} style={styles.teamRow}>
                {c.photo_base64 ? (
                  <Image source={{ uri: `data:image/jpeg;base64,${c.photo_base64}` }} style={styles.smallAvatar} contentFit="cover" />
                ) : (
                  <View style={[styles.smallAvatar, styles.avatarPlaceholder]}>
                    <Ionicons name="person" size={20} color={theme.colors.brandPrimary} />
                  </View>
                )}
                <View style={{ flex: 1 }}>
                  <Text style={styles.teamName}>{c.name}</Text>
                  <Text style={styles.teamEmail}>{c.email}</Text>
                </View>
              </Pressable>
            ))}
          </View>
        )}

        {tab === "schedule" && (
          <View style={{ padding: 16, gap: 10 }}>
            {shifts.length === 0 && <Text style={styles.empty}>No shifts scheduled. Add via Team → Caregivers (coming next).</Text>}
            {shifts.map((s) => {
              const cg = caregivers.find((c) => c.id === s.caregiver_id);
              return (
                <View key={s.id} style={styles.shift}>
                  <View style={styles.shiftIcon}>
                    <Ionicons name="time-outline" size={20} color={theme.colors.brandPrimary} />
                  </View>
                  <View style={{ flex: 1 }}>
                    <Text style={styles.shiftWhen}>
                      {s.kind === "recurring" ? (s.weekdays || []).join(" · ") : s.date}
                      {"  "}{s.start_time}–{s.end_time}
                    </Text>
                    <Text style={styles.shiftWho}>{cg?.name || "Unknown caregiver"}</Text>
                    {!!s.notes && <Text style={styles.shiftNotes}>{s.notes}</Text>}
                  </View>
                </View>
              );
            })}
          </View>
        )}
      </ScrollView>
      <PdfViewerModal
        visible={showBinder}
        onClose={() => setShowBinder(false)}
        title={`${client.name} — Audit Binder`}
        path={showBinder ? `/reports/audit-binder?client_id=${id}` : null}
      />
    </SafeAreaView>
  );
}

const styles = StyleSheet.create({
  root: { flex: 1, backgroundColor: theme.colors.surface },
  center: { flex: 1, alignItems: "center", justifyContent: "center" },
  header: { flexDirection: "row", alignItems: "center", justifyContent: "space-between", paddingHorizontal: 16, paddingVertical: 10 },
  brand: { fontWeight: "700", color: theme.colors.brandPrimary, fontSize: 14 },
  hero: { alignItems: "center", paddingVertical: 16, paddingHorizontal: 20, gap: 4 },
  avatarWrap: { position: "relative" },
  avatar: { width: 110, height: 110, borderRadius: 55 },
  avatarPlaceholder: { backgroundColor: theme.colors.brandTertiary, alignItems: "center", justifyContent: "center" },
  cameraBadge: { position: "absolute", bottom: 0, right: 0, width: 30, height: 30, borderRadius: 15, backgroundColor: theme.colors.brandPrimary, alignItems: "center", justifyContent: "center", borderWidth: 2, borderColor: "#fff" },
  name: { fontSize: 22, fontWeight: "700", color: theme.colors.onSurface, marginTop: 10 },
  meta: { fontSize: 13, color: theme.colors.muted },
  progress: { width: "100%", marginTop: 14 },
  progressText: { fontSize: 12, fontWeight: "700", color: theme.colors.muted, textAlign: "center", marginBottom: 6 },
  bar: { height: 6, backgroundColor: theme.colors.surfaceTertiary, borderRadius: 999, overflow: "hidden" },
  barFill: { height: "100%", backgroundColor: theme.colors.success },
  tabsRow: { flexDirection: "row", gap: 8, paddingHorizontal: 16 },
  tab: { flex: 1, paddingVertical: 10, borderRadius: 10, backgroundColor: theme.colors.surfaceSecondary, borderWidth: 1, borderColor: theme.colors.border, alignItems: "center" },
  tabActive: { backgroundColor: theme.colors.brandPrimary, borderColor: theme.colors.brandPrimary },
  tabText: { fontSize: 12, fontWeight: "700", color: theme.colors.onSurface },
  tabTextActive: { color: "#fff" },
  empty: { textAlign: "center", color: theme.colors.muted, fontSize: 13, paddingVertical: 40, paddingHorizontal: 20 },
  task: { flexDirection: "row", alignItems: "center", gap: 12, padding: 12, backgroundColor: theme.colors.surfaceSecondary, borderRadius: 12, borderWidth: 1, borderColor: theme.colors.border },
  checkbox: { width: 24, height: 24, borderRadius: 12, borderWidth: 2, borderColor: theme.colors.borderStrong, alignItems: "center", justifyContent: "center" },
  checkboxOn: { backgroundColor: theme.colors.success, borderColor: theme.colors.success },
  taskText: { flex: 1, fontSize: 14, color: theme.colors.onSurface, fontWeight: "500" },
  teamRow: { flexDirection: "row", alignItems: "center", gap: 12, padding: 12, backgroundColor: theme.colors.surfaceSecondary, borderRadius: 12, borderWidth: 1, borderColor: theme.colors.border },
  smallAvatar: { width: 44, height: 44, borderRadius: 22 },
  teamName: { fontWeight: "600", fontSize: 14, color: theme.colors.onSurface },
  teamEmail: { fontSize: 12, color: theme.colors.muted, marginTop: 2 },
  shift: { flexDirection: "row", alignItems: "center", gap: 12, padding: 12, backgroundColor: theme.colors.surfaceSecondary, borderRadius: 12, borderWidth: 1, borderColor: theme.colors.border },
  shiftIcon: { width: 40, height: 40, borderRadius: 10, backgroundColor: theme.colors.brandTertiary, alignItems: "center", justifyContent: "center" },
  shiftWhen: { fontWeight: "700", color: theme.colors.onSurface, fontSize: 13 },
  shiftWho: { fontSize: 12, color: theme.colors.muted, marginTop: 2 },
  shiftNotes: { fontSize: 12, color: theme.colors.onSurfaceTertiary, marginTop: 4 },
});
