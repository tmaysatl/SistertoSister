import { useState } from "react";
import {
  View, Text, TextInput, Pressable, StyleSheet,
  KeyboardAvoidingView, Platform, ScrollView, ActivityIndicator,
} from "react-native";
import { Image } from "expo-image";
import { LinearGradient } from "expo-linear-gradient";
import { Ionicons } from "@expo/vector-icons";
import { Link, useRouter } from "expo-router";
import { useAuth } from "@/src/context/AuthContext";
import { theme, HERO_IMAGE } from "@/src/theme";

export default function Login() {
  const { login } = useAuth();
  const router = useRouter();
  const [email, setEmail] = useState("admin@healthguard.com");
  const [password, setPassword] = useState("Admin@123");
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const onSubmit = async () => {
    setError(null);
    setLoading(true);
    try {
      await login(email.trim(), password);
      router.replace("/(tabs)");
    } catch (e: any) {
      setError("Incorrect email or password");
    } finally {
      setLoading(false);
    }
  };

  return (
    <KeyboardAvoidingView
      behavior={Platform.OS === "ios" ? "padding" : undefined}
      style={styles.flex}
    >
      <ScrollView
        contentContainerStyle={styles.scroll}
        keyboardShouldPersistTaps="handled"
      >
        <View style={styles.heroWrap}>
          <Image source={{ uri: HERO_IMAGE }} style={styles.hero} contentFit="cover" />
          <LinearGradient
            colors={["rgba(17,20,18,0.15)", "rgba(17,20,18,0.85)"]}
            style={styles.scrim}
          />
          <View style={styles.heroContent}>
            <View style={styles.logo}>
              <Ionicons name="shield-checkmark" size={22} color="#fff" />
            </View>
            <Text style={styles.heroTitle}>HealthGuard</Text>
            <Text style={styles.heroSubtitle}>
              Audit-ready compliance for home health agencies
            </Text>
          </View>
        </View>

        <View style={styles.form}>
          <Text style={styles.title}>Welcome back</Text>
          <Text style={styles.subtitle}>Sign in to your agency workspace</Text>

          <View style={styles.field}>
            <Text style={styles.label}>Email</Text>
            <TextInput
              testID="login-email-input"
              value={email}
              onChangeText={setEmail}
              autoCapitalize="none"
              keyboardType="email-address"
              placeholder="you@agency.com"
              placeholderTextColor={theme.colors.muted}
              style={styles.input}
            />
          </View>

          <View style={styles.field}>
            <Text style={styles.label}>Password</Text>
            <TextInput
              testID="login-password-input"
              value={password}
              onChangeText={setPassword}
              secureTextEntry
              placeholder="••••••••"
              placeholderTextColor={theme.colors.muted}
              style={styles.input}
            />
          </View>

          {error && <Text style={styles.error}>{error}</Text>}

          <Pressable
            testID="login-submit-button"
            onPress={onSubmit}
            disabled={loading}
            style={({ pressed }) => [styles.primaryBtn, pressed && { opacity: 0.85 }]}
          >
            {loading ? (
              <ActivityIndicator color="#fff" />
            ) : (
              <Text style={styles.primaryBtnText}>Sign in</Text>
            )}
          </Pressable>

          <Link href="/(auth)/register" asChild>
            <Pressable testID="go-register-link" style={styles.secondaryBtn}>
              <Text style={styles.secondaryBtnText}>Create new account</Text>
            </Pressable>
          </Link>

          <View style={styles.hintBox}>
            <Ionicons name="information-circle-outline" size={16} color={theme.colors.muted} />
            <Text style={styles.hint}>
              Demo: admin@healthguard.com / Admin@123  ·  caregiver@healthguard.com / Caregiver@123
            </Text>
          </View>
        </View>
      </ScrollView>
    </KeyboardAvoidingView>
  );
}

const styles = StyleSheet.create({
  flex: { flex: 1, backgroundColor: theme.colors.surface },
  scroll: { flexGrow: 1 },
  heroWrap: { height: 280, position: "relative" },
  hero: { ...StyleSheet.absoluteFillObject },
  scrim: { ...StyleSheet.absoluteFillObject },
  heroContent: {
    position: "absolute", bottom: 24, left: 24, right: 24,
  },
  logo: {
    width: 40, height: 40, borderRadius: 12,
    backgroundColor: theme.colors.brandPrimary,
    alignItems: "center", justifyContent: "center", marginBottom: 12,
  },
  heroTitle: { color: "#fff", fontSize: 28, fontWeight: "700", letterSpacing: -0.5 },
  heroSubtitle: { color: "rgba(255,255,255,0.85)", fontSize: 14, marginTop: 4 },
  form: { padding: 24, gap: 14 },
  title: { fontSize: 24, fontWeight: "700", color: theme.colors.onSurface },
  subtitle: { fontSize: 14, color: theme.colors.muted, marginBottom: 8 },
  field: { gap: 6 },
  label: { fontSize: 13, fontWeight: "600", color: theme.colors.onSurfaceTertiary },
  input: {
    borderWidth: 1, borderColor: theme.colors.border,
    backgroundColor: theme.colors.surfaceSecondary,
    borderRadius: 12, paddingHorizontal: 14, paddingVertical: 14,
    fontSize: 15, color: theme.colors.onSurface,
  },
  error: { color: theme.colors.error, fontSize: 13 },
  primaryBtn: {
    backgroundColor: theme.colors.brandPrimary,
    borderRadius: 12, paddingVertical: 16,
    alignItems: "center", marginTop: 8,
  },
  primaryBtnText: { color: "#fff", fontSize: 16, fontWeight: "600" },
  secondaryBtn: {
    paddingVertical: 12, alignItems: "center",
  },
  secondaryBtnText: { color: theme.colors.brandPrimary, fontSize: 14, fontWeight: "600" },
  hintBox: {
    flexDirection: "row", gap: 6, alignItems: "flex-start",
    backgroundColor: theme.colors.surfaceTertiary,
    padding: 12, borderRadius: 10, marginTop: 8,
  },
  hint: { flex: 1, fontSize: 11, color: theme.colors.muted, lineHeight: 16 },
});
