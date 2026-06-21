import { useCallback, useEffect, useRef, useState } from "react";
import {
  View, Text, StyleSheet, TextInput, Pressable, KeyboardAvoidingView, Platform, ScrollView,
} from "react-native";
import { Ionicons } from "@expo/vector-icons";
import { SafeAreaView } from "react-native-safe-area-context";
import { useLocalSearchParams, useRouter } from "expo-router";
import { apiGet, apiPost } from "@/src/api/client";
import { useAuth } from "@/src/context/AuthContext";
import { theme } from "@/src/theme";

type Msg = { id: string; from_id: string; from_name: string; to_id: string; text: string; created_at: string };

export default function ChatThread() {
  const { id: otherId } = useLocalSearchParams<{ id: string }>();
  const router = useRouter();
  const { user } = useAuth();
  const [messages, setMessages] = useState<Msg[]>([]);
  const [text, setText] = useState("");
  const [otherName, setOtherName] = useState("");
  const scrollRef = useRef<ScrollView>(null);

  const load = useCallback(async () => {
    if (!otherId) return;
    const msgs = await apiGet<Msg[]>(`/chat/messages?with=${otherId}`);
    setMessages(msgs);
    if (msgs.length) {
      const m = msgs[msgs.length - 1];
      setOtherName(m.from_id === user?.id ? m.to_id : m.from_name);
    }
  }, [otherId, user?.id]);

  useEffect(() => { load(); }, [load]);
  useEffect(() => {
    const t = setInterval(load, 4000);
    return () => clearInterval(t);
  }, [load]);
  useEffect(() => { scrollRef.current?.scrollToEnd({ animated: true }); }, [messages]);

  const send = async () => {
    const t = text.trim();
    if (!t || !otherId) return;
    setText("");
    await apiPost("/chat/messages", { to_user_id: otherId, text: t });
    load();
  };

  return (
    <SafeAreaView edges={["top"]} style={styles.root}>
      <View style={styles.header}>
        <Pressable onPress={() => router.back()} hitSlop={10}>
          <Ionicons name="chevron-back" size={26} color={theme.colors.onSurface} />
        </Pressable>
        <View style={{ flex: 1, alignItems: "center" }}>
          <Text style={styles.title}>{otherName || "Conversation"}</Text>
        </View>
        <View style={{ width: 26 }} />
      </View>

      <KeyboardAvoidingView
        behavior={Platform.OS === "ios" ? "padding" : "height"}
        style={{ flex: 1 }}
        keyboardVerticalOffset={Platform.OS === "ios" ? 90 : 0}
      >
        <ScrollView ref={scrollRef} contentContainerStyle={{ padding: 16, gap: 8 }}>
          {messages.map((m) => {
            const mine = m.from_id === user?.id;
            return (
              <View
                key={m.id}
                testID={`msg-${m.id}`}
                style={[styles.bubble, mine ? styles.mine : styles.theirs]}
              >
                <Text style={mine ? styles.mineText : styles.theirsText}>{m.text}</Text>
                <Text style={[styles.timeText, mine && { color: "rgba(255,255,255,0.6)" }]}>
                  {new Date(m.created_at).toLocaleTimeString([], { hour: "2-digit", minute: "2-digit" })}
                </Text>
              </View>
            );
          })}
          {messages.length === 0 && (
            <Text style={styles.empty}>Start the conversation.</Text>
          )}
        </ScrollView>

        <View style={styles.inputBar}>
          <TextInput
            testID="chat-input"
            value={text}
            onChangeText={setText}
            placeholder="Type a message…"
            placeholderTextColor={theme.colors.muted}
            style={styles.input}
            multiline
          />
          <Pressable
            testID="chat-send"
            onPress={send}
            disabled={!text.trim()}
            style={[styles.sendBtn, !text.trim() && { opacity: 0.5 }]}
          >
            <Ionicons name="arrow-up" size={20} color="#fff" />
          </Pressable>
        </View>
      </KeyboardAvoidingView>
    </SafeAreaView>
  );
}

const styles = StyleSheet.create({
  root: { flex: 1, backgroundColor: theme.colors.surface },
  header: { flexDirection: "row", alignItems: "center", paddingHorizontal: 16, paddingVertical: 10, gap: 8, borderBottomWidth: 1, borderBottomColor: theme.colors.divider },
  title: { fontSize: 16, fontWeight: "700", color: theme.colors.onSurface },
  bubble: { maxWidth: "82%", paddingHorizontal: 14, paddingVertical: 8, borderRadius: 18 },
  mine: { alignSelf: "flex-end", backgroundColor: theme.colors.brandPrimary, borderBottomRightRadius: 4 },
  theirs: { alignSelf: "flex-start", backgroundColor: theme.colors.surfaceSecondary, borderBottomLeftRadius: 4, borderWidth: 1, borderColor: theme.colors.border },
  mineText: { color: "#fff", fontSize: 15, lineHeight: 20 },
  theirsText: { color: theme.colors.onSurface, fontSize: 15, lineHeight: 20 },
  timeText: { fontSize: 10, color: theme.colors.muted, marginTop: 4, alignSelf: "flex-end" },
  empty: { textAlign: "center", color: theme.colors.muted, marginTop: 40 },
  inputBar: { flexDirection: "row", gap: 10, padding: 12, borderTopWidth: 1, borderTopColor: theme.colors.divider, backgroundColor: theme.colors.surfaceSecondary, alignItems: "flex-end" },
  input: { flex: 1, maxHeight: 120, backgroundColor: theme.colors.surfaceTertiary, borderRadius: 22, paddingHorizontal: 16, paddingVertical: 12, fontSize: 15, color: theme.colors.onSurface },
  sendBtn: { width: 44, height: 44, borderRadius: 22, backgroundColor: theme.colors.brandPrimary, alignItems: "center", justifyContent: "center" },
});
