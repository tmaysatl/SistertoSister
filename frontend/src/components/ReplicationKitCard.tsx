import { useState } from "react";
import {
  View, Text, StyleSheet, Pressable, ActivityIndicator, Alert, Platform,
} from "react-native";
import { Ionicons } from "@expo/vector-icons";
import * as Linking from "expo-linking";
import * as FileSystem from "expo-file-system/legacy";
import * as Sharing from "expo-sharing";
import AsyncStorage from "@react-native-async-storage/async-storage";
import { API_BASE } from "@/src/api/client";
import { theme } from "@/src/theme";

const ITEMS = [
  {
    key: "playbook",
    title: "Replication Playbook",
    sub: "Branded reference PDF \u00b7 accounts, systems, tweak checklist, costs",
    icon: "book-outline" as const,
    path: "/reports/replication-playbook.pdf",
    filename: "Agency_Replication_Playbook.pdf",
  },
  {
    key: "intake",
    title: "Replication Intake Form",
    sub: "Fillable PDF for new agencies to complete and send back",
    icon: "document-text-outline" as const,
    path: "/reports/replication-intake-form.pdf",
    filename: "Agency_Replication_IntakeForm.pdf",
  },
];

export function ReplicationKitCard() {
  const [loading, setLoading] = useState<string | null>(null);

  const download = async (item: typeof ITEMS[number]) => {
    setLoading(item.key);
    try {
      const token = await AsyncStorage.getItem("auth_token");
      if (Platform.OS === "web") {
        // Web: open in new tab with token in fetch headers — easier to just open the URL with a token redirect
        const res = await fetch(`${API_BASE}${item.path}`, {
          headers: { Authorization: `Bearer ${token}` },
        });
        if (!res.ok) throw new Error(`HTTP ${res.status}`);
        const blob = await res.blob();
        const url = URL.createObjectURL(blob);
        const a = document.createElement("a");
        a.href = url;
        a.download = item.filename;
        document.body.appendChild(a);
        a.click();
        a.remove();
        setTimeout(() => URL.revokeObjectURL(url), 1500);
      } else {
        // Native: download to cache then share/open
        const localPath = `${FileSystem.cacheDirectory}${item.filename}`;
        const dl = await FileSystem.downloadAsync(
          `${API_BASE}${item.path}`,
          localPath,
          { headers: { Authorization: `Bearer ${token}` } },
        );
        if (!dl.uri) throw new Error("Download failed");
        if (await Sharing.isAvailableAsync()) {
          await Sharing.shareAsync(dl.uri, { mimeType: "application/pdf",
                                            UTI: "com.adobe.pdf",
                                            dialogTitle: item.title });
        } else {
          await Linking.openURL(dl.uri);
        }
      }
    } catch (e: any) {
      Alert.alert("Download failed", e?.message || "Try again later.");
    } finally {
      setLoading(null);
    }
  };

  return (
    <View style={styles.card}>
      <View style={{ flexDirection: "row", alignItems: "center", gap: 8 }}>
        <View style={styles.iconCircle}>
          <Ionicons name="layers-outline" size={18} color={theme.colors.brandPrimary} />
        </View>
        <View style={{ flex: 1 }}>
          <Text style={styles.title}>Replication Kit</Text>
          <Text style={styles.sub}>Downloadable docs to scale this app to other agencies.</Text>
        </View>
      </View>

      <View style={{ gap: 8, marginTop: 6 }}>
        {ITEMS.map((it) => (
          <Pressable
            key={it.key}
            testID={`replication-${it.key}`}
            onPress={() => download(it)}
            disabled={loading !== null}
            style={[styles.row, loading === it.key && { opacity: 0.6 }]}
          >
            <View style={styles.rowIcon}>
              <Ionicons name={it.icon} size={18} color={theme.colors.brandPrimary} />
            </View>
            <View style={{ flex: 1 }}>
              <Text style={styles.rowTitle}>{it.title}</Text>
              <Text style={styles.rowSub} numberOfLines={2}>{it.sub}</Text>
            </View>
            {loading === it.key ? (
              <ActivityIndicator color={theme.colors.brandPrimary} />
            ) : (
              <Ionicons name="download-outline" size={18} color={theme.colors.brandPrimary} />
            )}
          </Pressable>
        ))}
      </View>
    </View>
  );
}

const styles = StyleSheet.create({
  card: {
    backgroundColor: theme.colors.surface,
    marginHorizontal: 16,
    marginTop: 8,
    marginBottom: 4,
    padding: 14,
    borderRadius: 14,
    borderWidth: 1,
    borderColor: theme.colors.border,
    gap: 8,
  },
  iconCircle: {
    width: 32, height: 32, borderRadius: 16,
    backgroundColor: theme.colors.brandTertiary,
    alignItems: "center", justifyContent: "center",
  },
  title: { fontSize: 14, fontWeight: "700", color: theme.colors.onSurface ?? "#1d2421" },
  sub: { fontSize: 12, color: theme.colors.muted },
  row: {
    flexDirection: "row", alignItems: "center", gap: 10,
    padding: 10, borderRadius: 10,
    backgroundColor: theme.colors.surfaceSecondary,
    borderWidth: 1, borderColor: theme.colors.border,
  },
  rowIcon: {
    width: 32, height: 32, borderRadius: 16,
    backgroundColor: "#fff",
    alignItems: "center", justifyContent: "center",
    borderWidth: 1, borderColor: theme.colors.border,
  },
  rowTitle: { fontSize: 13, fontWeight: "700", color: theme.colors.onSurface ?? "#1d2421" },
  rowSub: { fontSize: 11, color: theme.colors.muted, marginTop: 2 },
});
