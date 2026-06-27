import { useEffect, useState } from "react";
import {
  View, Text, TextInput, Pressable, StyleSheet,
  KeyboardAvoidingView, Platform, ScrollView, ActivityIndicator,
} from "react-native";
import { Image } from "expo-image";
import { LinearGradient } from "expo-linear-gradient";
import { Ionicons } from "@expo/vector-icons";
import { Link, useRouter } from "expo-router";
import { useAuth } from "@/src/context/AuthContext";
import { theme, BRAND_NAME, BRAND_TAGLINE, LOGO_URL } from "@/src/theme";

export default function Login() {
  const { login, mode, setMode } = useAuth();
  const router = useRouter();
  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);

  // If the user lands on the screen with empty fields, prefill the demo
  // admin email (does NOT touch the field if they've already typed). Password
  // is intentionally left blank so users always type the active-mode password
  // themselves — this avoids the historical bug where a stale autofill
  // (e.g. the Supabase password while the persisted mode was Legacy) caused
  // every sign-in to fail with "Incorrect email or password".
  useEffect(() => {
    setEmail((prev) => (prev ? prev : "admin@healthguard.com"));
  }, []);

  const toggleMode = async () => {
    const next = mode === "supabase" ? "legacy" : "supabase";
    await setMode(next);
    setError(null);
  };

  const onSubmit = async () => {
    setError(null);
    if (!email.trim() || !password) {
      setError("Email and password are required");
      return;
    }
    setLoading(true);
    try {
      await login(email.trim(), password);
      router.replace("/(tabs)");
    } catch (e: any) {
      const raw = String(e?.message || e || "");
      // Surface the most useful piece of the error. Supabase returns JSON like
      // {"error":"invalid_grant","error_description":"Invalid login credentials"}
      // and the FastAPI legacy login returns {"detail":"Incorrect email or password"}.
      // Anything else (e.g. network failure) is shown verbatim so we don't
      // mislabel a connectivity problem as a credentials problem.
      const lower = raw.toLowerCase();
      if (lower.includes("invalid login") || lower.includes("incorrect email") || lower.includes("invalid_grant")) {
        setError("Incorrect email or password");
      } else if (lower.includes("failed to fetch") || lower.includes("network") || lower.includes("typeerror")) {
        setError("Can't reach the server. Check your connection.");
      } else {
        setError(raw.slice(0, 180) || "Sign in failed");
      }
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
          <View style={StyleSheet.absoluteFill}>
            <LinearGradient
              colors={["#1a0606", "#000000"]}
              style={StyleSheet.absoluteFill}
            />
          </View>
          <View style={styles.heroContent}>
            <Image source={{ uri: LOGO_URL }} style={styles.logoImg} contentFit="contain" />
            <Text style={styles.heroTitle}>{BRAND_NAME}</Text>
            <Text style={styles.heroSubtitle}>{BRAND_TAGLINE}</Text>
          </View>
        </View>

        <View style={styles.form}>
          <Text style={styles.title}>Welcome back</Text>
          <Text style={styles.subtitle}>Sign in to your agency workspace</Text>

          <Pressable
            testID="auth-mode-toggle"
            onPress={toggleMode}
            style={styles.modePill}
          >
            <Ionicons
              name={mode === "supabase" ? "cloud-done-outline" : "server-outline"}
              size={14}
              color={theme.colors.brandPrimary}
            />
            <Text style={styles.modePillText}>
              Auth: {mode === "supabase" ? "Supabase" : "Legacy (MongoDB)"} · tap to switch
            </Text>
          </Pressable>

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
              {mode === "supabase"
                ? "Supabase: admin@healthguard.com / AdminPassword123!  ·  caregiver@healthguard.com / Caregiver123!"
                : "Legacy: admin@healthguard.com / Admin@123  ·  caregiver@healthguard.com / Caregiver@123"}
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
  heroWrap: { height: 320, position: "relative" },
  hero: { ...StyleSheet.absoluteFillObject },
  scrim: { ...StyleSheet.absoluteFillObject },
  heroContent: {
    position: "absolute", bottom: 24, left: 24, right: 24, alignItems: "flex-start",
  },
  logoImg: { width: 96, height: 96, marginBottom: 8, marginLeft: -8 },
  logo: {
    width: 40, height: 40, borderRadius: 12,
    backgroundColor: theme.colors.brandPrimary,
    alignItems: "center", justifyContent: "center", marginBottom: 12,
  },
  heroTitle: { color: "#fff", fontSize: 24, fontWeight: "700", letterSpacing: -0.3 },
  heroSubtitle: { color: "rgba(255,255,255,0.75)", fontSize: 13, marginTop: 4 },
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
  modePill: {
    flexDirection: "row",
    gap: 6,
    alignItems: "center",
    alignSelf: "flex-start",
    backgroundColor: theme.colors.surfaceTertiary,
    borderWidth: 1,
    borderColor: theme.colors.border,
    paddingHorizontal: 10,
    paddingVertical: 6,
    borderRadius: 999,
    marginBottom: 4,
  },
  modePillText: { fontSize: 11, color: theme.colors.brandPrimary, fontWeight: "600" },
});
