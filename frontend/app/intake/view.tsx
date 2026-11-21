import { useEffect, useState } from "react";
import { View, Text, Pressable, StyleSheet, ScrollView, ActivityIndicator, TextInput } from "react-native";
import { useLocalSearchParams, useRouter } from "expo-router";
import { Ionicons } from "@expo/vector-icons";
import { SafeAreaView } from "react-native-safe-area-context";
import { apiGet, apiPatch } from "@/src/api/client";
import { theme } from "@/src/theme";
import { IntakeSchema, STATUSES, sectionVisible } from "@/src/components/intake/intakeTypes";

type Sub = {
  id: string; form_title: string; summary: string; status: string; admin_notes?: string;
  submitted_at: string; submitted_by_name?: string; values: Record<string, any>; schema: IntakeSchema;
};

function show(v: any): string {
  if (v === true) return "Yes";
  if (Array.isArray(v)) return v.length ? v.join(", ") : "—";
  return v ? String(v) : "—";
}

export default function IntakeView() {
  const router = useRouter();
  const { id } = useLocalSearchParams<{ id: string }>();
  const [sub, setSub] = useState<Sub | null>(null);
  const [loading, setLoading] = useState(true);
  const [notes, setNotes] = useState("");
  const [msg, setMsg] = useState<string | null>(null);
  const [saving, setSaving] = useState(false);

  useEffect(() => {
    apiGet<Sub>(`/intake/submissions/${id}`)
      .then((r) => { setSub(r); setNotes(r.admin_notes || ""); })
      .catch(() => setSub(null))
      .finally(() => setLoading(false));
  }, [id]);

  const update = async (patch: { status?: string; admin_notes?: string }) => {
    setSaving(true);
    setMsg("Saving…");
    try {
      await apiPatch(`/intake/submissions/${id}`, patch);
      setSub((cur) => (cur ? { ...cur, ...patch } : cur));
      setMsg("Saved");
    } catch {
      setMsg("Couldn't save. Try again.");
    } finally {
      setSaving(false);
    }
  };

  return (
    <SafeAreaView style={s.root}>
      <View style={s.header}>
        <Pressable testID="intake-view-back" onPress={() => router.back()} hitSlop={10}>
          <Ionicons name="chevron-back" size={24} color="#fff" />
        </Pressable>
        <Text style={s.title} numberOfLines={1}>{sub?.summary || "Submission"}</Text>
      </View>
      {loading ? (
        <View style={s.center}><ActivityIndicator color={theme.colors.brandPrimary} /></View>
      ) : !sub ? (
        <View style={s.center}><Text style={s.muted}>Submission not found.</Text></View>
      ) : (
        <ScrollView contentContainerStyle={s.body} keyboardShouldPersistTaps="handled">
          <Text style={s.muted}>
            {sub.form_title} · {sub.submitted_at.slice(0, 10)}{sub.submitted_by_name ? ` · entered by ${sub.submitted_by_name}` : ""}
          </Text>

          <Text style={s.sectionTitle}>Status</Text>
          <View style={s.chipsRow}>
            {STATUSES.map((st) => (
              <Pressable key={st} testID={`intake-status-${st}`} onPress={() => update({ status: st })} disabled={saving}
                         style={[s.chip, sub.status === st && s.chipOn]}>
                <Text style={[s.chipText, sub.status === st && { color: "#fff" }]}>{st}</Text>
              </Pressable>
            ))}
          </View>

          <Text style={s.sectionTitle}>Staff notes</Text>
          <TextInput testID="intake-notes" value={notes} onChangeText={setNotes} multiline
                     placeholder="Follow-up notes (no card or ID numbers)" placeholderTextColor={theme.colors.muted}
                     style={s.input} />
          <Pressable testID="intake-save-notes" onPress={() => update({ admin_notes: notes })} disabled={saving} style={[s.btn, saving && { opacity: 0.6 }]}>
            {saving ? <ActivityIndicator color="#fff" /> : <Text style={s.btnText}>Save notes</Text>}
          </Pressable>
          {msg && <Text style={s.muted}>{msg}</Text>}

          {sub.schema.sections.filter((sec) => sectionVisible(sec, sub.values)).map((sec) => (
            <View key={sec.title} style={{ gap: 8 }}>
              <Text style={s.sectionTitle}>{sec.title}</Text>
              {sec.fields.map((f) => (
                <View key={f.key}>
                  <Text style={s.label}>{f.label}</Text>
                  <Text style={s.value}>{show(sub.values[f.key])}</Text>
                </View>
              ))}
            </View>
          ))}
        </ScrollView>
      )}
    </SafeAreaView>
  );
}

const s = StyleSheet.create({
  root: { flex: 1, backgroundColor: theme.colors.background },
  header: { backgroundColor: theme.colors.brandPrimary, padding: 14, flexDirection: "row", alignItems: "center", gap: 10 },
  title: { flex: 1, color: "#fff", fontWeight: "700", fontSize: 16 },
  body: { padding: 16, paddingBottom: 80, gap: 12 },
  center: { flex: 1, alignItems: "center", justifyContent: "center", padding: 24 },
  muted: { color: theme.colors.muted, fontSize: 12 },
  sectionTitle: { fontSize: 13, fontWeight: "800", color: theme.colors.brandPrimary, textTransform: "uppercase", letterSpacing: 0.4, marginTop: 8 },
  label: { fontSize: 12, fontWeight: "700", color: theme.colors.onSurface },
  value: { fontSize: 14, color: theme.colors.onSurfaceTertiary },
  chipsRow: { flexDirection: "row", flexWrap: "wrap", gap: 6 },
  chip: { backgroundColor: "#fff", borderRadius: 999, paddingHorizontal: 14, paddingVertical: 11, minHeight: 42, justifyContent: "center", borderWidth: 1, borderColor: theme.colors.border },
  chipOn: { backgroundColor: theme.colors.brandPrimary, borderColor: theme.colors.brandPrimary },
  chipText: { fontSize: 12, fontWeight: "600", color: theme.colors.onSurface, textTransform: "capitalize" },
  input: { backgroundColor: "#fff", borderRadius: 10, padding: 12, borderWidth: 1, borderColor: theme.colors.border, minHeight: 72, textAlignVertical: "top", color: theme.colors.onSurface },
  btn: { backgroundColor: theme.colors.brandPrimary, padding: 14, minHeight: 46, justifyContent: "center", borderRadius: 10, alignItems: "center" },
  btnText: { color: "#fff", fontWeight: "700" },
});
