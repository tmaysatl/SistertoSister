import { useEffect, useState } from "react";
import {
  View, Text, Pressable, StyleSheet, ScrollView, ActivityIndicator,
  KeyboardAvoidingView, Platform,
} from "react-native";
import { useLocalSearchParams, useRouter } from "expo-router";
import { Ionicons } from "@expo/vector-icons";
import { SafeAreaView } from "react-native-safe-area-context";
import { apiGet, apiPost } from "@/src/api/client";
import { theme } from "@/src/theme";
import IntakeFormFields from "@/src/components/intake/IntakeFormFields";
import { IntakeFormDef } from "@/src/components/intake/intakeTypes";

export default function NewIntake() {
  const router = useRouter();
  const { form } = useLocalSearchParams<{ form: string }>();
  const [def, setDef] = useState<IntakeFormDef | null>(null);
  const [loading, setLoading] = useState(true);
  const [values, setValues] = useState<Record<string, any>>({});
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [done, setDone] = useState(false);

  useEffect(() => {
    apiGet<IntakeFormDef[]>("/intake/forms")
      .then((all) => setDef(all.find((f) => f.key === form) || null))
      .catch(() => setDef(null))
      .finally(() => setLoading(false));
  }, [form]);

  const submit = async () => {
    if (!def) return;
    setError(null);
    setBusy(true);
    try {
      await apiPost("/intake/submissions", { form_key: def.key, values });
      setDone(true);
    } catch (e) {
      // Server returns {"detail": "..."} with a readable message for validation problems.
      const raw = e instanceof Error ? e.message : "";
      const m = raw.match(/"detail"\s*:\s*"([^"]+)"/);
      setError(m ? m[1] : "Couldn't submit. Check your connection and try again.");
    } finally {
      setBusy(false);
    }
  };

  return (
    <SafeAreaView style={s.root}>
      <KeyboardAvoidingView behavior={Platform.OS === "ios" ? "padding" : undefined} style={{ flex: 1 }}>
        <View style={s.header}>
          <Pressable testID="intake-new-back" onPress={() => router.back()} hitSlop={10}>
            <Ionicons name="chevron-back" size={24} color="#fff" />
          </Pressable>
          <Text style={s.title} numberOfLines={1}>{def?.title || "Intake form"}</Text>
        </View>
        {loading ? (
          <View style={s.center}><ActivityIndicator color={theme.colors.brandPrimary} /></View>
        ) : !def ? (
          <View style={s.center}><Text style={s.muted}>This form could not be loaded.</Text></View>
        ) : done ? (
          <View style={s.center}>
            <Ionicons name="checkmark-circle" size={56} color={theme.colors.success} />
            <Text style={s.doneTitle}>Saved</Text>
            <Text style={s.muted}>The submission was added to the intake list.</Text>
            <Pressable testID="intake-done" onPress={() => router.replace("/intake" as any)} style={s.primaryBtn}>
              <Text style={s.primaryText}>Back to intake list</Text>
            </Pressable>
          </View>
        ) : (
          <ScrollView contentContainerStyle={s.body} keyboardShouldPersistTaps="handled">
            <Text style={s.desc}>{def.description}</Text>
            <IntakeFormFields
              schema={def.schema}
              values={values}
              onChange={(k, v) => setValues((cur) => ({ ...cur, [k]: v }))}
            />
            {error && <Text testID="intake-error" style={s.error}>{error}</Text>}
            <Pressable testID="intake-submit" onPress={submit} disabled={busy}
                       style={[s.primaryBtn, busy && { opacity: 0.6 }]}>
              {busy ? <ActivityIndicator color="#fff" /> : <Text style={s.primaryText}>Submit</Text>}
            </Pressable>
          </ScrollView>
        )}
      </KeyboardAvoidingView>
    </SafeAreaView>
  );
}

const s = StyleSheet.create({
  root: { flex: 1, backgroundColor: theme.colors.background },
  header: { backgroundColor: theme.colors.brandPrimary, padding: 14, flexDirection: "row", alignItems: "center", gap: 10 },
  title: { flex: 1, color: "#fff", fontWeight: "700", fontSize: 16 },
  body: { padding: 16, paddingBottom: 80, gap: 14 },
  center: { flex: 1, alignItems: "center", justifyContent: "center", gap: 10, padding: 24 },
  desc: { fontSize: 13, color: theme.colors.onSurfaceTertiary, lineHeight: 19 },
  muted: { color: theme.colors.muted, textAlign: "center" },
  doneTitle: { fontSize: 20, fontWeight: "800", color: theme.colors.onSurface },
  error: { color: theme.colors.danger, fontWeight: "700" },
  primaryBtn: { backgroundColor: theme.colors.brandPrimary, padding: 14, borderRadius: 10, alignItems: "center", marginTop: 8 },
  primaryText: { color: "#fff", fontWeight: "700", fontSize: 15 },
});
