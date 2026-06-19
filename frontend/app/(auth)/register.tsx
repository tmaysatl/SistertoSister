import { useState } from "react";
import {
  View, Text, TextInput, Pressable, StyleSheet,
  KeyboardAvoidingView, Platform, ScrollView, ActivityIndicator,
} from "react-native";
import { Ionicons } from "@expo/vector-icons";
import { Link, useRouter } from "expo-router";
import { useAuth, Role } from "@/src/context/AuthContext";
import { theme } from "@/src/theme";

export default function Register() {
  const { register } = useAuth();
  const router = useRouter();
  const [name, setName] = useState("");
  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const [role, setRole] = useState<Role>("admin");
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const onSubmit = async () => {
    if (!name || !email || password.length < 6) {
      setError("Fill all fields. Password ≥ 6 characters.");
      return;
    }
    setError(null);
    setLoading(true);
    try {
      await register(email.trim(), password, name.trim(), role);
      router.replace("/(tabs)");
    } catch (e: any) {
      setError("Email may already be registered.");
    } finally {
      setLoading(false);
    }
  };

  return (
    <KeyboardAvoidingView
      behavior={Platform.OS === "ios" ? "padding" : undefined}
      style={styles.flex}
    >
      <ScrollView contentContainerStyle={styles.scroll} keyboardShouldPersistTaps="handled">
        <Link href="/(auth)/login" asChild>
          <Pressable testID="back-to-login-button" style={styles.back}>
            <Ionicons name="chevron-back" size={22} color={theme.colors.onSurface} />
          </Pressable>
        </Link>

        <View style={styles.body}>
          <Text style={styles.title}>Create account</Text>
          <Text style={styles.subtitle}>Start your compliance workspace</Text>

          <View style={styles.field}>
            <Text style={styles.label}>Full name</Text>
            <TextInput
              testID="register-name-input"
              value={name} onChangeText={setName}
              placeholder="Jane Doe"
              placeholderTextColor={theme.colors.muted}
              style={styles.input}
            />
          </View>

          <View style={styles.field}>
            <Text style={styles.label}>Email</Text>
            <TextInput
              testID="register-email-input"
              value={email} onChangeText={setEmail}
              autoCapitalize="none" keyboardType="email-address"
              placeholder="you@agency.com"
              placeholderTextColor={theme.colors.muted}
              style={styles.input}
            />
          </View>

          <View style={styles.field}>
            <Text style={styles.label}>Password</Text>
            <TextInput
              testID="register-password-input"
              value={password} onChangeText={setPassword}
              secureTextEntry placeholder="At least 6 characters"
              placeholderTextColor={theme.colors.muted}
              style={styles.input}
            />
          </View>

          <View style={styles.field}>
            <Text style={styles.label}>Role</Text>
            <View style={styles.roleRow}>
              {(["admin", "caregiver"] as Role[]).map((r) => (
                <Pressable
                  key={r}
                  testID={`register-role-${r}`}
                  onPress={() => setRole(r)}
                  style={[styles.rolePill, role === r && styles.rolePillActive]}
                >
                  <Text style={[styles.rolePillText, role === r && styles.rolePillTextActive]}>
                    {r === "admin" ? "Agency Owner" : "Caregiver"}
                  </Text>
                </Pressable>
              ))}
            </View>
          </View>

          {error && <Text style={styles.error}>{error}</Text>}

          <Pressable
            testID="register-submit-button"
            onPress={onSubmit}
            disabled={loading}
            style={({ pressed }) => [styles.primaryBtn, pressed && { opacity: 0.85 }]}
          >
            {loading ? (
              <ActivityIndicator color="#fff" />
            ) : (
              <Text style={styles.primaryBtnText}>Create account</Text>
            )}
          </Pressable>
        </View>
      </ScrollView>
    </KeyboardAvoidingView>
  );
}

const styles = StyleSheet.create({
  flex: { flex: 1, backgroundColor: theme.colors.surface },
  scroll: { flexGrow: 1, paddingTop: 60 },
  back: { paddingHorizontal: 16, paddingVertical: 8 },
  body: { padding: 24, gap: 14 },
  title: { fontSize: 28, fontWeight: "700", color: theme.colors.onSurface },
  subtitle: { fontSize: 14, color: theme.colors.muted, marginBottom: 8 },
  field: { gap: 6 },
  label: { fontSize: 13, fontWeight: "600", color: theme.colors.onSurfaceTertiary },
  input: {
    borderWidth: 1, borderColor: theme.colors.border,
    backgroundColor: theme.colors.surfaceSecondary,
    borderRadius: 12, paddingHorizontal: 14, paddingVertical: 14,
    fontSize: 15, color: theme.colors.onSurface,
  },
  roleRow: { flexDirection: "row", gap: 8 },
  rolePill: {
    flex: 1, paddingVertical: 12, borderRadius: 12,
    borderWidth: 1, borderColor: theme.colors.border,
    backgroundColor: theme.colors.surfaceSecondary, alignItems: "center",
  },
  rolePillActive: {
    backgroundColor: theme.colors.brandPrimary,
    borderColor: theme.colors.brandPrimary,
  },
  rolePillText: { color: theme.colors.onSurface, fontSize: 14, fontWeight: "600" },
  rolePillTextActive: { color: "#fff" },
  error: { color: theme.colors.error, fontSize: 13 },
  primaryBtn: {
    backgroundColor: theme.colors.brandPrimary,
    borderRadius: 12, paddingVertical: 16,
    alignItems: "center", marginTop: 8,
  },
  primaryBtnText: { color: "#fff", fontSize: 16, fontWeight: "600" },
});
