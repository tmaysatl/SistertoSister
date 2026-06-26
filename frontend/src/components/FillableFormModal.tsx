import { useCallback, useEffect, useState } from "react";
import {
  View, Text, StyleSheet, ScrollView, Pressable, TextInput, ActivityIndicator,
  Modal, KeyboardAvoidingView, Platform, Alert,
} from "react-native";
import { Ionicons } from "@expo/vector-icons";
import SignatureScreen from "react-native-signature-canvas";
import { apiGet, apiPost } from "@/src/api/client";
import { theme } from "@/src/theme";

type Field = {
  key: string; label: string;
  type: "text" | "longtext" | "date" | "number" | "money" |
        "select" | "radio" | "checkbox" | "signature";
  required?: boolean;
  options?: string[];
};
type Section = { title: string; fields: Field[] };
type Schema = { sections: Section[] };

type Props = {
  visible: boolean;
  docId: string | null;
  docTitle: string;
  onClose: () => void;
  onSubmitted?: () => void;
};

export function FillableFormModal({ visible, docId, docTitle, onClose, onSubmitted }: Props) {
  const [schema, setSchema] = useState<Schema | null>(null);
  const [values, setValues] = useState<Record<string, any>>({});
  const [sig, setSig] = useState<string | null>(null);
  const [loading, setLoading] = useState(false);
  const [busy, setBusy] = useState(false);
  const [showSig, setShowSig] = useState(false);

  const load = useCallback(async () => {
    if (!docId) return;
    setLoading(true);
    setValues({}); setSig(null);
    try {
      const r = await apiGet<{ has_form: boolean; schema?: Schema }>(`/documents/${docId}/form-schema`);
      if (r.has_form && r.schema) setSchema(r.schema);
      else setSchema(null);
    } finally { setLoading(false); }
  }, [docId]);

  useEffect(() => { if (visible) load(); }, [visible, load]);

  const set = (k: string, v: any) => setValues((s) => ({ ...s, [k]: v }));

  const toggleCheck = (k: string, opt: string) => {
    setValues((s) => {
      const arr: string[] = Array.isArray(s[k]) ? s[k] : [];
      return { ...s, [k]: arr.includes(opt) ? arr.filter((x) => x !== opt) : [...arr, opt] };
    });
  };

  const submit = async () => {
    if (!schema || !docId) return;
    for (const sec of schema.sections) {
      for (const f of sec.fields) {
        if (f.required && f.type !== "signature" && !values[f.key]) {
          Alert.alert("Missing", `Please fill: ${f.label}`); return;
        }
        if (f.required && f.type === "signature" && !sig) {
          Alert.alert("Signature required", `Please sign before submitting.`); return;
        }
      }
    }
    setBusy(true);
    try {
      await apiPost(`/documents/${docId}/submit-form`, { values, signature_b64: sig });
      Alert.alert("Submitted", "Your completed form has been saved to your profile.");
      onSubmitted?.();
      onClose();
    } catch (e: any) {
      Alert.alert("Submit failed", e?.message || "Try again.");
    } finally { setBusy(false); }
  };

  const renderField = (f: Field) => {
    if (f.type === "signature") {
      return (
        <View key={f.key} style={{ gap: 6 }}>
          <Text style={styles.label}>{f.label}{f.required ? " *" : ""}</Text>
          {sig ? (
            <View style={styles.sigPreview}>
              <Ionicons name="checkmark-circle" size={18} color={theme.colors.success} />
              <Text style={{ color: theme.colors.success, fontWeight: "700" }}>Signature captured</Text>
              <Pressable onPress={() => { setSig(null); setShowSig(true); }} hitSlop={10}>
                <Text style={styles.redo}>Redo</Text>
              </Pressable>
            </View>
          ) : (
            <Pressable onPress={() => setShowSig(true)} style={styles.sigBtn}>
              <Ionicons name="create-outline" size={18} color="#fff" />
              <Text style={{ color: "#fff", fontWeight: "700" }}>Tap to sign</Text>
            </Pressable>
          )}
        </View>
      );
    }
    if (f.type === "radio" || f.type === "select") {
      const opts = f.options || [];
      return (
        <View key={f.key} style={{ gap: 6 }}>
          <Text style={styles.label}>{f.label}{f.required ? " *" : ""}</Text>
          <View style={styles.chipsRow}>
            {opts.map((o) => {
              const on = values[f.key] === o;
              return (
                <Pressable key={o} onPress={() => set(f.key, o)} style={[styles.chip, on && styles.chipOn]}>
                  <Text style={[styles.chipText, on && styles.chipTextOn]}>{o}</Text>
                </Pressable>
              );
            })}
          </View>
        </View>
      );
    }
    if (f.type === "checkbox") {
      const opts = f.options || [];
      const arr: string[] = Array.isArray(values[f.key]) ? values[f.key] : [];
      return (
        <View key={f.key} style={{ gap: 6 }}>
          <Text style={styles.label}>{f.label}{f.required ? " *" : ""}</Text>
          <View style={{ gap: 6 }}>
            {opts.map((o) => {
              const on = arr.includes(o);
              return (
                <Pressable key={o} onPress={() => toggleCheck(f.key, o)} style={[styles.checkRow, on && styles.checkRowOn]}>
                  <View style={[styles.checkbox, on && styles.checkboxOn]}>
                    {on && <Ionicons name="checkmark" size={14} color="#fff" />}
                  </View>
                  <Text style={[styles.chipText, on && { fontWeight: "700" }]}>{o}</Text>
                </Pressable>
              );
            })}
          </View>
        </View>
      );
    }
    const multi = f.type === "longtext";
    return (
      <View key={f.key} style={{ gap: 6 }}>
        <Text style={styles.label}>{f.label}{f.required ? " *" : ""}</Text>
        <TextInput
          testID={`field-${f.key}`}
          value={values[f.key] || ""}
          onChangeText={(v) => set(f.key, v)}
          placeholder={f.type === "date" ? "YYYY-MM-DD" : ""}
          placeholderTextColor={theme.colors.muted}
          keyboardType={f.type === "number" || f.type === "money" ? "decimal-pad" : "default"}
          multiline={multi}
          style={[styles.input, multi && { minHeight: 64 }]}
        />
      </View>
    );
  };

  return (
    <Modal visible={visible} animationType="slide" onRequestClose={onClose} presentationStyle="fullScreen">
      <KeyboardAvoidingView behavior={Platform.OS === "ios" ? "padding" : undefined} style={styles.root}>
        <View style={styles.header}>
          <Pressable onPress={onClose} hitSlop={10}><Ionicons name="close" size={24} color="#fff" /></Pressable>
          <Text style={styles.title} numberOfLines={1}>{docTitle}</Text>
          <Pressable testID="submit-form" onPress={submit} disabled={busy || !schema}
                    style={[styles.submitBtn, (busy || !schema) && { opacity: 0.5 }]}>
            {busy ? <ActivityIndicator color="#204231" /> : <Text style={styles.submitText}>Submit</Text>}
          </Pressable>
        </View>
        {loading ? (
          <View style={styles.center}><ActivityIndicator color={theme.colors.brandPrimary} /></View>
        ) : !schema ? (
          <View style={styles.center}>
            <Text style={{ color: theme.colors.muted, textAlign: "center", padding: 20 }}>
              {`This document doesn't have a fillable form yet. View it as a PDF instead.`}
            </Text>
          </View>
        ) : (
          <ScrollView style={{ flex: 1 }} contentContainerStyle={{ padding: 16, paddingBottom: 80, gap: 14 }}>
            {schema.sections.map((sec) => (
              <View key={sec.title} style={{ gap: 10 }}>
                <Text style={styles.sectionTitle}>{sec.title}</Text>
                {sec.fields.map(renderField)}
              </View>
            ))}
          </ScrollView>
        )}

        {/* Signature drawer */}
        <Modal visible={showSig} transparent animationType="slide" onRequestClose={() => setShowSig(false)}>
          <View style={styles.sigBackdrop}>
            <View style={styles.sigBox}>
              <Text style={[styles.sectionTitle, { marginBottom: 6 }]}>Sign here</Text>
              <View style={{ height: 220 }}>
                <SignatureScreen
                  onOK={(b64) => { setSig(b64); setShowSig(false); }}
                  webStyle={`.m-signature-pad--footer {display: none;}
                            .m-signature-pad {box-shadow: none; border: 1px solid #BCC2BD; border-radius: 8px;}`}
                  descriptionText=""
                  imageType="image/png"
                  autoClear={false}
                />
              </View>
              <View style={{ flexDirection: "row", gap: 8, marginTop: 10 }}>
                <Pressable onPress={() => setShowSig(false)} style={[styles.cancelBtn, { flex: 1 }]}>
                  <Text style={{ fontWeight: "700", color: theme.colors.onSurface }}>Cancel</Text>
                </Pressable>
              </View>
              <Text style={{ fontSize: 11, color: theme.colors.muted, textAlign: "center", marginTop: 6 }}>
              {`Tap inside to draw your signature, then use the "Confirm" button in the toolbar (or scroll up).`}
              </Text>
            </View>
          </View>
        </Modal>
      </KeyboardAvoidingView>
    </Modal>
  );
}

const styles = StyleSheet.create({
  root: { flex: 1, backgroundColor: "#F9F9F8" },
  header: { backgroundColor: theme.colors.brandPrimary, padding: 14, flexDirection: "row", alignItems: "center", gap: 10 },
  title: { flex: 1, color: "#fff", fontWeight: "700", fontSize: 14 },
  submitBtn: { backgroundColor: "#fff", paddingHorizontal: 14, paddingVertical: 8, borderRadius: 999 },
  submitText: { color: theme.colors.brandPrimary, fontWeight: "700", fontSize: 13 },
  center: { flex: 1, alignItems: "center", justifyContent: "center" },
  sectionTitle: { fontSize: 13, fontWeight: "800", color: theme.colors.brandPrimary, textTransform: "uppercase", letterSpacing: 0.4 },
  label: { fontSize: 12, fontWeight: "700", color: theme.colors.onSurface },
  input: { backgroundColor: theme.colors.surface, borderRadius: 10, padding: 12, borderWidth: 1, borderColor: theme.colors.border, color: theme.colors.onSurface, fontSize: 14 },
  chipsRow: { flexDirection: "row", flexWrap: "wrap", gap: 6 },
  chip: { backgroundColor: theme.colors.surface, borderRadius: 999, paddingHorizontal: 12, paddingVertical: 7, borderWidth: 1, borderColor: theme.colors.border },
  chipOn: { backgroundColor: theme.colors.brandPrimary, borderColor: theme.colors.brandPrimary },
  chipText: { fontSize: 12, color: theme.colors.onSurface, fontWeight: "600" },
  chipTextOn: { color: "#fff" },
  checkRow: { flexDirection: "row", alignItems: "center", gap: 10, padding: 10, borderRadius: 10, backgroundColor: theme.colors.surface, borderWidth: 1, borderColor: theme.colors.border },
  checkRowOn: { backgroundColor: theme.colors.brandTertiary, borderColor: theme.colors.brandPrimary },
  checkbox: { width: 22, height: 22, borderRadius: 11, borderWidth: 2, borderColor: theme.colors.borderStrong, alignItems: "center", justifyContent: "center" },
  checkboxOn: { backgroundColor: theme.colors.success, borderColor: theme.colors.success },
  sigBtn: { flexDirection: "row", alignItems: "center", justifyContent: "center", gap: 8, backgroundColor: theme.colors.brandPrimary, padding: 14, borderRadius: 10 },
  sigPreview: { flexDirection: "row", alignItems: "center", gap: 8, padding: 12, borderRadius: 10, backgroundColor: theme.colors.brandTertiary, borderWidth: 1, borderColor: theme.colors.success },
  redo: { color: theme.colors.brandPrimary, fontWeight: "700", marginLeft: "auto" },
  sigBackdrop: { flex: 1, backgroundColor: "rgba(0,0,0,0.5)", justifyContent: "center", padding: 16 },
  sigBox: { backgroundColor: theme.colors.background, borderRadius: 14, padding: 14 },
  cancelBtn: { padding: 12, borderRadius: 10, alignItems: "center", backgroundColor: theme.colors.surfaceSecondary, borderWidth: 1, borderColor: theme.colors.border },
});
