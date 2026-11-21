import { useCallback, useState } from "react";
import { View, Text, Pressable, StyleSheet, ScrollView, ActivityIndicator, RefreshControl } from "react-native";
import { useFocusEffect, useRouter } from "expo-router";
import { Ionicons } from "@expo/vector-icons";
import { SafeAreaView } from "react-native-safe-area-context";
import { apiGet } from "@/src/api/client";
import { theme } from "@/src/theme";
import { STATUSES } from "@/src/components/intake/intakeTypes";

type Row = {
  id: string; form_key: string; form_title: string; summary: string;
  status: string; submitted_at: string; submitted_by_name?: string; source?: string;
};

const NEW_FORMS: { key: string; label: string; icon: any }[] = [
  { key: "client_enrollment", label: "Client enrollment", icon: "person-add-outline" },
  { key: "referral", label: "Case manager referral", icon: "swap-horizontal-outline" },
  { key: "job_application", label: "Job application", icon: "briefcase-outline" },
];

export default function IntakeHome() {
  const router = useRouter();
  const [rows, setRows] = useState<Row[]>([]);
  const [loading, setLoading] = useState(true);
  const [refreshing, setRefreshing] = useState(false);
  const [filter, setFilter] = useState<string>("");
  const [error, setError] = useState(false);

  const load = useCallback(async () => {
    try {
      setError(false);
      setRows(await apiGet<Row[]>("/intake/submissions"));
    } catch {
      setError(true);
    } finally {
      setLoading(false);
      setRefreshing(false);
    }
  }, []);

  useFocusEffect(useCallback(() => { load(); }, [load]));

  const shown = filter ? rows.filter((r) => r.status === filter) : rows;

  return (
    <SafeAreaView style={s.root}>
      <View style={s.header}>
        <Pressable testID="intake-back" onPress={() => router.back()} hitSlop={10}>
          <Ionicons name="chevron-back" size={24} color="#fff" />
        </Pressable>
        <Text style={s.title}>Intake forms</Text>
      </View>
      <ScrollView
        contentContainerStyle={s.body}
        refreshControl={<RefreshControl refreshing={refreshing} onRefresh={() => { setRefreshing(true); load(); }} />}
      >
        <Text style={s.sectionTitle}>Enter a new one</Text>
        <Text style={s.hint}>Same questions as the forms on sistertosisterhomecare.com.</Text>
        <View style={{ gap: 8 }}>
          {NEW_FORMS.map((f) => (
            <Pressable key={f.key} testID={`intake-new-${f.key}`}
                       onPress={() => router.push({ pathname: "/intake/new", params: { form: f.key } } as any)}
                       style={s.newBtn}>
              <Ionicons name={f.icon} size={20} color={theme.colors.brandPrimary} />
              <Text style={s.newText}>{f.label}</Text>
              <Ionicons name="chevron-forward" size={18} color={theme.colors.muted} />
            </Pressable>
          ))}
        </View>

        <Text style={[s.sectionTitle, { marginTop: 18 }]}>Submissions</Text>
        <View style={s.chipsRow}>
          {["", ...STATUSES].map((st) => (
            <Pressable key={st || "all"} onPress={() => setFilter(st)} style={[s.chip, filter === st && s.chipOn]}>
              <Text style={[s.chipText, filter === st && { color: "#fff" }]}>{st ? st : "all"}</Text>
            </Pressable>
          ))}
        </View>
        {loading ? (
          <ActivityIndicator color={theme.colors.brandPrimary} style={{ marginTop: 20 }} />
        ) : error ? (
          <Text style={s.hint}>Couldn&apos;t load submissions. Pull down to retry.</Text>
        ) : shown.length === 0 ? (
          <Text style={s.hint}>Nothing here yet.</Text>
        ) : (
          shown.map((r) => (
            <Pressable key={r.id} testID={`intake-row-${r.id}`}
                       onPress={() => router.push({ pathname: "/intake/view", params: { id: r.id } } as any)}
                       style={s.row}>
              <View style={{ flex: 1 }}>
                <Text style={s.rowTitle} numberOfLines={1}>{r.summary}</Text>
                <Text style={s.rowSub} numberOfLines={1}>
                  {r.form_title} · {r.submitted_at.slice(0, 10)}{r.source === "website" ? " · website" : ""}
                </Text>
              </View>
              <Text style={[s.badge, r.status === "new" && s.badgeNew]}>{r.status}</Text>
            </Pressable>
          ))
        )}
      </ScrollView>
    </SafeAreaView>
  );
}

const s = StyleSheet.create({
  root: { flex: 1, backgroundColor: theme.colors.background },
  header: { backgroundColor: theme.colors.brandPrimary, padding: 14, flexDirection: "row", alignItems: "center", gap: 10 },
  title: { flex: 1, color: "#fff", fontWeight: "700", fontSize: 16 },
  body: { padding: 16, paddingBottom: 60, gap: 10 },
  sectionTitle: { fontSize: 13, fontWeight: "800", color: theme.colors.brandPrimary, textTransform: "uppercase", letterSpacing: 0.4 },
  hint: { fontSize: 12, color: theme.colors.muted },
  newBtn: { flexDirection: "row", alignItems: "center", gap: 10, backgroundColor: "#fff", padding: 14, borderRadius: 12, borderWidth: 1, borderColor: theme.colors.border },
  newText: { flex: 1, fontSize: 14, fontWeight: "700", color: theme.colors.onSurface },
  chipsRow: { flexDirection: "row", flexWrap: "wrap", gap: 6 },
  chip: { backgroundColor: "#fff", borderRadius: 999, paddingHorizontal: 14, paddingVertical: 10, minHeight: 40, justifyContent: "center", borderWidth: 1, borderColor: theme.colors.border },
  chipOn: { backgroundColor: theme.colors.brandPrimary, borderColor: theme.colors.brandPrimary },
  chipText: { fontSize: 12, fontWeight: "600", color: theme.colors.onSurface, textTransform: "capitalize" },
  row: { flexDirection: "row", alignItems: "center", gap: 10, backgroundColor: "#fff", padding: 14, minHeight: 56, borderRadius: 12, borderWidth: 1, borderColor: theme.colors.border },
  rowTitle: { fontSize: 14, fontWeight: "700", color: theme.colors.onSurface },
  rowSub: { fontSize: 12, color: theme.colors.muted, marginTop: 2 },
  badge: { fontSize: 11, fontWeight: "700", color: theme.colors.onSurface, backgroundColor: theme.colors.surfaceTertiary, paddingHorizontal: 8, paddingVertical: 4, borderRadius: 999, overflow: "hidden", textTransform: "capitalize" },
  badgeNew: { backgroundColor: theme.colors.brandPrimary, color: "#fff" },
});
