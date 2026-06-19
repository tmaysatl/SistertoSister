import { useCallback, useEffect, useRef, useState } from "react";
import {
  View, Text, StyleSheet, ScrollView, Pressable, TextInput,
  KeyboardAvoidingView, Platform, ActivityIndicator,
} from "react-native";
import { Ionicons } from "@expo/vector-icons";
import { SafeAreaView } from "react-native-safe-area-context";
import AsyncStorage from "@react-native-async-storage/async-storage";
import { API_BASE } from "@/src/api/client";
import { theme } from "@/src/theme";

type Msg = { id: string; role: "user" | "assistant"; content: string };

const SUGGESTIONS = [
  "What's required for a Medicare CoP audit?",
  "OASIS documentation checklist",
  "HIPAA safeguards for caregivers",
  "Annual training requirements",
];

export default function Assistant() {
  const [messages, setMessages] = useState<Msg[]>([]);
  const [input, setInput] = useState("");
  const [streaming, setStreaming] = useState(false);
  const sessionId = useRef<string>(`s-${Date.now()}`).current;
  const scrollRef = useRef<ScrollView>(null);

  useEffect(() => {
    scrollRef.current?.scrollToEnd({ animated: true });
  }, [messages]);

  const send = useCallback(async (text?: string) => {
    const content = (text ?? input).trim();
    if (!content || streaming) return;
    setInput("");
    const userMsg: Msg = { id: `u-${Date.now()}`, role: "user", content };
    const aiId = `a-${Date.now()}`;
    setMessages((m) => [...m, userMsg, { id: aiId, role: "assistant", content: "" }]);
    setStreaming(true);

    try {
      const token = await AsyncStorage.getItem("userToken");
      const res = await fetch(`${API_BASE}/assistant/chat`, {
        method: "POST",
        headers: {
          "Content-Type": "application/json",
          Authorization: `Bearer ${token}`,
        },
        body: JSON.stringify({ session_id: sessionId, message: content }),
      });

      if (!res.body) {
        const txt = await res.text();
        setMessages((m) => m.map((x) => x.id === aiId ? { ...x, content: txt } : x));
        return;
      }

      const reader = (res.body as any).getReader();
      const decoder = new TextDecoder();
      let buf = "";
      let acc = "";
      while (true) {
        const { value, done } = await reader.read();
        if (done) break;
        buf += decoder.decode(value, { stream: true });
        const lines = buf.split("\n\n");
        buf = lines.pop() || "";
        for (const line of lines) {
          if (!line.startsWith("data:")) continue;
          const data = line.slice(5).trimStart();
          if (data === "[DONE]") continue;
          acc += data;
          setMessages((m) => m.map((x) => x.id === aiId ? { ...x, content: acc } : x));
        }
      }
    } catch (e) {
      console.log("stream error", e);
      setMessages((m) =>
        m.map((x) => x.id === aiId ? { ...x, content: "Sorry, I couldn't reach the assistant." } : x)
      );
    } finally {
      setStreaming(false);
    }
  }, [input, streaming, sessionId]);

  return (
    <SafeAreaView edges={["top"]} style={styles.root}>
      <View style={styles.header}>
        <View style={styles.avatar}>
          <Ionicons name="sparkles" size={18} color="#fff" />
        </View>
        <View style={{ flex: 1 }}>
          <Text style={styles.title}>Compliance Assistant</Text>
          <Text style={styles.subtitle}>Powered by Claude · audit guidance</Text>
        </View>
      </View>

      <KeyboardAvoidingView
        behavior={Platform.OS === "ios" ? "padding" : "height"}
        style={{ flex: 1 }}
        keyboardVerticalOffset={Platform.OS === "ios" ? 90 : 0}
      >
        <ScrollView
          ref={scrollRef}
          style={{ flex: 1 }}
          contentContainerStyle={{ padding: 20, paddingBottom: 20, gap: 12 }}
        >
          {messages.length === 0 && (
            <View style={styles.welcome}>
              <View style={styles.welcomeIcon}>
                <Ionicons name="shield-checkmark" size={36} color={theme.colors.brandPrimary} />
              </View>
              <Text style={styles.welcomeTitle}>Hi! I'm HealthGuard.</Text>
              <Text style={styles.welcomeText}>
                Ask me about Medicare CoPs, HIPAA, state surveys, or how to prepare for your next audit.
              </Text>
              <View style={styles.suggestionWrap}>
                {SUGGESTIONS.map((s) => (
                  <Pressable
                    key={s}
                    testID={`suggestion-${s.slice(0, 8)}`}
                    onPress={() => send(s)}
                    style={styles.suggestion}
                  >
                    <Text style={styles.suggestionText}>{s}</Text>
                  </Pressable>
                ))}
              </View>
            </View>
          )}

          {messages.map((m) => (
            <View
              key={m.id}
              testID={`msg-${m.role}`}
              style={[
                styles.bubble,
                m.role === "user" ? styles.userBubble : styles.aiBubble,
              ]}
            >
              <Text style={m.role === "user" ? styles.userText : styles.aiText}>
                {m.content || (streaming ? "…" : "")}
              </Text>
            </View>
          ))}

          {streaming && (
            <View style={styles.typingRow}>
              <ActivityIndicator size="small" color={theme.colors.brandPrimary} />
              <Text style={styles.typingText}>Thinking…</Text>
            </View>
          )}
        </ScrollView>

        <View style={styles.inputBar}>
          <TextInput
            testID="chat-input"
            value={input}
            onChangeText={setInput}
            placeholder="Ask anything about compliance…"
            placeholderTextColor={theme.colors.muted}
            style={styles.input}
            multiline
            onSubmitEditing={() => send()}
          />
          <Pressable
            testID="send-message-button"
            onPress={() => send()}
            disabled={!input.trim() || streaming}
            style={[styles.sendBtn, (!input.trim() || streaming) && { opacity: 0.5 }]}
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
  header: { flexDirection: "row", alignItems: "center", gap: 12, paddingHorizontal: 20, paddingTop: 8, paddingBottom: 12, borderBottomWidth: 1, borderBottomColor: theme.colors.divider },
  avatar: { width: 40, height: 40, borderRadius: 12, backgroundColor: theme.colors.brandPrimary, alignItems: "center", justifyContent: "center" },
  title: { fontSize: 18, fontWeight: "700", color: theme.colors.onSurface },
  subtitle: { fontSize: 11, color: theme.colors.muted },
  welcome: { padding: 24, alignItems: "center", gap: 8 },
  welcomeIcon: { width: 80, height: 80, borderRadius: 24, backgroundColor: theme.colors.brandTertiary, alignItems: "center", justifyContent: "center", marginBottom: 8 },
  welcomeTitle: { fontSize: 22, fontWeight: "700", color: theme.colors.onSurface, marginTop: 4 },
  welcomeText: { fontSize: 14, color: theme.colors.muted, textAlign: "center", lineHeight: 20, paddingHorizontal: 20 },
  suggestionWrap: { flexDirection: "row", flexWrap: "wrap", gap: 8, justifyContent: "center", marginTop: 12 },
  suggestion: { paddingHorizontal: 14, paddingVertical: 10, borderRadius: 999, backgroundColor: theme.colors.surfaceSecondary, borderWidth: 1, borderColor: theme.colors.border },
  suggestionText: { fontSize: 12, color: theme.colors.onSurface, fontWeight: "500" },
  bubble: { maxWidth: "82%", paddingHorizontal: 14, paddingVertical: 10, borderRadius: 18 },
  userBubble: { alignSelf: "flex-end", backgroundColor: theme.colors.brandPrimary, borderBottomRightRadius: 4 },
  aiBubble: { alignSelf: "flex-start", backgroundColor: theme.colors.surfaceSecondary, borderBottomLeftRadius: 4, borderWidth: 1, borderColor: theme.colors.border },
  userText: { color: "#fff", fontSize: 15, lineHeight: 22 },
  aiText: { color: theme.colors.onSurface, fontSize: 15, lineHeight: 22 },
  typingRow: { flexDirection: "row", gap: 8, alignItems: "center", alignSelf: "flex-start", padding: 4 },
  typingText: { color: theme.colors.muted, fontSize: 12 },
  inputBar: { flexDirection: "row", gap: 10, padding: 12, borderTopWidth: 1, borderTopColor: theme.colors.divider, backgroundColor: theme.colors.surfaceSecondary, alignItems: "flex-end" },
  input: { flex: 1, maxHeight: 120, backgroundColor: theme.colors.surfaceTertiary, borderRadius: 22, paddingHorizontal: 16, paddingVertical: 12, fontSize: 15, color: theme.colors.onSurface },
  sendBtn: { width: 44, height: 44, borderRadius: 22, backgroundColor: theme.colors.brandPrimary, alignItems: "center", justifyContent: "center" },
});
