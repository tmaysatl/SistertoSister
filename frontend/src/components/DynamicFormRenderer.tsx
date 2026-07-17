import React, { useCallback, useEffect, useMemo, useRef, useState } from "react";
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
import AsyncStorage from "@react-native-async-storage/async-storage";
import SignatureScreen, { SignatureViewRef } from "react-native-signature-canvas";
import { useSafeAreaInsets } from "react-native-safe-area-context";
import { API_BASE, getAuthHeaders } from "@/src/api/client";
import { theme } from "@/src/theme";
import { PdfViewerModal } from "@/src/components/pdf/PdfViewerModal";
import { HEADER_CONTENT_HEIGHT } from "@/src/components/ScreenHeader";

/**
 * DynamicFormRenderer — Phase 2 (baseline) + Phase 3 (validation, view
 * original PDF, draft auto-save, signature capture).
 *
 * Fetches GET /api/documents/{documentId}/schema and renders one input
 * per detected field using React Native primitives only.
 *
 * Phase 3 additions:
 *   - Field-level validation: `required` (from schema) is enforced, plus
 *     heuristic format checks on text fields based on the field name
 *     (email, date, phone, SSN, ZIP). Errors render inline and block Submit.
 *   - "View Original PDF" button in the header opens the PdfViewerModal
 *     on top of the form so users can cross-reference the source form.
 *   - Draft auto-save to AsyncStorage under `formDraft:{documentId}` on a
 *     500ms debounce. Restored automatically on next open. Cleared on
 *     successful submit or explicit "Discard draft".
 *   - Signature capture: replaces the "coming soon" placeholder with a
 *     SignatureScreen modal (react-native-signature-canvas) that stores
 *     the base64 PNG in values[field_name].
 */

// ---- Heuristic format validators ------------------------------------------
// The /schema response has no explicit format metadata (Phase 1 kept it
// minimal), so we derive format hints from the field name — same approach
// most legacy fillable-form apps use. Keep the regexes deliberately
// permissive; we're guarding against obvious typos, not enforcing strict
// bank-grade formats.
const FMT_EMAIL = /^[^\s@]+@[^\s@]+\.[^\s@]{2,}$/i;
const FMT_DATE = /^(?:\d{4}-\d{2}-\d{2}|\d{1,2}\/\d{1,2}\/\d{2,4})$/;
const FMT_PHONE = /^[\d\s()+.-]{7,}$/;
const FMT_SSN = /^\d{3}-?\d{2}-?\d{4}$/;
const FMT_ZIP = /^\d{5}(?:-\d{4})?$/;

function inferFormat(fieldName: string): {
  test: (v: string) => boolean;
  hint: string;
} | null {
  const n = fieldName.toLowerCase();
  if (/e[-\s]?mail\b/.test(n)) return { test: (v) => FMT_EMAIL.test(v), hint: "Enter a valid email" };
  if (/\b(date|dob|d\.o\.b|birth)\b/.test(n)) return { test: (v) => FMT_DATE.test(v), hint: "Use YYYY-MM-DD or MM/DD/YYYY" };
  if (/\bphone|cell|mobile|tel\b/.test(n)) return { test: (v) => FMT_PHONE.test(v), hint: "Enter a valid phone number" };
  if (/\bssn|social security\b/.test(n)) return { test: (v) => FMT_SSN.test(v), hint: "Format: 123-45-6789" };
  if (/\bzip|postal\b/.test(n)) return { test: (v) => FMT_ZIP.test(v), hint: "5 or 9-digit ZIP" };
  return null;
}

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
  // Phase 4 — safe-area handling for the modal presentation. This modal is
  // presented on top of the whole app, so it does NOT inherit the parent's
  // top inset — we must reserve `insets.top` inside the coloured header
  // ourselves. `insets.bottom` is pushed onto the ScrollView content so
  // the footer submit button never sits under the home indicator.
  const insets = useSafeAreaInsets();
  const [envelope, setEnvelope] = useState<SchemaEnvelope | null>(null);
  const [values, setValues] = useState<Record<string, any>>({});
  const [loading, setLoading] = useState(false);
  const [submitting, setSubmitting] = useState(false);
  const [error, setError] = useState<string | null>(null);
  // Phase 3 — validation errors keyed by field_name; populated on Submit
  // and cleared as the user edits the offending field.
  const [errors, setErrors] = useState<Record<string, string>>({});
  // Phase 3 — nested modals
  const [viewOriginal, setViewOriginal] = useState(false);
  const [signingField, setSigningField] = useState<SchemaField | null>(null);
  // Phase 3 — draft-restore banner (auto-dismisses after 4s)
  const [restoredBanner, setRestoredBanner] = useState(false);
  // Ref-mirror of `values` for the debounced saver (avoids stale closures).
  const valuesRef = useRef<Record<string, any>>({});
  valuesRef.current = values;

  const draftKey = documentId ? `formDraft:${documentId}` : null;

  // Fetch schema whenever the modal opens for a new documentId, then merge
  // any locally-persisted draft on top so the user picks up where they left off.
  const loadSchema = useCallback(async () => {
    if (!documentId) return;
    setLoading(true);
    setError(null);
    setErrors({});
    setValues({});
    setEnvelope(null);
    setRestoredBanner(false);
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
      // Defensive: collapse any duplicate field entries by field_name. An
      // AcroForm radio group shares one field_name across its option widgets
      // (Yes/No), so an older cached schema may list the same field twice.
      // They are the SAME logical field bound to one value — keep the first
      // so we don't render the question twice or collide on React keys.
      if (Array.isArray(body.fields)) {
        const seen = new Set<string>();
        body.fields = body.fields.filter((f) => {
          if (seen.has(f.field_name)) return false;
          seen.add(f.field_name);
          return true;
        });
      }
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
      // Phase 3: overlay any AsyncStorage draft on top of the seed.
      let hadDraft = false;
      if (draftKey) {
        try {
          const raw = await AsyncStorage.getItem(draftKey);
          if (raw) {
            const parsed = JSON.parse(raw);
            if (parsed && typeof parsed === "object") {
              Object.assign(seed, parsed);
              hadDraft = true;
            }
          }
        } catch {
          /* draft parse failure is non-fatal — just skip restore */
        }
      }
      setValues(seed);
      if (hadDraft) {
        setRestoredBanner(true);
        setTimeout(() => setRestoredBanner(false), 4000);
      }
    } catch (e: any) {
      setError(e?.message || "Failed to load form");
    } finally {
      setLoading(false);
    }
  }, [documentId, draftKey]);

  useEffect(() => {
    if (visible) loadSchema();
  }, [visible, loadSchema]);

  // Phase 3 — debounced draft auto-save. Persists `values` to AsyncStorage
  // 500ms after the last edit. Skipped while loading (would otherwise clobber
  // the stored draft with the seed values before we merge them in).
  useEffect(() => {
    if (!visible || !draftKey || loading) return;
    const handle = setTimeout(() => {
      // Only persist if there's at least one non-empty value — avoids
      // writing an empty {} draft on first mount.
      const v = valuesRef.current;
      const hasContent = Object.values(v).some(
        (x) => x !== "" && x !== false && x != null &&
               !(Array.isArray(x) && x.length === 0)
      );
      if (hasContent) {
        AsyncStorage.setItem(draftKey, JSON.stringify(v)).catch(() => {});
      }
    }, 500);
    return () => clearTimeout(handle);
  }, [values, visible, draftKey, loading]);

  const discardDraft = useCallback(async () => {
    if (!draftKey) return;
    Alert.alert(
      "Discard draft?",
      "This clears the locally saved values for this form. You can't undo this.",
      [
        { text: "Cancel", style: "cancel" },
        {
          text: "Discard",
          style: "destructive",
          onPress: async () => {
            await AsyncStorage.removeItem(draftKey).catch(() => {});
            setValues({});
            setErrors({});
            setRestoredBanner(false);
          },
        },
      ]
    );
  }, [draftKey]);

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
    // Clear a stale error for this field as the user edits.
    setErrors((prev) => {
      if (!prev[k]) return prev;
      const next = { ...prev };
      delete next[k];
      return next;
    });
  }, []);

  // Phase 3 — validation pass. Returns a map of field_name → error message.
  const validate = useCallback((): Record<string, string> => {
    const out: Record<string, string> = {};
    for (const f of envelope?.fields || []) {
      if (f.field_type === "button") continue;
      const v = values[f.field_name];
      // Required check first
      if (f.required) {
        const empty =
          v == null ||
          v === "" ||
          v === false ||
          (Array.isArray(v) && v.length === 0);
        if (empty) {
          out[f.field_name] = "This field is required";
          continue;
        }
      }
      // Format check (text fields only, only if the user typed something)
      if (f.field_type === "text" && typeof v === "string" && v.trim() !== "") {
        const fmt = inferFormat(f.field_name);
        if (fmt && !fmt.test(v.trim())) {
          out[f.field_name] = fmt.hint;
        }
      }
    }
    return out;
  }, [envelope, values]);

  const submit = async () => {
    if (!documentId || !envelope) return;
    const found = validate();
    if (Object.keys(found).length > 0) {
      setErrors(found);
      Alert.alert(
        "Fix errors",
        `${Object.keys(found).length} field${Object.keys(found).length === 1 ? "" : "s"} need attention.`,
      );
      return;
    }
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
      // Phase 3 — draft is stale after successful submit; clear it so the
      // next open starts clean.
      if (draftKey) {
        AsyncStorage.removeItem(draftKey).catch(() => {});
      }
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
  const renderTextField = (f: SchemaField) => {
    const err = errors[f.field_name];
    const fmt = inferFormat(f.field_name);
    return (
      <View style={styles.fieldBlock}>
        <Text style={styles.label}>
          {cleanLabel(f.field_name)}
          {f.required ? " *" : ""}
          {fmt ? <Text style={styles.hint}>  · {fmt.hint}</Text> : null}
        </Text>
        <TextInput
          testID={`dyn-field-${f.field_name}`}
          value={values[f.field_name] ?? ""}
          onChangeText={(v) => set(f.field_name, v)}
          placeholder=""
          placeholderTextColor={theme.colors.muted}
          style={[styles.input, err && styles.inputError]}
          multiline={false}
          keyboardType={
            /email/i.test(f.field_name) ? "email-address" :
            /phone|cell|mobile|tel/i.test(f.field_name) ? "phone-pad" :
            /zip|postal|ssn|social/i.test(f.field_name) ? "numbers-and-punctuation" :
            "default"
          }
          autoCapitalize={/email/i.test(f.field_name) ? "none" : "sentences"}
        />
        {err ? (
          <Text testID={`dyn-error-${f.field_name}`} style={styles.errorInline}>
            {err}
          </Text>
        ) : null}
      </View>
    );
  };

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
            {f.options.map((opt, idx) => {
              const on = selected.includes(opt);
              return (
                <Pressable
                  key={`${opt}-${idx}`}
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
          {opts.map((opt, idx) => {
            const on = current === opt;
            return (
              <Pressable
                key={`${opt}-${idx}`}
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
            {opts.map((opt, idx) => {
              const on = selected.includes(opt);
              return (
                <Pressable
                  key={`${opt}-${idx}`}
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
          {opts.map((opt, idx) => {
            const on = current === opt;
            return (
              <Pressable
                key={`${opt}-${idx}`}
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

  const renderSignatureField = (f: SchemaField) => {
    const captured: string | null =
      typeof values[f.field_name] === "string" && values[f.field_name].startsWith("data:")
        ? values[f.field_name]
        : null;
    const err = errors[f.field_name];
    return (
      <View style={styles.fieldBlock}>
        <Text style={styles.label}>
          {cleanLabel(f.field_name)}
          {f.required ? " *" : ""}
        </Text>
        <Pressable
          testID={`dyn-signature-${f.field_name}`}
          onPress={() => setSigningField(f)}
          style={[
            styles.signaturePlaceholder,
            captured && styles.signaturePlaceholderFilled,
            err && styles.inputError,
          ]}
        >
          {captured ? (
            <View style={styles.signaturePreviewRow}>
              <Ionicons name="checkmark-circle" size={22} color={theme.colors.success} />
              <View style={{ flex: 1 }}>
                <Text style={styles.signatureCapturedLabel}>Signature captured</Text>
                <Text style={styles.signatureCapturedHint}>Tap to re-sign</Text>
              </View>
              <Pressable
                onPress={(e) => { e.stopPropagation(); set(f.field_name, ""); }}
                hitSlop={10}
                testID={`dyn-signature-clear-${f.field_name}`}
              >
                <Ionicons name="trash-outline" size={18} color={theme.colors.danger} />
              </Pressable>
            </View>
          ) : (
            <>
              <Ionicons name="create-outline" size={20} color={theme.colors.brandPrimary} />
              <Text style={styles.signatureText}>Tap to sign</Text>
            </>
          )}
        </Pressable>
        {err ? <Text style={styles.errorInline}>{err}</Text> : null}
      </View>
    );
  };

  const renderField = (f: SchemaField, idx: number) => {
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
    return <View key={`${f.field_name}-${idx}`}>{node}</View>;
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
        <View
          style={[
            styles.header,
            {
              paddingTop: insets.top,
              height: HEADER_CONTENT_HEIGHT + insets.top,
            },
          ]}
        >
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
          {/* Phase 3 — View Original PDF button (only if the doc has a
              backing file; Phase 1 metadata-only docs never show this). */}
          {envelope && fieldCount > 0 ? (
            <Pressable
              testID="dyn-view-original"
              onPress={() => setViewOriginal(true)}
              hitSlop={8}
              style={styles.headerIconBtn}
            >
              <Ionicons name="document-text-outline" size={20} color="#fff" />
            </Pressable>
          ) : null}
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
            contentContainerStyle={[
              styles.scrollContent,
              { paddingBottom: 60 + insets.bottom },
            ]}
          >
            {/* Phase 3 — draft restored banner (auto-hides after 4s). */}
            {restoredBanner ? (
              <View style={styles.draftBanner} testID="dyn-draft-restored">
                <Ionicons name="save-outline" size={16} color={theme.colors.brandPrimary} />
                <Text style={styles.draftBannerText}>
                  Restored your saved draft. Auto-saving as you type.
                </Text>
                <Pressable onPress={discardDraft} hitSlop={8} testID="dyn-draft-discard">
                  <Text style={styles.draftBannerAction}>Discard</Text>
                </Pressable>
              </View>
            ) : null}
            {Object.keys(errors).length > 0 ? (
              <View style={styles.errorBanner} testID="dyn-error-banner">
                <Ionicons name="alert-circle" size={16} color={theme.colors.danger} />
                <Text style={styles.errorBannerText}>
                  {Object.keys(errors).length} field{Object.keys(errors).length === 1 ? "" : "s"} need attention
                </Text>
              </View>
            ) : null}
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

      {/* Phase 3 — View Original PDF (nested modal on top of the form) */}
      {viewOriginal && documentId ? (
        <PdfViewerModal
          visible={viewOriginal}
          path={`/documents/${documentId}/stamped`}
          onClose={() => setViewOriginal(false)}
          title={documentTitle || "Original PDF"}
        />
      ) : null}

      {/* Phase 3 — Signature capture sub-modal */}
      <SignatureCaptureModal
        visible={!!signingField}
        fieldLabel={signingField ? cleanLabel(signingField.field_name) : ""}
        initial={signingField ? values[signingField.field_name] : undefined}
        onClose={() => setSigningField(null)}
        onSave={(b64) => {
          if (signingField) set(signingField.field_name, b64);
          setSigningField(null);
        }}
      />
    </Modal>
  );
}

// ---- SignatureCaptureModal ------------------------------------------------
// Wraps react-native-signature-canvas in a full-screen Modal so users can
// draw with their finger, save (returns a data-URL base64 PNG), clear, or
// cancel. Kept private to this file — DynamicFormRenderer owns the state
// of which field is being signed.
type SigProps = {
  visible: boolean;
  fieldLabel: string;
  initial?: string;
  onClose: () => void;
  onSave: (base64: string) => void;
};

function SignatureCaptureModal({ visible, fieldLabel, initial, onClose, onSave }: SigProps) {
  const ref = useRef<SignatureViewRef>(null);
  const [saving, setSaving] = useState(false);
  // Same modal-safe-area concern: reserve insets on the coloured header
  // and add insets.bottom padding to the footer so nothing sits under
  // the home indicator.
  const insets = useSafeAreaInsets();

  const handleOK = (b64: string) => {
    setSaving(false);
    if (b64 && b64.length > 0) onSave(b64);
  };

  const handleEmpty = () => {
    setSaving(false);
    Alert.alert("Empty signature", "Please draw your signature before saving.");
  };

  return (
    <Modal
      visible={visible}
      animationType="slide"
      presentationStyle="fullScreen"
      onRequestClose={onClose}
    >
      <View style={styles.sigRoot}>
        <View
          style={[
            styles.sigHeader,
            {
              paddingTop: insets.top,
              height: HEADER_CONTENT_HEIGHT + insets.top,
            },
          ]}
        >
          <Pressable onPress={onClose} hitSlop={10} testID="dyn-sig-cancel">
            <Ionicons name="close" size={22} color="#fff" />
          </Pressable>
          <Text style={styles.sigTitle} numberOfLines={1}>
            Sign: {fieldLabel || "signature"}
          </Text>
          <Pressable
            testID="dyn-sig-save"
            onPress={() => { setSaving(true); ref.current?.readSignature(); }}
            style={styles.sigSaveBtn}
            disabled={saving}
          >
            {saving ? (
              <ActivityIndicator color={theme.colors.brandPrimary} />
            ) : (
              <Text style={styles.sigSaveText}>Save</Text>
            )}
          </Pressable>
        </View>
        <View style={styles.sigCanvasWrap}>
          <SignatureScreen
            ref={ref}
            onOK={handleOK}
            onEmpty={handleEmpty}
            dataURL={initial && typeof initial === "string" && initial.startsWith("data:") ? initial : undefined}
            descriptionText=""
            imageType="image/png"
            webStyle={SIG_WEB_STYLE}
          />
        </View>
        <View style={[styles.sigFooter, { paddingBottom: 12 + insets.bottom }]}>
          <Pressable
            testID="dyn-sig-clear"
            onPress={() => ref.current?.clearSignature()}
            style={styles.sigClearBtn}
          >
            <Ionicons name="refresh" size={16} color={theme.colors.brandPrimary} />
            <Text style={styles.sigClearText}>Clear</Text>
          </Pressable>
          <Text style={styles.sigHint}>Draw with your finger. Tap Save when done.</Text>
        </View>
      </View>
    </Modal>
  );
}

// The signature canvas is a WebView under the hood; keep it visually clean.
const SIG_WEB_STYLE = `
  .m-signature-pad--footer { display: none; margin: 0; }
  .m-signature-pad { box-shadow: none; border: none; }
  body,html { margin:0; padding:0; background:#fff; }
`;

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

  // ----- Phase 3 additions --------------------------------------------------
  headerIconBtn: {
    width: 36,
    height: 36,
    borderRadius: 18,
    backgroundColor: "rgba(255,255,255,0.16)",
    alignItems: "center",
    justifyContent: "center",
  },
  inputError: {
    borderColor: theme.colors.danger,
    borderWidth: 1.5,
  },
  errorInline: {
    color: theme.colors.danger,
    fontSize: 11,
    fontWeight: "600",
    marginTop: 2,
  },
  draftBanner: {
    flexDirection: "row",
    alignItems: "center",
    gap: 8,
    padding: 10,
    borderRadius: 10,
    backgroundColor: theme.colors.brandTertiary,
    borderWidth: 1,
    borderColor: theme.colors.brandPrimary,
  },
  draftBannerText: {
    flex: 1,
    fontSize: 12,
    fontWeight: "600",
    color: theme.colors.brandPrimary,
  },
  draftBannerAction: {
    fontSize: 12,
    fontWeight: "800",
    color: theme.colors.danger,
    textDecorationLine: "underline",
  },
  errorBanner: {
    flexDirection: "row",
    alignItems: "center",
    gap: 8,
    padding: 10,
    borderRadius: 10,
    backgroundColor: "#fdecec",
    borderWidth: 1,
    borderColor: theme.colors.danger,
  },
  errorBannerText: {
    color: theme.colors.danger,
    fontWeight: "700",
    fontSize: 12,
  },
  signaturePlaceholderFilled: {
    borderStyle: "solid",
    backgroundColor: theme.colors.surface,
    borderColor: theme.colors.success,
  },
  signaturePreviewRow: {
    flex: 1,
    flexDirection: "row",
    alignItems: "center",
    gap: 10,
  },
  signatureCapturedLabel: {
    fontSize: 13,
    fontWeight: "700",
    color: theme.colors.onSurface,
  },
  signatureCapturedHint: {
    fontSize: 11,
    color: theme.colors.muted,
    marginTop: 2,
  },
  // Signature capture sub-modal
  sigRoot: { flex: 1, backgroundColor: "#000" },
  sigHeader: {
    flexDirection: "row",
    alignItems: "center",
    gap: 12,
    backgroundColor: theme.colors.brandPrimary,
    paddingHorizontal: 14,
    paddingVertical: 12,
  },
  sigTitle: {
    flex: 1,
    color: "#fff",
    fontWeight: "700",
    fontSize: 14,
  },
  sigSaveBtn: {
    backgroundColor: "#fff",
    paddingHorizontal: 14,
    paddingVertical: 8,
    borderRadius: 999,
    minWidth: 76,
    alignItems: "center",
  },
  sigSaveText: { color: theme.colors.brandPrimary, fontWeight: "700", fontSize: 13 },
  sigCanvasWrap: { flex: 1, backgroundColor: "#fff" },
  sigFooter: {
    flexDirection: "row",
    alignItems: "center",
    justifyContent: "space-between",
    padding: 12,
    backgroundColor: "#f6f7fb",
    borderTopWidth: 1,
    borderTopColor: theme.colors.border,
  },
  sigClearBtn: {
    flexDirection: "row",
    alignItems: "center",
    gap: 6,
    backgroundColor: "#fff",
    paddingHorizontal: 12,
    paddingVertical: 8,
    borderRadius: 999,
    borderWidth: 1,
    borderColor: theme.colors.brandPrimary,
  },
  sigClearText: { color: theme.colors.brandPrimary, fontWeight: "700", fontSize: 12 },
  sigHint: { fontSize: 11, color: theme.colors.muted, fontStyle: "italic" },
});
