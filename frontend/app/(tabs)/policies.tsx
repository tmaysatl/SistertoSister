import { useCallback, useState } from "react";
import {
  View, Text, StyleSheet, ScrollView, Pressable, ActivityIndicator,
  RefreshControl,
} from "react-native";
import { Ionicons } from "@expo/vector-icons";
import { SafeAreaView } from "react-native-safe-area-context";
import { useFocusEffect } from "expo-router";
import { useAuth } from "@/src/context/AuthContext";
import { apiGet, apiPost, apiDelete } from "@/src/api/client";
import { PdfViewerModal } from "@/src/components/pdf/PdfViewerModal";
import { theme } from "@/src/theme";

type Policy = {
  id: string;
  title: string;
  notes?: string;
  uploaded_at: string;
  seq?: number | null;
  storage_path?: string | null;
  file_base64?: string | null;
};

type Ack = {
  id: string;
  policy_id: string;
  policy_title: string;
  user_id: string;
  user_name: string;
  acknowledged_at: string;
};

export default function Policies() {
  const { user } = useAuth();
  const [policies, setPolicies] = useState<Policy[]>([]);
  const [acks, setAcks] = useState<Ack[]>([]);
  const [loading, setLoading] = useState(true);
  const [refreshing, setRefreshing] = useState(false);
  const [busyId, setBusyId] = useState<string | null>(null);
  const [viewing, setViewing] = useState<Policy | null>(null);

  const load = useCallback(async () => {
    try {
      const [p, a] = await Promise.all([
        apiGet<Policy[]>("/documents?category=policy"),
        apiGet<Ack[]>("/policies/acknowledgments"),
      ]);
      p.sort((x, y) => (x.seq ?? 999) - (y.seq ?? 999));
      setPolicies(p);
      setAcks(a);
    } catch (e) { console.log("policies load", e); }
    finally { setLoading(false); setRefreshing(false); }
  }, []);

  useFocusEffect(useCallback(() => { load(); }, [load]));

  const myAckFor = (pid: string) =>
    acks.find((a) => a.policy_id === pid && a.user_id === user?.id);

  const toggle = async (pol: Policy) => {
    setBusyId(pol.id);
    try {
      const existing = myAckFor(pol.id);
      if (existing) {
        await apiDelete(`/policies/acknowledge/${pol.id}`);
      } else {
        await apiPost(`/policies/acknowledge`, { policy_id: pol.id });
      }
      await load();
    } catch (e) { console.log("ack err", e); }
    finally { setBusyId(null); }
  };

  const total = policies.length;
  const acked = policies.filter((p) => myAckFor(p.id)).length;
  const pct = total > 0 ? Math.round((acked / total) * 100) : 0;

  if (loading) {
    return (
      <SafeAreaView style={styles.safe} edges={["top"]}>
        <View style={styles.center}><ActivityIndicator color={theme.colors.brandPrimary} /></View>
      </SafeAreaView>
    );
  }

  return (
    <SafeAreaView style={styles.safe} edges={["top"]}>
      <View style={styles.header}>
        <Text style={styles.title}>Policies</Text>
        <Text style={styles.subtitle}>Review each policy and acknowledge that you have read and understood it.</Text>
      </View>

      <View style={styles.progressCard}>
        <View style={styles.progressRow}>
          <Text style={styles.progressTitle}>Your acknowledgments</Text>
          <Text style={styles.progressCount}>{acked} / {total}</Text>
        </View>
        <View style={styles.progressBar}>
          <View style={[styles.progressFill, { width: `${pct}%` }]} />
        </View>
      </View>

      <ScrollView
        contentContainerStyle={{ padding: 16, paddingBottom: 80, gap: 10 }}
        refreshControl={<RefreshControl refreshing={refreshing} onRefresh={() => { setRefreshing(true); load(); }} tintColor={theme.colors.brandPrimary} />}
      >
        {policies.length === 0 && (
          <Text style={styles.empty}>No policies seeded yet.</Text>
        )}
        {policies.map((p) => {
          const a = myAckFor(p.id);
          const acked = !!a;
          // Same gate documents.tsx uses: only offer to open the viewer when
          // a file actually exists, so a stub policy (no file attached)
          // can't open a broken/blank PdfViewerModal.
          const hasFile = !!(p.storage_path || p.file_base64);
          return (
            <Pressable
              key={p.id}
              testID={`policy-ack-${p.id}`}
              onPress={() => toggle(p)}
              disabled={busyId === p.id}
              style={[styles.card, acked && styles.cardOn]}
            >
              <View style={[styles.checkbox, acked && styles.checkboxOn]}>
                {busyId === p.id ? (
                  <ActivityIndicator color={acked ? "#fff" : theme.colors.brandPrimary} size="small" />
                ) : acked ? (
                  <Ionicons name="checkmark" size={18} color="#fff" />
                ) : null}
              </View>
              <View style={{ flex: 1 }}>
                <Text style={styles.cardTitle}>{p.title}</Text>
                {acked ? (
                  <Text style={styles.cardSubOk}>
                    Acknowledged {new Date(a!.acknowledged_at).toLocaleString()}
                  </Text>
                ) : hasFile ? (
                  <Text style={styles.cardSub}>Tap Read to view, then check to acknowledge.</Text>
                ) : (
                  <Text style={styles.cardSub}>Not yet available — check back soon.</Text>
                )}
              </View>
              {hasFile ? (
                <Pressable
                  testID={`policy-read-${p.id}`}
                  onPress={(e) => { e.stopPropagation(); setViewing(p); }}
                  hitSlop={8}
                  style={styles.readBtn}
                >
                  <Ionicons name="document-text-outline" size={16} color={theme.colors.brandPrimary} />
                  <Text style={styles.readBtnText}>Read</Text>
                </Pressable>
              ) : (
                <View style={[styles.readBtn, styles.readBtnDisabled]}>
                  <Ionicons name="time-outline" size={16} color={theme.colors.muted} />
                  <Text style={styles.readBtnTextDisabled}>Pending</Text>
                </View>
              )}
            </Pressable>
          );
        })}
      </ScrollView>

      <PdfViewerModal
        visible={!!viewing}
        onClose={() => setViewing(null)}
        title={viewing?.title || ""}
        path={viewing ? `/documents/${viewing.id}/stamped` : null}
      />
    </SafeAreaView>
  );
}

const styles = StyleSheet.create({
  safe: { flex: 1, backgroundColor: theme.colors.background },
  center: { flex: 1, alignItems: "center", justifyContent: "center" },
  header: { paddingHorizontal: 16, paddingTop: 12, paddingBottom: 6, gap: 4 },
  title: { fontSize: 26, fontWeight: "800", color: theme.colors.onSurface, letterSpacing: -0.4 },
  subtitle: { fontSize: 12, color: theme.colors.muted },
  progressCard: {
    marginHorizontal: 16, marginTop: 4, padding: 14,
    backgroundColor: theme.colors.surface, borderRadius: 14,
    borderWidth: 1, borderColor: theme.colors.border, gap: 10,
  },
  progressRow: { flexDirection: "row", justifyContent: "space-between", alignItems: "center" },
  progressTitle: { fontSize: 13, fontWeight: "700", color: theme.colors.onSurface },
  progressCount: { fontSize: 13, fontWeight: "700", color: theme.colors.brandPrimary },
  progressBar: { height: 6, borderRadius: 3, backgroundColor: theme.colors.surfaceSecondary, overflow: "hidden" },
  progressFill: { height: "100%", backgroundColor: theme.colors.success },
  empty: { textAlign: "center", color: theme.colors.muted, paddingVertical: 24 },
  card: {
    flexDirection: "row", alignItems: "center", gap: 12,
    padding: 14, backgroundColor: theme.colors.surface,
    borderRadius: 14, borderWidth: 1, borderColor: theme.colors.border,
  },
  cardOn: { backgroundColor: theme.colors.brandTertiary, borderColor: theme.colors.brandPrimary },
  cardTitle: { fontSize: 14, fontWeight: "700", color: theme.colors.onSurface },
  cardSub: { fontSize: 12, color: theme.colors.muted, marginTop: 2 },
  cardSubOk: { fontSize: 12, color: theme.colors.success, marginTop: 2, fontWeight: "600" },
  checkbox: {
    width: 28, height: 28, borderRadius: 14, borderWidth: 2,
    borderColor: theme.colors.borderStrong, alignItems: "center", justifyContent: "center",
  },
  checkboxOn: { backgroundColor: theme.colors.success, borderColor: theme.colors.success },
  readBtn: {
    flexDirection: "row", alignItems: "center", gap: 4,
    paddingHorizontal: 10, paddingVertical: 7, borderRadius: 999,
    backgroundColor: theme.colors.brandTertiary,
    borderWidth: 1, borderColor: theme.colors.brandPrimary,
  },
  readBtnText: { fontSize: 12, fontWeight: "700", color: theme.colors.brandPrimary },
  readBtnDisabled: { backgroundColor: theme.colors.surfaceTertiary, borderColor: theme.colors.border },
  readBtnTextDisabled: { fontSize: 12, fontWeight: "700", color: theme.colors.muted },
});
