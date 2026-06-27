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

function AssignClientPicker({ caregiverId, assignedIds, onAssigned }: { caregiverId: string; assignedIds: string[]; onAssigned: () => void }) {
  const [clients, setClients] = useState<{ id: string; name: string }[]>([]);
  const [busy, setBusy] = useState<string | null>(null);
  useEffect(() => {
    apiGet<any[]>("/clients").then(setClients).catch(() => { });
  }, []);
  const available = clients.filter((c) => !assignedIds.includes(c.id));
  if (available.length === 0) {
    return <Text style={{ fontSize: 12, color: theme.colors.muted, marginTop: 6 }}>All clients already assigned.</Text>;
  }
  return (
    <View style={{ flexDirection: "row", flexWrap: "wrap", gap: 8, marginTop: 8 }}>
      {available.map((c) => (
        <Pressable
          key={c.id}
          testID={`assign-cl-${c.id}`}
          disabled={busy === c.id}
          onPress={async () => {
            setBusy(c.id);
            try {
              await apiPost("/assignments", { caregiver_id: caregiverId, client_id: c.id });
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
  caregiver: { id: string; name: string; email: string; photo_base64?: string };
  clients: { id: string; name: string; address?: string; phone?: string }[];
  shifts: { id: string; client_id: string; kind: string; date?: string; weekdays?: string[]; start_time: string; end_time: string; notes?: string; clocked_in_at?: string; clocked_out_at?: string }[];
  credentials: { id: string; title: string; expires_at?: string }[];
  onboarding: { id: string; title: string; completed: boolean }[];
};

export default function CaregiverDetail() {
  const { id } = useLocalSearchParams<{ id: string }>();
  const router = useRouter();
  const { user } = useAuth();
  const [data, setData] = useState<Detail | null>(null);
  const [loading, setLoading] = useState(false);
  const [tab, setTab] = useState<"schedule" | "clients" | "credentials" | "onboarding">("schedule");
  const [showBinder, setShowBinder] = useState(false);
  const isSelf = user?.id === id;
  const isAdmin = user?.role === "admin";

  const load = useCallback(async () => {
    if (!id) return;
    setLoading(true);
    try {
      const d = await apiGet<Detail>(`/caregivers/${id}/detail`);
      setData(d);
    } catch (e) { console.log(e); }
    finally { setLoading(false); }
  }, [id]);

  useFocusEffect(useCallback(() => { load(); }, [load]));

  const pickPhoto = async () => {
    if (!isAdmin && !isSelf) return;
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
    await fetch(`${API_BASE}/users/${id}/photo`, {
      method: "PUT",
      headers: { "Content-Type": "application/json", Authorization: `Bearer ${token}` },
      body: JSON.stringify({ photo_base64: b64 }),
    });
    load();
  };

  const downloadBinder = () => setShowBinder(true);

  const clockIn = async (shiftId: string) => {
    await apiPost(`/shifts/${shiftId}/clock-in`, {});
    load();
  };
  const clockOut = async (shiftId: string) => {
    await apiPost(`/shifts/${shiftId}/clock-out`, {});
    load();
  };

  if (loading && !data) {
    return <SafeAreaView style={styles.center}><ActivityIndicator color={theme.colors.brandPrimary} /></SafeAreaView>;
  }
  if (!data) return <SafeAreaView style={styles.center}><Text>Not found</Text></SafeAreaView>;

  const { caregiver, clients, shifts, credentials, onboarding } = data;
  const onDone = onboarding.filter((s) => s.completed).length;

  return (
    <SafeAreaView edges={["top"]} style={styles.root}>
      <ScrollView contentContainerStyle={{ paddingBottom: 40 }}
        refreshControl={<RefreshControl refreshing={loading} onRefresh={load} />}>
        <View style={styles.header}>
          <Pressable onPress={() => router.back()} hitSlop={10}>
            <Ionicons name="chevron-back" size={26} color={theme.colors.onSurface} />
          </Pressable>
          <Text style={styles.brand}>{BRAND_NAME}</Text>
          <View style={{ flexDirection: "row", gap: 12 }}>
            {isAdmin && (
              <Pressable onPress={downloadBinder} hitSlop={10}>
                <Ionicons name="download-outline" size={22} color={theme.colors.brandPrimary} />
              </Pressable>
            )}
            <Pressable onPress={() => router.push(`/chat/${id}`)} hitSlop={10}>
              <Ionicons name="chatbubble-outline" size={22} color={theme.colors.brandPrimary} />
            </Pressable>
          </View>
        </View>

        <View style={styles.hero}>
          <Pressable onPress={pickPhoto} style={styles.avatarWrap}>
            {caregiver.photo_base64 ? (
              <Image source={{ uri: `data:image/jpeg;base64,${caregiver.photo_base64}` }} style={styles.avatar} contentFit="cover" />
            ) : (
              <View style={[styles.avatar, styles.avatarPh]}>
                <Ionicons name="person" size={36} color={theme.colors.brandPrimary} />
              </View>
            )}
            {(isAdmin || isSelf) && (
              <View style={styles.cameraBadge}>
                <Ionicons name="camera" size={12} color="#fff" />
              </View>
            )}
          </Pressable>
          <Text style={styles.name}>{caregiver.name}</Text>
          <Text style={styles.meta}>{caregiver.email}</Text>
          <View style={styles.progress}>
            <Text style={styles.progressText}>
              {onDone}/{onboarding.length} onboarding · {clients.length} clients · {credentials.length} credentials
            </Text>
            <View style={styles.bar}>
              <View style={[styles.barFill, { width: onboarding.length ? `${(onDone / onboarding.length) * 100}%` : "0%" }]} />
            </View>
          </View>
        </View>

        <View style={styles.tabsRow}>
          {(["schedule", "clients", "credentials", "onboarding"] as const).map((t) => (
            <Pressable key={t} testID={`tab-${t}`} onPress={() => setTab(t)} style={[styles.tab, tab === t && styles.tabActive]}>
              <Text style={[styles.tabText, tab === t && styles.tabTextActive]}>
                {t === "schedule" ? `Schedule (${shifts.length})` :
                 t === "clients" ? `Clients (${clients.length})` :
                 t === "credentials" ? `Creds (${credentials.length})` :
                 `Onboarding (${onboarding.length})`}
              </Text>
            </Pressable>
          ))}
        </View>

        {tab === "schedule" && (
          <View style={{ padding: 16, gap: 10 }}>
            {shifts.length === 0 && <Text style={styles.empty}>No shifts scheduled.</Text>}
            {shifts.map((s) => {
              const cl = clients.find((c) => c.id === s.client_id);
              const isClockedIn = !!s.clocked_in_at && !s.clocked_out_at;
              const isComplete = !!s.clocked_out_at;
              return (
                <View key={s.id} style={styles.shift} testID={`shift-${s.id}`}>
                  <View style={[styles.shiftIcon, isComplete ? { backgroundColor: theme.colors.success } : isClockedIn ? { backgroundColor: theme.colors.warning } : undefined]}>
                    <Ionicons name={isComplete ? "checkmark" : "time-outline"} size={20} color={isComplete || isClockedIn ? "#fff" : theme.colors.brandPrimary} />
                  </View>
                  <View style={{ flex: 1 }}>
                    <Text style={styles.shiftWhen}>
                      {s.kind === "recurring" ? (s.weekdays || []).join(" · ") : s.date}
                      {"  "}{s.start_time}–{s.end_time}
                    </Text>
                    <Text style={styles.shiftWho}>{cl?.name || "Unknown client"}</Text>
                    {s.clocked_in_at && (
                      <Text style={[styles.shiftWho, { color: theme.colors.warning, fontWeight: "700" }]}>
                        Clocked in: {new Date(s.clocked_in_at).toLocaleTimeString([], { hour: "2-digit", minute: "2-digit" })}
                        {s.clocked_out_at && ` · Out: ${new Date(s.clocked_out_at).toLocaleTimeString([], { hour: "2-digit", minute: "2-digit" })}`}
                      </Text>
                    )}
                  </View>
                  {(isSelf || isAdmin) && !isComplete && (
                    <Pressable
                      testID={`clock-${s.id}`}
                      onPress={() => isClockedIn ? clockOut(s.id) : clockIn(s.id)}
                      style={[styles.clockBtn, { backgroundColor: isClockedIn ? theme.colors.error : theme.colors.success }]}
                    >
                      <Ionicons name={isClockedIn ? "stop-circle-outline" : "play-circle-outline"} size={16} color="#fff" />
                      <Text style={styles.clockBtnText}>{isClockedIn ? "Out" : "In"}</Text>
                    </Pressable>
                  )}
                </View>
              );
            })}
          </View>
        )}

        {tab === "clients" && (
          <View style={{ padding: 16, gap: 10 }}>
            {isAdmin && (
              <View>
                <Text style={[styles.empty, { textAlign: "left", paddingVertical: 0, fontSize: 12, fontWeight: "700", color: theme.colors.muted }]}>ASSIGN A CLIENT</Text>
                <AssignClientPicker
                  caregiverId={id as string}
                  assignedIds={clients.map((c) => c.id)}
                  onAssigned={load}
                />
              </View>
            )}
            {clients.length === 0 && <Text style={styles.empty}>No clients assigned.</Text>}
            {clients.map((c) => (
              <Pressable key={c.id} onPress={() => router.push(`/client/${c.id}`)} style={styles.row}>
                <View style={[styles.smallAvatar, styles.avatarPh]}>
                  <Ionicons name="person-outline" size={20} color={theme.colors.brandPrimary} />
                </View>
                <View style={{ flex: 1 }}>
                  <Text style={styles.teamName}>{c.name}</Text>
                  {!!c.address && <Text style={styles.teamEmail}>{c.address}</Text>}
                </View>
                <Ionicons name="chevron-forward" size={18} color={theme.colors.muted} />
              </Pressable>
            ))}
          </View>
        )}

        {tab === "credentials" && (
          <View style={{ padding: 16, gap: 10 }}>
            {credentials.length === 0 && <Text style={styles.empty}>No credentials uploaded.</Text>}
            {credentials.map((c) => {
              const exp = c.expires_at ? new Date(c.expires_at) : null;
              const expired = exp && exp.getTime() < Date.now();
              const soon = exp && exp.getTime() - Date.now() < 60 * 86400000;
              return (
                <View key={c.id} style={styles.row}>
                  <View style={[styles.smallAvatar, styles.avatarPh]}>
                    <Ionicons name="ribbon-outline" size={20} color={theme.colors.brandPrimary} />
                  </View>
                  <View style={{ flex: 1 }}>
                    <Text style={styles.teamName}>{c.title}</Text>
                    {!!exp && (
                      <Text style={[styles.teamEmail, { color: expired ? theme.colors.error : soon ? theme.colors.warning : theme.colors.success, fontWeight: "700" }]}>
                        {expired ? "EXPIRED" : "Expires"} {exp.toLocaleDateString()}
                      </Text>
                    )}
                  </View>
                </View>
              );
            })}
          </View>
        )}

        {tab === "onboarding" && (
          <View style={{ padding: 16, gap: 8 }}>
            {onboarding.length === 0 && <Text style={styles.empty}>No onboarding steps assigned.</Text>}
            {onboarding.map((o) => (
              <View key={o.id} style={styles.row}>
                <View style={[styles.checkbox, o.completed && styles.checkboxOn]}>
                  {o.completed && <Ionicons name="checkmark" size={16} color="#fff" />}
                </View>
                <Text style={[styles.taskText, o.completed && { textDecorationLine: "line-through", color: theme.colors.muted }]} numberOfLines={2}>
                  {o.title}
                </Text>
              </View>
            ))}
          </View>
        )}
      </ScrollView>
      <PdfViewerModal
        visible={showBinder}
        onClose={() => setShowBinder(false)}
        title={`${caregiver.name} — Audit Binder`}
        path={showBinder ? `/reports/audit-binder?caregiver_id=${id}` : null}
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
  avatarPh: { backgroundColor: theme.colors.brandTertiary, alignItems: "center", justifyContent: "center" },
  cameraBadge: { position: "absolute", bottom: 0, right: 0, width: 30, height: 30, borderRadius: 15, backgroundColor: theme.colors.brandPrimary, alignItems: "center", justifyContent: "center", borderWidth: 2, borderColor: "#fff" },
  name: { fontSize: 22, fontWeight: "700", color: theme.colors.onSurface, marginTop: 10 },
  meta: { fontSize: 13, color: theme.colors.muted },
  progress: { width: "100%", marginTop: 14 },
  progressText: { fontSize: 12, fontWeight: "700", color: theme.colors.muted, textAlign: "center", marginBottom: 6 },
  bar: { height: 6, backgroundColor: theme.colors.surfaceTertiary, borderRadius: 999, overflow: "hidden" },
  barFill: { height: "100%", backgroundColor: theme.colors.success },
  tabsRow: { flexDirection: "row", gap: 6, paddingHorizontal: 12 },
  tab: { flex: 1, paddingVertical: 10, borderRadius: 10, backgroundColor: theme.colors.surfaceSecondary, borderWidth: 1, borderColor: theme.colors.border, alignItems: "center" },
  tabActive: { backgroundColor: theme.colors.brandPrimary, borderColor: theme.colors.brandPrimary },
  tabText: { fontSize: 11, fontWeight: "700", color: theme.colors.onSurface },
  tabTextActive: { color: "#fff" },
  empty: { textAlign: "center", color: theme.colors.muted, fontSize: 13, paddingVertical: 40 },
  shift: { flexDirection: "row", alignItems: "center", gap: 10, padding: 12, backgroundColor: theme.colors.surfaceSecondary, borderRadius: 12, borderWidth: 1, borderColor: theme.colors.border },
  shiftIcon: { width: 40, height: 40, borderRadius: 10, backgroundColor: theme.colors.brandTertiary, alignItems: "center", justifyContent: "center" },
  shiftWhen: { fontWeight: "700", color: theme.colors.onSurface, fontSize: 13 },
  shiftWho: { fontSize: 12, color: theme.colors.muted, marginTop: 2 },
  clockBtn: { flexDirection: "row", alignItems: "center", gap: 4, paddingHorizontal: 10, paddingVertical: 8, borderRadius: 999 },
  clockBtnText: { color: "#fff", fontWeight: "800", fontSize: 12 },
  row: { flexDirection: "row", alignItems: "center", gap: 12, padding: 12, backgroundColor: theme.colors.surfaceSecondary, borderRadius: 12, borderWidth: 1, borderColor: theme.colors.border },
  smallAvatar: { width: 44, height: 44, borderRadius: 22 },
  teamName: { fontWeight: "600", fontSize: 14, color: theme.colors.onSurface },
  teamEmail: { fontSize: 12, color: theme.colors.muted, marginTop: 2 },
  checkbox: { width: 24, height: 24, borderRadius: 12, borderWidth: 2, borderColor: theme.colors.borderStrong, alignItems: "center", justifyContent: "center" },
  checkboxOn: { backgroundColor: theme.colors.success, borderColor: theme.colors.success },
  taskText: { flex: 1, fontSize: 14, color: theme.colors.onSurface, fontWeight: "500" },
});
