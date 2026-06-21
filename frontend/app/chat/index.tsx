import { useCallback, useState } from "react";
import { View, Text, StyleSheet, FlatList, Pressable, ActivityIndicator, RefreshControl } from "react-native";
import { Image } from "expo-image";
import { Ionicons } from "@expo/vector-icons";
import { SafeAreaView } from "react-native-safe-area-context";
import { useFocusEffect, useRouter } from "expo-router";
import { apiGet } from "@/src/api/client";
import { theme } from "@/src/theme";

type Thread = { other_id: string; other_name: string; last_message: string; last_at: string; unread: number; photo_base64?: string; role?: string };
type Contact = { id: string; name: string; email: string; role: string; photo_base64?: string };

export default function ChatList() {
  const router = useRouter();
  const [threads, setThreads] = useState<Thread[]>([]);
  const [contacts, setContacts] = useState<Contact[]>([]);
  const [loading, setLoading] = useState(false);
  const [showNew, setShowNew] = useState(false);

  const load = useCallback(async () => {
    setLoading(true);
    try {
      const [t, c] = await Promise.all([
        apiGet<Thread[]>("/chat/threads"),
        apiGet<Contact[]>("/chat/contacts"),
      ]);
      setThreads(t); setContacts(c);
    } finally { setLoading(false); }
  }, []);

  useFocusEffect(useCallback(() => { load(); }, [load]));

  const open = (id: string) => router.push(`/chat/${id}`);

  return (
    <SafeAreaView edges={["top"]} style={styles.root}>
      <View style={styles.header}>
        <Pressable onPress={() => router.back()} hitSlop={10}>
          <Ionicons name="chevron-back" size={26} color={theme.colors.onSurface} />
        </Pressable>
        <Text style={styles.title}>Messages</Text>
        <Pressable testID="chat-new" onPress={() => setShowNew(!showNew)} hitSlop={10}>
          <Ionicons name={showNew ? "close" : "create-outline"} size={22} color={theme.colors.brandPrimary} />
        </Pressable>
      </View>

      {showNew && (
        <View style={styles.contactsBox}>
          <Text style={styles.section}>Start a conversation</Text>
          {contacts.map((c) => (
            <Pressable key={c.id} onPress={() => { setShowNew(false); open(c.id); }} style={styles.row}>
              {c.photo_base64 ? (
                <Image source={{ uri: `data:image/jpeg;base64,${c.photo_base64}` }} style={styles.avatar} contentFit="cover" />
              ) : (
                <View style={[styles.avatar, styles.avatarPh]}>
                  <Ionicons name="person" size={18} color={theme.colors.brandPrimary} />
                </View>
              )}
              <View style={{ flex: 1 }}>
                <Text style={styles.name}>{c.name}</Text>
                <Text style={styles.last}>{c.email}</Text>
              </View>
              <Ionicons name="chevron-forward" size={18} color={theme.colors.muted} />
            </Pressable>
          ))}
        </View>
      )}

      <FlatList
        data={threads}
        keyExtractor={(i) => i.other_id}
        contentContainerStyle={{ padding: 16, gap: 10 }}
        refreshControl={<RefreshControl refreshing={loading} onRefresh={load} />}
        ListEmptyComponent={!loading ? (
          <View style={styles.empty}>
            <Ionicons name="chatbubbles-outline" size={48} color={theme.colors.brandPrimary} />
            <Text style={styles.emptyTitle}>No conversations yet</Text>
            <Text style={styles.emptySub}>Tap the pencil to start one with a caregiver or admin.</Text>
          </View>
        ) : null}
        renderItem={({ item }) => (
          <Pressable testID={`thread-${item.other_id}`} onPress={() => open(item.other_id)} style={styles.row}>
            {item.photo_base64 ? (
              <Image source={{ uri: `data:image/jpeg;base64,${item.photo_base64}` }} style={styles.avatar} contentFit="cover" />
            ) : (
              <View style={[styles.avatar, styles.avatarPh]}>
                <Ionicons name="person" size={18} color={theme.colors.brandPrimary} />
              </View>
            )}
            <View style={{ flex: 1 }}>
              <View style={{ flexDirection: "row", justifyContent: "space-between" }}>
                <Text style={styles.name}>{item.other_name}</Text>
                <Text style={styles.timeText}>{new Date(item.last_at).toLocaleDateString()}</Text>
              </View>
              <Text style={styles.last} numberOfLines={1}>{item.last_message}</Text>
            </View>
            {item.unread > 0 && (
              <View style={styles.unread}><Text style={styles.unreadText}>{item.unread}</Text></View>
            )}
          </Pressable>
        )}
      />
    </SafeAreaView>
  );
}

const styles = StyleSheet.create({
  root: { flex: 1, backgroundColor: theme.colors.surface },
  header: { flexDirection: "row", alignItems: "center", justifyContent: "space-between", paddingHorizontal: 16, paddingVertical: 10 },
  title: { fontSize: 18, fontWeight: "700", color: theme.colors.onSurface },
  contactsBox: { paddingHorizontal: 16, paddingBottom: 12, gap: 8 },
  section: { fontSize: 11, fontWeight: "700", color: theme.colors.muted, textTransform: "uppercase", letterSpacing: 0.8, marginTop: 4 },
  row: { flexDirection: "row", alignItems: "center", gap: 12, padding: 12, backgroundColor: theme.colors.surfaceSecondary, borderRadius: 12, borderWidth: 1, borderColor: theme.colors.border },
  avatar: { width: 44, height: 44, borderRadius: 22 },
  avatarPh: { backgroundColor: theme.colors.brandTertiary, alignItems: "center", justifyContent: "center" },
  name: { fontSize: 14, fontWeight: "700", color: theme.colors.onSurface },
  last: { fontSize: 12, color: theme.colors.muted, marginTop: 2 },
  timeText: { fontSize: 11, color: theme.colors.muted },
  unread: { backgroundColor: theme.colors.brandPrimary, paddingHorizontal: 8, paddingVertical: 3, borderRadius: 999 },
  unreadText: { color: "#fff", fontSize: 11, fontWeight: "800" },
  empty: { alignItems: "center", paddingVertical: 80, gap: 6 },
  emptyTitle: { fontSize: 16, fontWeight: "700", color: theme.colors.onSurface, marginTop: 8 },
  emptySub: { fontSize: 13, color: theme.colors.muted, textAlign: "center", paddingHorizontal: 40 },
});
