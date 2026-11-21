import { View, Text, TextInput, Pressable, StyleSheet } from "react-native";
import { Ionicons } from "@expo/vector-icons";
import { theme } from "@/src/theme";
import { IntakeField, IntakeSchema, sectionVisible } from "./intakeTypes";

type Props = {
  schema: IntakeSchema;
  values: Record<string, any>;
  onChange: (key: string, value: any) => void;
};

export default function IntakeFormFields({ schema, values, onChange }: Props) {
  const toggle = (k: string, opt: string) => {
    const arr: string[] = Array.isArray(values[k]) ? values[k] : [];
    onChange(k, arr.includes(opt) ? arr.filter((x) => x !== opt) : [...arr, opt]);
  };

  const renderField = (f: IntakeField) => {
    const label = `${f.label}${f.required ? " *" : ""}`;
    if (f.type === "consent") {
      const on = values[f.key] === true;
      return (
        <Pressable key={f.key} testID={`field-${f.key}`} onPress={() => onChange(f.key, !on)}
                   style={[s.checkRow, on && s.checkRowOn]}>
          <View style={[s.checkbox, on && s.checkboxOn]}>
            {on && <Ionicons name="checkmark" size={14} color="#fff" />}
          </View>
          <Text style={[s.optText, { flex: 1 }]}>{label}</Text>
        </Pressable>
      );
    }
    if (f.type === "select" || f.type === "radio") {
      return (
        <View key={f.key} style={s.group}>
          <Text style={s.label}>{label}</Text>
          <View style={s.chipsRow}>
            {(f.options || []).map((o) => {
              const on = values[f.key] === o;
              return (
                <Pressable key={o} onPress={() => onChange(f.key, on ? "" : o)} style={[s.chip, on && s.chipOn]}>
                  <Text style={[s.chipText, on && s.chipTextOn]}>{o}</Text>
                </Pressable>
              );
            })}
          </View>
        </View>
      );
    }
    if (f.type === "checkbox") {
      const arr: string[] = Array.isArray(values[f.key]) ? values[f.key] : [];
      return (
        <View key={f.key} style={s.group}>
          <Text style={s.label}>{label}</Text>
          {(f.options || []).map((o) => {
            const on = arr.includes(o);
            return (
              <Pressable key={o} onPress={() => toggle(f.key, o)} style={[s.checkRow, on && s.checkRowOn]}>
                <View style={[s.checkbox, on && s.checkboxOn]}>
                  {on && <Ionicons name="checkmark" size={14} color="#fff" />}
                </View>
                <Text style={[s.optText, { flex: 1 }, on && { fontWeight: "700" }]}>{o}</Text>
              </Pressable>
            );
          })}
        </View>
      );
    }
    const multi = f.type === "longtext";
    return (
      <View key={f.key} style={s.group}>
        <Text style={s.label}>{label}</Text>
        <TextInput
          testID={`field-${f.key}`}
          value={values[f.key] || ""}
          onChangeText={(v) => onChange(f.key, v)}
          placeholder={f.type === "date" ? "YYYY-MM-DD" : ""}
          placeholderTextColor={theme.colors.muted}
          multiline={multi}
          style={[s.input, multi && { minHeight: 72, textAlignVertical: "top" }]}
        />
      </View>
    );
  };

  return (
    <>
      {schema.sections.filter((sec) => sectionVisible(sec, values)).map((sec) => (
        <View key={sec.title} style={{ gap: 10 }}>
          <Text style={s.sectionTitle}>{sec.title}</Text>
          {sec.fields.map(renderField)}
        </View>
      ))}
    </>
  );
}

const s = StyleSheet.create({
  group: { gap: 6 },
  sectionTitle: { fontSize: 13, fontWeight: "800", color: theme.colors.brandPrimary, textTransform: "uppercase", letterSpacing: 0.4, marginTop: 8 },
  label: { fontSize: 12, fontWeight: "700", color: theme.colors.onSurface },
  input: { backgroundColor: "#fff", borderRadius: 10, padding: 12, borderWidth: 1, borderColor: theme.colors.border, color: theme.colors.onSurface, fontSize: 14 },
  chipsRow: { flexDirection: "row", flexWrap: "wrap", gap: 6 },
  chip: { backgroundColor: "#fff", borderRadius: 999, paddingHorizontal: 12, paddingVertical: 8, borderWidth: 1, borderColor: theme.colors.border },
  chipOn: { backgroundColor: theme.colors.brandPrimary, borderColor: theme.colors.brandPrimary },
  chipText: { fontSize: 12, color: theme.colors.onSurface, fontWeight: "600" },
  chipTextOn: { color: "#fff" },
  optText: { fontSize: 13, color: theme.colors.onSurface },
  checkRow: { flexDirection: "row", alignItems: "center", gap: 10, padding: 10, borderRadius: 10, backgroundColor: "#fff", borderWidth: 1, borderColor: theme.colors.border },
  checkRowOn: { backgroundColor: theme.colors.brandTertiary, borderColor: theme.colors.brandPrimary },
  checkbox: { width: 22, height: 22, borderRadius: 11, borderWidth: 2, borderColor: theme.colors.borderStrong, alignItems: "center", justifyContent: "center" },
  checkboxOn: { backgroundColor: theme.colors.success, borderColor: theme.colors.success },
});
