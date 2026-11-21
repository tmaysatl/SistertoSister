import { useState } from "react";
import {
  View, Text, TextInput, Pressable, StyleSheet, ActivityIndicator,
  KeyboardAvoidingView, Platform, ScrollView,
} from "react-native";
import { useRouter } from "expo-router";
import { Ionicons } from "@expo/vector-icons";
import { SafeAreaView } from "react-native-safe-area-context";
import { apiPost } from "../src/api/client";
import { theme } from "../src/theme";

export default function ChangePassword() {
  const router = useRouter();
  const [current, setCurrent] = useState("");
  const [next, setNext] = useState("");
  const [confirm, setConfirm] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [done, setDone] = useState(false);

  const submit = async () => {
    setError(null);
    if (next.length < 10) { setError("New password must be at least 10 characters."); return; }
    if (next !== confirm) { setError("New passwords don't match."); return; }
    setBusy(true);
    try {
      await apiPost("/auth/change-password", { current_password: current, new_password: next });
      setDone(true);
    } catch (e) {
      const msg = e instanceof Error ? e.message : "";
      setError(
        msg.includes("incorrect") ? "Your current password is incorrect."
        : msg.includes("different") ? "New password must be different from the current one."
        : "Couldn't change the password. Please try again."
      );
    } finally {
      setBusy(false);
    }
  };

  return (
    <SafeAreaView style={styles.root}>
      <KeyboardAvoidingView behavior={Platform.OS === "ios" ? "padding" : undefined} style={{ flex: 1 }}>
        <ScrollView contentContainerStyle={styles.body} keyboardShouldPersistTaps="handled">
          <Pressable testID="cp-back" onPress={() => router.back()} hitSlop={10} style={styles.back}>
            <Ionicons name="chevron-back" size={22} color={theme.colors.onSurface} />
            <Text style={styles.backText}>Back</Text>
          </Pressable>
          <Text style={styles.title}>Change password</Text>

          {done ? (
            <View style={{ gap: 12 }}>
              <Text style={styles.ok}>Your password was changed. Use the new one next time you sign in.</Text>
              <Pressable testID="cp-done" onPress={() => router.back()} style={styles.btn}>
                <Text style={styles.btnText}>Done</Text>
              </Pressable>
            </View>
          ) : (
            <View style={{ gap: 12 }}>
              <TextInput testID="cp-current" style={styles.input} placeholder="Current password"
                secureTextEntry value={current} onChangeText={setCurrent} autoCapitalize="none" />
              <TextInput testID="cp-new" style={styles.input} placeholder="New password (10+ characters)"
                secureTextEntry value={next} onChangeText={setNext} autoCapitalize="none" />
              <TextInput testID="cp-confirm" style={styles.input} placeholder="Confirm new password"
                secureTextEntry value={confirm} onChangeText={setConfirm} autoCapitalize="none" />
              {error ? <Text style={styles.err}>{error}</Text> : null}
              <Pressable testID="cp-submit" onPress={submit} disabled={busy || !current || !next}
                style={[styles.btn, { opacity: busy || !current || !next ? 0.6 : 1 }]}>
                {busy ? <ActivityIndicator color="#fff" /> : <Text style={styles.btnText}>Save new password</Text>}
              </Pressable>
            </View>
          )}
        </ScrollView>
      </KeyboardAvoidingView>
    </SafeAreaView>
  );
}

const styles = StyleSheet.create({
  root: { flex: 1, backgroundColor: theme.colors.surface },
  body: { padding: 20, gap: 16 },
  back: { flexDirection: "row", alignItems: "center", gap: 2 },
  backText: { fontSize: 14, color: theme.colors.onSurface },
  title: { fontSize: 22, fontWeight: "800", color: theme.colors.onSurface },
  input: {
    borderWidth: 1, borderColor: theme.colors.border, borderRadius: 12,
    paddingHorizontal: 14, paddingVertical: 12, fontSize: 15, color: theme.colors.onSurface,
    backgroundColor: "#fff",
  },
  err: { color: "#B3261E", fontSize: 13 },
  ok: { color: theme.colors.onSurface, fontSize: 14 },
  btn: { padding: 14, borderRadius: 12, backgroundColor: theme.colors.brandPrimary, alignItems: "center" },
  btnText: { color: "#fff", fontWeight: "700", fontSize: 14 },
});
