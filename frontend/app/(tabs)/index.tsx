import { useCallback, useState } from "react";
import {
  View, Text, StyleSheet, ScrollView, Pressable, RefreshControl, ActivityIndicator,
} from "react-native";
import { Image } from "expo-image";
import { LinearGradient } from "expo-linear-gradient";
import { Ionicons } from "@expo/vector-icons";
import { SafeAreaView } from "react-native-safe-area-context";
import { useFocusEffect, useRouter } from "expo-router";
import { useAuth } from "@/src/context/AuthContext";
import { apiGet } from "@/src/api/client";
import { theme, HERO_IMAGE, BRAND_NAME, LOGO_URL } from "@/src/theme";

type Stats = {
  total_clients: number;
  total_caregivers: number;
  total_documents: number;
  total_assignments: number;
  total_training: number;
  audit_readiness: number;
  pending_onboarding: number;
  pending_training: number;
  onboarding_pct: number;
  training_pct: number;
};

export default function Dashboard() {
  const { user, logout } = useAuth();
  const router = useRouter();
  const [stats, setStats] = useState<Stats | null>(null);
  const [loading, setLoading] = useState(false);

  const load = useCallback(async () => {
    try {
      setLoading(true);
      const s = await apiGet<Stats>("/stats");
      setStats(s);
    } catch (e) {
      console.log("stats error", e);
    } finally {
      setLoading(false);
    }
  }, []);

  useFocusEffect(useCallback(() => { load(); }, [load]));

  const readiness = stats?.audit_readiness ?? 0;
  const readinessColor =
    readiness >= 80 ? theme.colors.success :
    readiness >= 60 ? theme.colors.warning : theme.colors.error;

  return (
    <View style={styles.root}>
      <ScrollView
        contentContainerStyle={{ paddingBottom: 32 }}
        refreshControl={<RefreshControl refreshing={loading} onRefresh={load} />}
      >
        <View style={styles.heroWrap}>
          <Image source={{ uri: HERO_IMAGE }} style={styles.hero} contentFit="cover" />
          <LinearGradient
            colors={["rgba(17,20,18,0.2)", "rgba(17,20,18,0.85)"]}
            style={StyleSheet.absoluteFill}
          />
          <SafeAreaView edges={["top"]} style={styles.heroSafe}>
            <View style={styles.heroHeader}>
              <View style={styles.heroBrand}>
                <Image source={{ uri: LOGO_URL }} style={styles.heroLogo} contentFit="contain" />
                <View>
                  <Text style={styles.heroGreeting}>
                    {user?.role === "admin" ? BRAND_NAME : "Caregiver"}
                  </Text>
                  <Text style={styles.heroName}>{user?.name}</Text>
                </View>
              </View>
              <View style={{ flexDirection: "row", gap: 8 }}>
                {user?.role === "caregiver" && (
                  <Pressable
                    testID="open-my-profile"
                    onPress={() => router.push(`/caregiver/${user.id}`)}
                    style={styles.iconBtn}
                  >
                    <Ionicons name="person-outline" size={20} color="#fff" />
                  </Pressable>
                )}
                <Pressable
                  testID="open-chat-button"
                  onPress={() => router.push("/chat")}
                  style={styles.iconBtn}
                >
                  <Ionicons name="chatbubbles-outline" size={20} color="#fff" />
                </Pressable>
                <Pressable testID="logout-button" onPress={logout} style={styles.iconBtn}>
                  <Ionicons name="log-out-outline" size={20} color="#fff" />
                </Pressable>
              </View>
            </View>

            <View style={styles.readinessCard} testID="audit-readiness-card">
              <Text style={styles.readinessLabel}>Audit Readiness</Text>
              <View style={styles.readinessRow}>
                <Text style={[styles.readinessValue, { color: readinessColor }]}>
                  {readiness}%
                </Text>
                <View style={[styles.readinessBadge, { backgroundColor: readinessColor }]}>
                  <Ionicons
                    name={readiness >= 80 ? "shield-checkmark" : "alert-circle"}
                    size={14} color="#fff"
                  />
                  <Text style={styles.readinessBadgeText}>
                    {readiness >= 80 ? "Audit ready" : "Action needed"}
                  </Text>
                </View>
              </View>
              <View style={styles.progressTrack}>
                <View style={[styles.progressFill, { width: `${readiness}%`, backgroundColor: readinessColor }]} />
              </View>
              <View style={styles.subStats}>
                <Text style={styles.subStat}>
                  Onboarding {stats?.onboarding_pct ?? 0}%
                </Text>
                <Text style={styles.subStatDot}>·</Text>
                <Text style={styles.subStat}>
                  Training {stats?.training_pct ?? 0}%
                </Text>
              </View>
            </View>
          </SafeAreaView>
        </View>

        <View style={styles.section}>
          <Text style={styles.sectionTitle}>Overview</Text>
          <View style={styles.grid}>
            <StatCard
              icon="people" label="Clients"
              value={stats?.total_clients ?? 0}
              onPress={() => router.push("/(tabs)/team")}
              testID="stat-clients"
            />
            <StatCard
              icon="medkit" label="Caregivers"
              value={stats?.total_caregivers ?? 0}
              onPress={() => router.push("/(tabs)/team")}
              testID="stat-caregivers"
            />
            <StatCard
              icon="document-text" label="Documents"
              value={stats?.total_documents ?? 0}
              onPress={() => router.push("/(tabs)/documents")}
              testID="stat-documents"
            />
            <StatCard
              icon="school" label="Trainings"
              value={stats?.total_training ?? 0}
              onPress={() => router.push("/training")}
              testID="stat-trainings"
            />
          </View>
        </View>

        {(stats?.pending_onboarding || stats?.pending_training) ? (
          <View style={styles.section}>
            <Text style={styles.sectionTitle}>Action required</Text>
            {!!stats?.pending_onboarding && (
              <ActionRow
                icon="checkbox-outline"
                title="Onboarding tasks"
                subtitle={`${stats.pending_onboarding} step${stats.pending_onboarding === 1 ? "" : "s"} pending`}
                onPress={() => router.push("/(tabs)/team")}
                testID="action-onboarding"
              />
            )}
            {!!stats?.pending_training && (
              <ActionRow
                icon="play-circle-outline"
                title="Training completion"
                subtitle={`${stats.pending_training} caregiver-training${stats.pending_training === 1 ? "" : "s"} outstanding`}
                onPress={() => router.push("/training")}
                testID="action-training"
              />
            )}
          </View>
        ) : null}

        {loading && !stats && (
          <ActivityIndicator style={{ marginTop: 40 }} color={theme.colors.brandPrimary} />
        )}
      </ScrollView>
    </View>
  );
}

function StatCard({ icon, label, value, onPress, testID }: any) {
  return (
    <Pressable onPress={onPress} style={styles.statCard} testID={testID}>
      <View style={styles.statIcon}>
        <Ionicons name={icon} size={18} color={theme.colors.brandPrimary} />
      </View>
      <Text style={styles.statValue}>{value}</Text>
      <Text style={styles.statLabel}>{label}</Text>
    </Pressable>
  );
}

function ActionRow({ icon, title, subtitle, onPress, testID }: any) {
  return (
    <Pressable onPress={onPress} style={styles.actionRow} testID={testID}>
      <View style={styles.actionIcon}>
        <Ionicons name={icon} size={18} color={theme.colors.brandPrimary} />
      </View>
      <View style={{ flex: 1 }}>
        <Text style={styles.actionTitle}>{title}</Text>
        <Text style={styles.actionSubtitle}>{subtitle}</Text>
      </View>
      <Ionicons name="chevron-forward" size={18} color={theme.colors.muted} />
    </Pressable>
  );
}

const styles = StyleSheet.create({
  root: { flex: 1, backgroundColor: theme.colors.surface },
  heroWrap: { height: 320, position: "relative" },
  hero: { ...StyleSheet.absoluteFillObject },
  heroSafe: { flex: 1, paddingHorizontal: 20, paddingTop: 8, justifyContent: "space-between" },
  heroHeader: { flexDirection: "row", justifyContent: "space-between", alignItems: "center" },
  heroBrand: { flexDirection: "row", alignItems: "center", gap: 10, flex: 1 },
  heroLogo: { width: 48, height: 48 },
  heroGreeting: { color: "rgba(255,255,255,0.7)", fontSize: 12, fontWeight: "600", textTransform: "uppercase", letterSpacing: 1 },
  heroName: { color: "#fff", fontSize: 22, fontWeight: "700", marginTop: 2 },
  iconBtn: {
    width: 38, height: 38, borderRadius: 19,
    backgroundColor: "rgba(255,255,255,0.18)",
    alignItems: "center", justifyContent: "center",
  },
  readinessCard: {
    backgroundColor: "rgba(255,255,255,0.96)",
    borderRadius: 20, padding: 18, marginBottom: 16,
  },
  readinessLabel: { fontSize: 12, fontWeight: "700", letterSpacing: 1, color: theme.colors.muted, textTransform: "uppercase" },
  readinessRow: { flexDirection: "row", alignItems: "center", justifyContent: "space-between", marginTop: 4 },
  readinessValue: { fontSize: 44, fontWeight: "800", letterSpacing: -1 },
  readinessBadge: {
    flexDirection: "row", alignItems: "center", gap: 4,
    paddingHorizontal: 10, paddingVertical: 6, borderRadius: 999,
  },
  readinessBadgeText: { color: "#fff", fontSize: 11, fontWeight: "700" },
  progressTrack: { height: 6, backgroundColor: theme.colors.surfaceTertiary, borderRadius: 999, marginTop: 12, overflow: "hidden" },
  progressFill: { height: "100%", borderRadius: 999 },
  subStats: { flexDirection: "row", marginTop: 10, gap: 6 },
  subStat: { fontSize: 12, color: theme.colors.onSurfaceTertiary, fontWeight: "600" },
  subStatDot: { color: theme.colors.muted },
  section: { paddingHorizontal: 20, marginTop: 20 },
  sectionTitle: { fontSize: 13, fontWeight: "700", letterSpacing: 1, color: theme.colors.muted, textTransform: "uppercase", marginBottom: 12 },
  grid: { flexDirection: "row", flexWrap: "wrap", justifyContent: "space-between" },
  statCard: {
    width: "48%", backgroundColor: theme.colors.surfaceSecondary,
    borderRadius: 16, padding: 16, marginBottom: 12,
    borderWidth: 1, borderColor: theme.colors.border,
  },
  statIcon: {
    width: 36, height: 36, borderRadius: 10,
    backgroundColor: theme.colors.brandTertiary,
    alignItems: "center", justifyContent: "center", marginBottom: 12,
  },
  statValue: { fontSize: 26, fontWeight: "700", color: theme.colors.onSurface },
  statLabel: { fontSize: 13, color: theme.colors.muted, marginTop: 2, fontWeight: "500" },
  actionRow: {
    flexDirection: "row", alignItems: "center", gap: 12,
    backgroundColor: theme.colors.surfaceSecondary, padding: 14, borderRadius: 14,
    borderWidth: 1, borderColor: theme.colors.border, marginBottom: 10,
  },
  actionIcon: {
    width: 36, height: 36, borderRadius: 10,
    backgroundColor: theme.colors.brandTertiary,
    alignItems: "center", justifyContent: "center",
  },
  actionTitle: { fontSize: 15, fontWeight: "600", color: theme.colors.onSurface },
  actionSubtitle: { fontSize: 12, color: theme.colors.muted, marginTop: 2 },
});
