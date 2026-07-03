import React, { useCallback, useEffect, useMemo, useState } from "react";
import {
  View,
  Text,
  StyleSheet,
  ScrollView,
  Pressable,
  TextInput,
  ActivityIndicator,
  Modal,
  KeyboardAvoidingView,
  Platform,
  Switch,
  Alert,
} from "react-native";
import { Ionicons } from "@expo/vector-icons";
import { API_BASE, getAuthHeaders } from "@/src/api/client";
import { theme } from "@/src/theme";

/**
 * DynamicFormRenderer — Phase 2 schema-driven form.
 *
 * Fetches GET /api/documents/{documentId}/schema (returned by the backend
 * PDF parser) and renders one input per detected field. All inputs are
 * built from React Native primitives — no web form libraries, no HTML.
 * Submission POSTs to /api/documents/{documentId}/submissions.
 *
 * Field-type mapping:
 *   - text        -> <TextInput>
 *   - checkbox    -> <Switch> (or options-driven checkbox group)
 *   - radio       -> pressable radio group (driven by field.options)
 *   - combobox    -> pressable inline selector (options list)
 *   - listbox     -> pressable inline selector (multi-select)
 *   - signature   -> placeholder ("Signature capture coming soon")
 *   - button      -> skipped (buttons in a PDF aren't user input)
 *
 * State: one flat useState({}) keyed by `field_name`. No external state lib.
 * Fields are grouped by `page` with a "Page N of M" divider so 180+ fields
 * remain navigable. Wrapped in ScrollView + KeyboardAvoidingView.
 *
 * Note: this component intentionally REPLACES the legacy FillableFormModal
 * in the documents tab. The legacy component still lives at
 * `_LegacyEmploymentForm.tsx` and can be re-imported to roll back.
 */

// ---- Types matching the /schema response ----------------------------------
type SchemaField = {
  field_name: string;
  field_type:
    | "text"
    | "checkbox"
    | "radio"
    | "combobox"
    | "listbox"
    | "signature"
    | "button";
  page: number;
  position?: { x0: number; y0: number; x1: number; y1: number };
  options?: string[];
  required?: boolean;
  value?: string | null;
  source?: string;
};

type SchemaEnvelope = {
  document_id: string;
  field_count: number;
  source: "acroform" | "text-heuristic" | "empty";
  fields: SchemaField[];
  extracted_at?: string;
  parser_version?: string;
};

type Props = {
  documentId: string | null;
  documentTitle?: string;
  visible: boolean;
  onClose: () => void;
  onSubmitted?: (submissionId: string, populated: number) => void;
};

// Deduped label — the schema can contain fields like "Date (2)". Show the
// display label without the disambiguation suffix but keep the raw name as
// the state key.
const displayLabel = (name: string) => name.replace(/\s*\(\d+\)\s*$/, "");

// Some PDF field names are laid out ALL CAPS or embedded prefixes; keep the
// raw text but trim wrapping quotes / trailing colons for the label view.
const cleanLabel = (name: string) =>
  displayLabel(name).replace(/^["']|["']$/g, "").replace(/[:\s]+$/, "");

export default function DynamicFormRenderer({
  documentId,
  documentTitle,
  visible,
  onClose,
  onSubmitted,
}: Props) {
  const [envelope, setEnvelope] = useState<SchemaEnvelope | null>(null);
  const [values, setValues] = useState<Record<string, any>>({});
  const [loading, setLoading] = useState(false);
  const [submitting, setSubmitting] = useState(false);
  const [error, setError] = useState<string | null>(null);

  // Fetch schema whenever the modal opens for a new documentId.
  const loadSchema = useCallback(async () => {
    if (!documentId) return;
    setLoading(true);
    setError(null);
    setValues({});
    setEnvelope(null);
    try {
      const headers = await getAuthHeaders();
      const res = await fetch(
        `${API_BASE}/documents/${documentId}/schema`,
        { headers }
      );
      if (!res.ok) {
        throw new Error(`Schema fetch failed: ${res.status}`);
      }
      const body: SchemaEnvelope = await res.json();
      setEnvelope(body);
      // Seed initial values from schema.value where present so previously
      // populated defaults (e.g. checkboxes preset in the PDF) appear.
      const seed: Record<string, any> = {};
      for (const f of body.fields || []) {
        if (f.value != null && f.value !== "") {
          seed[f.field_name] = f.value;
        } else if (f.field_type === "checkbox") {
          seed[f.field_name] = false;
        }
      }
      setValues(seed);
    } catch (e: any) {
      setError(e?.message || "Failed to load form");
    } finally {
      setLoading(false);
    }
  }, [documentId]);

  useEffect(() => {
    if (visible) loadSchema();
  }, [visible, loadSchema]);

  // Group fields by page (1-indexed) for the "Page N of M" dividers. Buttons
  // are skipped entirely (not user input) — see mapping table above.
  const grouped = useMemo(() => {
    const bins: Record<number, SchemaField[]> = {};
    for (const f of envelope?.fields || []) {
      if (f.field_type === "button") continue;
      const p = f.page || 1;
      (bins[p] = bins[p] || []).push(f);
    }
    const pages = Object.keys(bins)
      .map((k) => Number(k))
      .sort((a, b) => a - b);
    return { pages, bins };
  }, [envelope]);

  const totalPages = grouped.pages.length;
  const fieldCount = envelope?.fields?.filter(
    (f) => f.field_type !== "button"
  ).length ?? 0;

  const set = useCallback((k: string, v: any) => {
    setValues((s) => ({ ...s, [k]: v }));
  }, []);

  const submit = async () => {
    if (!documentId || !envelope) return;
    setSubmitting(true);
    setError(null);
    try {
      const headers = await getAuthHeaders();
      const res = await fetch(
        `${API_BASE}/documents/${documentId}/submissions`,
        {
          method: "POST",
          headers,
          body: JSON.stringify({ values }),
        }
      );
      if (!res.ok) {
        const t = await res.text();
        throw new Error(t || `Submit failed: ${res.status}`);
      }
      const body = await res.json();
      Alert.alert(
        "Submitted",
        `Saved ${body.field_count ?? 0} field${(body.field_count ?? 0) === 1 ? "" : "s"} for ${documentTitle || "this document"}.`
      );
      onSubmitted?.(body.id, body.field_count ?? 0);
      onClose();
    } catch (e: any) {
      const msg = e?.message || "Submit failed";
      setError(msg);
      Alert.alert("Submit failed", msg);
    } finally {
      setSubmitting(false);
    }
  };

  // -------- Field renderers ------------------------------------------------
  const renderTextField = (f: SchemaField) => (
    <View style={styles.fieldBlock}>
      <Text style={styles.label}>
        {cleanLabel(f.field_name)}
        {f.required ? " *" : ""}
      </Text>
      <TextInput
        testID={`dyn-field-${f.field_name}`}
        value={values[f.field_name] ?? ""}
        onChangeText={(v) => set(f.field_name, v)}
        placeholder=""
        placeholderTextColor={theme.colors.muted}
        style={styles.input}
        multiline={false}
      />
    </View>
  );

  const renderCheckboxField = (f: SchemaField) => {
    // If the widget carries options (e.g. YES/NO from the text-heuristic),
    // render as an options-driven multi-check row. Otherwise a single
    // Switch bound to a boolean value.
    if (Array.isArray(f.options) && f.options.length > 0) {
      const selected: string[] = Array.isArray(values[f.field_name])
        ? values[f.field_name]
        : [];
      const toggle = (opt: string) =>
        set(
          f.field_name,
          selected.includes(opt)
            ? selected.filter((x) => x !== opt)
            : [...selected, opt]
        );
      return (
        <View style={styles.fieldBlock}>
          <Text style={styles.label}>
            {cleanLabel(f.field_name)}
            {f.required ? " *" : ""}
          </Text>
          <View style={styles.chipRow}>
            {f.options.map((opt) => {
              const on = selected.includes(opt);
              return (
                <Pressable
                  key={opt}
                  testID={`dyn-check-${f.field_name}-${opt}`}
                  onPress={() => toggle(opt)}
                  style={[styles.checkChip, on && styles.checkChipOn]}
                >
                  <View style={[styles.checkbox, on && styles.checkboxOn]}>
                    {on && <Ionicons name="checkmark" size={13} color="#fff" />}
                  </View>
                  <Text style={[styles.checkText, on && styles.checkTextOn]}>{opt}</Text>
                </Pressable>
              );
            })}
          </View>
        </View>
      );
    }
    const on = !!values[f.field_name];
    return (
      <Pressable
        onPress={() => set(f.field_name, !on)}
        style={[styles.singleCheckRow, on && styles.singleCheckRowOn]}
        testID={`dyn-check-${f.field_name}`}
      >
        <Switch
          value={on}
          onValueChange={(v) => set(f.field_name, v)}
          trackColor={{ false: theme.colors.border, true: theme.colors.brandPrimary }}
          thumbColor={on ? "#fff" : "#f4f3f4"}
        />
        <Text style={styles.singleCheckLabel}>
          {cleanLabel(f.field_name)}
          {f.required ? " *" : ""}
        </Text>
      </Pressable>
    );
  };

  const renderRadioField = (f: SchemaField) => {
    const opts = f.options && f.options.length > 0 ? f.options : ["Yes", "No"];
    const current = values[f.field_name] ?? "";
    return (
      <View style={styles.fieldBlock}>
        <Text style={styles.label}>
          {cleanLabel(f.field_name)}
          {f.required ? " *" : ""}
        </Text>
        <View style={styles.chipRow}>
          {opts.map((opt) => {
            const on = current === opt;
            return (
              <Pressable
                key={opt}
                testID={`dyn-radio-${f.field_name}-${opt}`}
                onPress={() => set(f.field_name, opt)}
                style={[styles.radioChip, on && styles.radioChipOn]}
              >
                <View style={[styles.radioDot, on && styles.radioDotOn]} />
                <Text style={[styles.checkText, on && styles.checkTextOn]}>{opt}</Text>
              </Pressable>
            );
          })}
        </View>
      </View>
    );
  };

  const renderSelectField = (f: SchemaField, multi: boolean) => {
    const opts = f.options && f.options.length > 0 ? f.options : [];
    if (opts.length === 0) {
      // No options in schema — degrade to a plain text input rather than
      // showing an empty picker.
      return renderTextField(f);
    }
    if (multi) {
      const selected: string[] = Array.isArray(values[f.field_name])
        ? values[f.field_name]
        : [];
      const toggle = (opt: string) =>
        set(
          f.field_name,
          selected.includes(opt)
            ? selected.filter((x) => x !== opt)
            : [...selected, opt]
        );
      return (
        <View style={styles.fieldBlock}>
          <Text style={styles.label}>
            {cleanLabel(f.field_name)}
            {f.required ? " *" : ""}{" "}
            <Text style={styles.hint}>(select all that apply)</Text>
          </Text>
          <View style={styles.chipRow}>
            {opts.map((opt) => {
              const on = selected.includes(opt);
              return (
                <Pressable
                  key={opt}
                  onPress={() => toggle(opt)}
                  style={[styles.checkChip, on && styles.checkChipOn]}
                >
                  <View style={[styles.checkbox, on && styles.checkboxOn]}>
                    {on && <Ionicons name="checkmark" size={13} color="#fff" />}
                  </View>
                  <Text style={[styles.checkText, on && styles.checkTextOn]}>{opt}</Text>
                </Pressable>
              );
            })}
          </View>
        </View>
      );
    }
    // combobox — single-select pressable list
    const current = values[f.field_name] ?? "";
    return (
      <View style={styles.fieldBlock}>
        <Text style={styles.label}>
          {cleanLabel(f.field_name)}
          {f.required ? " *" : ""}
        </Text>
        <View style={styles.chipRow}>
          {opts.map((opt) => {
            const on = current === opt;
            return (
              <Pressable
                key={opt}
                onPress={() => set(f.field_name, opt)}
                style={[styles.radioChip, on && styles.radioChipOn]}
              >
                <Text style={[styles.checkText, on && styles.checkTextOn]}>{opt}</Text>
              </Pressable>
            );
          })}
        </View>
      </View>
    );
  };

  const renderSignatureField = (f: SchemaField) => (
    <View style={styles.fieldBlock}>
      <Text style={styles.label}>
        {cleanLabel(f.field_name)}
        {f.required ? " *" : ""}
      </Text>
      <View style={styles.signaturePlaceholder}>
        <Ionicons name="create-outline" size={20} color={theme.colors.muted} />
        <Text style={styles.signatureText}>Signature capture coming soon</Text>
      </View>
    </View>
  );

  const renderField = (f: SchemaField) => {
    let node: React.ReactNode = null;
    switch (f.field_type) {
      case "text":
        node = renderTextField(f);
        break;
      case "checkbox":
        node = renderCheckboxField(f);
        break;
      case "radio":
        node = renderRadioField(f);
        break;
      case "combobox":
        node = renderSelectField(f, false);
        break;
      case "listbox":
        node = renderSelectField(f, true);
        break;
      case "signature":
        node = renderSignatureField(f);
        break;
      case "button":
      default:
        return null;
    }
    return <View key={f.field_name}>{node}</View>;
  };

  // -------- Render ---------------------------------------------------------
  return (
    <Modal
      visible={visible}
      animationType="slide"
      onRequestClose={onClose}
      presentationStyle="fullScreen"
    >
      <KeyboardAvoidingView
        behavior={Platform.OS === "ios" ? "padding" : undefined}
        style={styles.root}
        keyboardVerticalOffset={Platform.OS === "ios" ? 0 : 0}
      >
        <View style={styles.header}>
          <Pressable onPress={onClose} hitSlop={10} testID="dyn-form-close">
            <Ionicons name="close" size={24} color="#fff" />
          </Pressable>
          <View style={{ flex: 1 }}>
            <Text style={styles.title} numberOfLines={1}>
              {documentTitle || "Form"}
            </Text>
            {envelope ? (
              <Text style={styles.subtitle}>
                {fieldCount} field{fieldCount === 1 ? "" : "s"} · {totalPages} page{totalPages === 1 ? "" : "s"}
              </Text>
            ) : null}
          </View>
          <Pressable
            testID="dyn-form-submit"
            onPress={submit}
            disabled={submitting || !envelope || fieldCount === 0}
            style={[
              styles.submitBtn,
              (submitting || !envelope || fieldCount === 0) && { opacity: 0.5 },
            ]}
          >
            {submitting ? (
              <ActivityIndicator color={theme.colors.brandPrimary} />
            ) : (
              <Text style={styles.submitText}>Submit</Text>
            )}
          </Pressable>
        </View>

        {loading ? (
          <View style={styles.center}>
            <ActivityIndicator color={theme.colors.brandPrimary} />
            <Text style={{ color: theme.colors.muted, marginTop: 8 }}>
              Loading form fields…
            </Text>
          </View>
        ) : error ? (
          <View style={styles.center}>
            <Ionicons name="alert-circle" size={28} color={theme.colors.danger} />
            <Text style={styles.errorText}>{error}</Text>
            <Pressable onPress={loadSchema} style={styles.retryBtn}>
              <Text style={styles.retryText}>Retry</Text>
            </Pressable>
          </View>
        ) : !envelope || fieldCount === 0 ? (
          <View style={styles.center}>
            <Ionicons name="document-outline" size={32} color={theme.colors.muted} />
            <Text style={styles.emptyText}>
              No fillable fields were detected in this PDF.{"\n"}
              View it as a PDF instead.
            </Text>
          </View>
        ) : (
          <ScrollView
            keyboardShouldPersistTaps="handled"
            contentContainerStyle={styles.scrollContent}
          >
            {grouped.pages.map((pageNum, pageIdx) => {
              const pageFields = grouped.bins[pageNum] || [];
              return (
                <View key={pageNum} style={styles.pageGroup}>
                  <View style={styles.pageHeaderRow}>
                    <View style={styles.pageBadge}>
                      <Text style={styles.pageBadgeText}>
                        Page {pageIdx + 1} of {totalPages}
                      </Text>
                    </View>
                    <Text style={styles.pageCount}>
                      {pageFields.length} field{pageFields.length === 1 ? "" : "s"}
                    </Text>
                  </View>
                  {pageFields.map(renderField)}
                </View>
              );
            })}
            <Pressable
              testID="dyn-form-submit-footer"
              onPress={submit}
              disabled={submitting}
              style={[styles.footerSubmit, submitting && { opacity: 0.5 }]}
            >
              {submitting ? (
                <ActivityIndicator color="#fff" />
              ) : (
                <>
                  <Ionicons name="checkmark-circle" size={18} color="#fff" />
                  <Text style={styles.footerSubmitText}>Submit form</Text>
                </>
              )}
            </Pressable>
          </ScrollView>
        )}
      </KeyboardAvoidingView>
    </Modal>
  );
}

const styles = StyleSheet.create({
  root: { flex: 1, backgroundColor: theme.colors.background },
  header: {
    backgroundColor: theme.colors.brandPrimary,
    paddingHorizontal: 14,
    paddingVertical: 12,
    flexDirection: "row",
    alignItems: "center",
    gap: 12,
  },
  title: { color: "#fff", fontWeight: "700", fontSize: 15 },
  subtitle: { color: "rgba(255,255,255,0.75)", fontSize: 11, marginTop: 2 },
  submitBtn: {
    backgroundColor: "#fff",
    paddingHorizontal: 14,
    paddingVertical: 8,
    borderRadius: 999,
    minWidth: 76,
    alignItems: "center",
  },
  submitText: { color: theme.colors.brandPrimary, fontWeight: "700", fontSize: 13 },

  center: { flex: 1, alignItems: "center", justifyContent: "center", padding: 24 },
  errorText: {
    color: theme.colors.danger,
    marginTop: 12,
    textAlign: "center",
    fontWeight: "600",
  },
  emptyText: {
    color: theme.colors.muted,
    marginTop: 10,
    textAlign: "center",
    lineHeight: 20,
  },
  retryBtn: {
    marginTop: 14,
    backgroundColor: theme.colors.brandPrimary,
    paddingHorizontal: 20,
    paddingVertical: 10,
    borderRadius: 8,
  },
  retryText: { color: "#fff", fontWeight: "700" },

  scrollContent: { padding: 16, paddingBottom: 60, gap: 20 },
  pageGroup: { gap: 14 },
  pageHeaderRow: {
    flexDirection: "row",
    alignItems: "center",
    justifyContent: "space-between",
    marginBottom: 4,
  },
  pageBadge: {
    backgroundColor: theme.colors.brandTertiary,
    paddingHorizontal: 10,
    paddingVertical: 5,
    borderRadius: 999,
  },
  pageBadgeText: {
    color: theme.colors.brandPrimary,
    fontWeight: "800",
    fontSize: 11,
    letterSpacing: 0.4,
    textTransform: "uppercase",
  },
  pageCount: { fontSize: 11, color: theme.colors.muted, fontWeight: "600" },

  fieldBlock: { gap: 6 },
  label: { fontSize: 12, fontWeight: "700", color: theme.colors.onSurface },
  hint: { fontWeight: "500", color: theme.colors.muted, fontSize: 11 },
  input: {
    backgroundColor: theme.colors.surface,
    borderRadius: 10,
    padding: 12,
    borderWidth: 1,
    borderColor: theme.colors.border,
    color: theme.colors.onSurface,
    fontSize: 14,
    minHeight: 44,
  },

  chipRow: { flexDirection: "row", flexWrap: "wrap", gap: 8 },
  checkChip: {
    flexDirection: "row",
    alignItems: "center",
    gap: 8,
    paddingHorizontal: 12,
    paddingVertical: 8,
    borderRadius: 999,
    backgroundColor: theme.colors.surface,
    borderWidth: 1,
    borderColor: theme.colors.border,
  },
  checkChipOn: {
    backgroundColor: theme.colors.brandTertiary,
    borderColor: theme.colors.brandPrimary,
  },
  checkbox: {
    width: 18,
    height: 18,
    borderRadius: 4,
    borderWidth: 2,
    borderColor: theme.colors.borderStrong,
    alignItems: "center",
    justifyContent: "center",
  },
  checkboxOn: {
    backgroundColor: theme.colors.success,
    borderColor: theme.colors.success,
  },
  checkText: { fontSize: 12, color: theme.colors.onSurface, fontWeight: "600" },
  checkTextOn: { color: theme.colors.brandPrimary },

  radioChip: {
    flexDirection: "row",
    alignItems: "center",
    gap: 8,
    paddingHorizontal: 12,
    paddingVertical: 8,
    borderRadius: 999,
    backgroundColor: theme.colors.surface,
    borderWidth: 1,
    borderColor: theme.colors.border,
  },
  radioChipOn: {
    backgroundColor: theme.colors.brandTertiary,
    borderColor: theme.colors.brandPrimary,
  },
  radioDot: {
    width: 14,
    height: 14,
    borderRadius: 7,
    borderWidth: 2,
    borderColor: theme.colors.borderStrong,
    backgroundColor: "#fff",
  },
  radioDotOn: {
    backgroundColor: theme.colors.brandPrimary,
    borderColor: theme.colors.brandPrimary,
  },

  singleCheckRow: {
    flexDirection: "row",
    alignItems: "center",
    gap: 12,
    padding: 12,
    borderRadius: 10,
    backgroundColor: theme.colors.surface,
    borderWidth: 1,
    borderColor: theme.colors.border,
  },
  singleCheckRowOn: {
    backgroundColor: theme.colors.brandTertiary,
    borderColor: theme.colors.brandPrimary,
  },
  singleCheckLabel: {
    flex: 1,
    fontSize: 13,
    fontWeight: "600",
    color: theme.colors.onSurface,
  },

  signaturePlaceholder: {
    flexDirection: "row",
    alignItems: "center",
    gap: 10,
    padding: 14,
    borderRadius: 10,
    borderWidth: 1,
    borderStyle: "dashed",
    borderColor: theme.colors.border,
    backgroundColor: theme.colors.surfaceSecondary,
  },
  signatureText: {
    color: theme.colors.muted,
    fontStyle: "italic",
    fontSize: 12,
    fontWeight: "600",
  },

  footerSubmit: {
    marginTop: 8,
    flexDirection: "row",
    alignItems: "center",
    justifyContent: "center",
    gap: 8,
    backgroundColor: theme.colors.brandPrimary,
    paddingVertical: 14,
    borderRadius: 12,
  },
  footerSubmitText: { color: "#fff", fontWeight: "800", fontSize: 14 },
});
