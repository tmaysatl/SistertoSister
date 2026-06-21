import { useCallback, useEffect, useMemo, useState } from "react";
import {
  View, Text, StyleSheet, ScrollView, Pressable, TextInput, Modal,
  ActivityIndicator, RefreshControl, KeyboardAvoidingView, Platform, Alert,
} from "react-native";
import { Ionicons } from "@expo/vector-icons";
import { SafeAreaView } from "react-native-safe-area-context";
import { useFocusEffect } from "expo-router";
import { useAuth } from "@/src/context/AuthContext";
import { apiGet, apiPost, apiPut, apiDelete } from "@/src/api/client";
import { theme } from "@/src/theme";

type Shift = {
  id: string;
  caregiver_id: string;
  client_id: string;
  kind: "recurring" | "one_off";
  date?: string | null;
  weekdays?: string[] | null;
  recurring_until?: string | null;
  start_time: string;
  end_time: string;
  notes?: string;
  service_type?: string;
  status: "scheduled" | "in_progress" | "completed" | "cancelled";
  clocked_in_at?: string | null;
  clocked_out_at?: string | null;
};

type Person = { id: string; name: string };

const SERVICE_TYPES = ["Personal Care", "Companion", "Skilled Nursing", "Respite", "Homemaker"];
const WEEKDAYS = ["MON", "TUE", "WED", "THU", "FRI", "SAT", "SUN"];

function toISO(d: Date) {
  const y = d.getFullYear();
  const m = String(d.getMonth() + 1).padStart(2, "0");
  const dd = String(d.getDate()).padStart(2, "0");
  return `${y}-${m}-${dd}`;
}
function addDays(d: Date, n: number) {
  const c = new Date(d);
  c.setDate(c.getDate() + n);
  return c;
}
function startOfWeek(d: Date) {
  const c = new Date(d);
  const day = (c.getDay() + 6) % 7; // Mon=0
  c.setDate(c.getDate() - day);
  c.setHours(0, 0, 0, 0);
  return c;
}
function fmtDay(d: Date) {
  return d.toLocaleDateString(undefined, { weekday: "short", month: "short", day: "numeric" });
}
function fmtFull(d: Date) {
  return d.toLocaleDateString(undefined, { weekday: "long", month: "long", day: "numeric", year: "numeric" });
}

export default function ScheduleScreen() {
  const { user } = useAuth();
  const isAdmin = user?.role === "admin";

  const [view, setView] = useState<"day" | "week">("day");
  const [anchor, setAnchor] = useState<Date>(() => { const d = new Date(); d.setHours(0,0,0,0); return d; });
  const [shifts, setShifts] = useState<Shift[]>([]);
  const [caregivers, setCaregivers] = useState<Person[]>([]);
  const [clients, setClients] = useState<Person[]>([]);
  const [refreshing, setRefreshing] = useState(false);
  const [loading, setLoading] = useState(true);

  // Editor modal
  const [editor, setEditor] = useState<Partial<Shift> | null>(null);
  const [busy, setBusy] = useState(false);

  const range = useMemo(() => {
    if (view === "day") {
      const s = toISO(anchor); return { start: s, end: s };
    }
    const sow = startOfWeek(anchor);
    return { start: toISO(sow), end: toISO(addDays(sow, 6)) };
  }, [view, anchor]);

  const load = useCallback(async () => {
    try {
      const q = new URLSearchParams({ start: range.start, end: range.end }).toString();
      const [sh, cg, cl] = await Promise.all([
        apiGet<Shift[]>(`/shifts?${q}`),
        isAdmin ? apiGet<any[]>(`/caregivers`) : Promise.resolve([]),
        apiGet<any[]>(`/clients`),
      ]);
      setShifts(sh || []);
      setCaregivers((cg || []).map((c: any) => ({ id: c.id, name: c.name || c.full_name || c.email })));
      setClients((cl || []).map((c: any) => ({ id: c.id, name: c.name || c.full_name })));
    } catch (e) {
      console.log("schedule load err", e);
    } finally {
      setLoading(false); setRefreshing(false);
    }
  }, [range.start, range.end, isAdmin]);

  useFocusEffect(useCallback(() => { load(); }, [load]));
  useEffect(() => { load(); }, [load]);

  const caregiverName = (id: string) => caregivers.find((c) => c.id === id)?.name || (user?.id === id ? (user as any).name || "You" : "Caregiver");
  const clientName = (id: string) => clients.find((c) => c.id === id)?.name || "Client";

  const openCreate = (date?: Date) => {
    setEditor({
      kind: "one_off",
      date: toISO(date || anchor),
      start_time: "09:00",
      end_time: "13:00",
      service_type: "Personal Care",
      notes: "",
      caregiver_id: !isAdmin ? user?.id : undefined,
      weekdays: [],
    });
  };

  const openEdit = (sh: Shift) => setEditor({ ...sh });

  const saveShift = async () => {
    if (!editor) return;
    if (!editor.client_id) { Alert.alert("Please select a client"); return; }
    if (!editor.caregiver_id) { Alert.alert("Please select a caregiver"); return; }
    if (!editor.start_time || !editor.end_time) { Alert.alert("Start and end times are required"); return; }
    if (editor.kind === "one_off" && !editor.date) { Alert.alert("Pick a date"); return; }
    if (editor.kind === "recurring") {
      if (!editor.weekdays || editor.weekdays.length === 0) { Alert.alert("Pick at least one weekday"); return; }
      if (!editor.recurring_until) { Alert.alert("Pick an end date"); return; }
      if (!editor.date) editor.date = toISO(new Date());
    }
    setBusy(true);
    try {
      if (editor.id) {
        await apiPut(`/shifts/${editor.id}`, {
          client_id: editor.client_id,
          caregiver_id: editor.caregiver_id,
          date: editor.date,
          start_time: editor.start_time,
          end_time: editor.end_time,
          notes: editor.notes,
          service_type: editor.service_type,
        });
      } else {
        await apiPost(`/shifts`, {
          caregiver_id: editor.caregiver_id,
          client_id: editor.client_id,
          kind: editor.kind || "one_off",
          date: editor.date,
          start_time: editor.start_time,
          end_time: editor.end_time,
          notes: editor.notes,
          service_type: editor.service_type,
          weekdays: editor.kind === "recurring" ? editor.weekdays : undefined,
          recurring_until: editor.kind === "recurring" ? editor.recurring_until : undefined,
        });
      }
      setEditor(null);
      await load();
    } catch (e: any) {
      Alert.alert("Could not save shift", e?.message || "Try again.");
    } finally {
      setBusy(false);
    }
  };

  const cancelShift = (sh: Shift) => {
    if (Platform.OS === "web") {
      if (!(globalThis as any).confirm?.(`Cancel shift on ${sh.date}?`)) return;
      apiDelete(`/shifts/${sh.id}`).then(load).catch(() => {});
      setEditor(null);
      return;
    }
    Alert.alert("Cancel shift?", `${sh.date} ${sh.start_time}-${sh.end_time}`, [
      { text: "Keep", style: "cancel" },
      { text: "Cancel shift", style: "destructive", onPress: async () => {
        await apiDelete(`/shifts/${sh.id}`);
        setEditor(null);
        await load();
      } },
    ]);
  };

  const clockIn = async (sh: Shift) => {
    await apiPost(`/shifts/${sh.id}/clock-in`, {});
    await load();
  };
  const clockOut = async (sh: Shift) => {
    await apiPost(`/shifts/${sh.id}/clock-out`, {});
    await load();
  };

  const onRefresh = () => { setRefreshing(true); load(); };

  // Day cells for week view
  const weekDays = useMemo(() => {
    const sow = startOfWeek(anchor);
    return Array.from({ length: 7 }, (_, i) => addDays(sow, i));
  }, [anchor]);

  const shiftsByDay = useMemo(() => {
    const m: Record<string, Shift[]> = {};
    for (const s of shifts) {
      const k = s.date || "";
      (m[k] ||= []).push(s);
    }
    Object.values(m).forEach((arr) => arr.sort((a, b) => a.start_time.localeCompare(b.start_time)));
    return m;
  }, [shifts]);

  if (loading) {
    return (
      <SafeAreaView style={styles.safe} edges={["top"]}>
        <View style={styles.loadingWrap}><ActivityIndicator color={theme.colors.brandPrimary} /></View>
      </SafeAreaView>
    );
  }

  return (
    <SafeAreaView style={styles.safe} edges={["top"]}>
      {/* Header */}
      <View style={styles.header}>
        <View>
          <Text style={styles.title}>Schedule</Text>
          <Text style={styles.subtitle}>{view === "day" ? fmtFull(anchor) : `${fmtDay(weekDays[0])} – ${fmtDay(weekDays[6])}`}</Text>
        </View>
        <Pressable testID="add-shift-button" onPress={() => openCreate(anchor)} style={styles.addBtn}>
          <Ionicons name="add" size={18} color="#fff" />
          <Text style={styles.addBtnText}>New shift</Text>
        </Pressable>
      </View>

      {/* View toggle + date nav */}
      <View style={styles.toolbar}>
        <View style={styles.segment}>
          {(["day", "week"] as const).map((v) => (
            <Pressable key={v} testID={`view-${v}`} onPress={() => setView(v)} style={[styles.segBtn, view === v && styles.segBtnOn]}>
              <Text style={[styles.segBtnText, view === v && styles.segBtnTextOn]}>{v === "day" ? "Day" : "Week"}</Text>
            </Pressable>
          ))}
        </View>
        <View style={styles.navRow}>
          <Pressable testID="nav-prev" onPress={() => setAnchor(addDays(anchor, view === "day" ? -1 : -7))} style={styles.navBtn}>
            <Ionicons name="chevron-back" size={18} color={theme.colors.brandPrimary} />
          </Pressable>
          <Pressable testID="nav-today" onPress={() => { const d = new Date(); d.setHours(0,0,0,0); setAnchor(d); }} style={styles.todayBtn}>
            <Text style={styles.todayText}>Today</Text>
          </Pressable>
          <Pressable testID="nav-next" onPress={() => setAnchor(addDays(anchor, view === "day" ? 1 : 7))} style={styles.navBtn}>
            <Ionicons name="chevron-forward" size={18} color={theme.colors.brandPrimary} />
          </Pressable>
        </View>
      </View>

      <ScrollView
        style={{ flex: 1 }}
        contentContainerStyle={{ padding: 16, paddingBottom: 100 }}
        refreshControl={<RefreshControl refreshing={refreshing} onRefresh={onRefresh} tintColor={theme.colors.brandPrimary} />}
      >
        {view === "day" && (
          <DayList
            day={anchor}
            shifts={shiftsByDay[toISO(anchor)] || []}
            onEdit={openEdit}
            onClockIn={clockIn}
            onClockOut={clockOut}
            caregiverName={caregiverName}
            clientName={clientName}
            currentUserId={user?.id}
            isAdmin={isAdmin}
          />
        )}
        {view === "week" && weekDays.map((d) => (
          <View key={toISO(d)} style={{ marginBottom: 16 }}>
            <Pressable onPress={() => { setAnchor(d); setView("day"); }} style={styles.weekHead}>
              <Text style={styles.weekHeadText}>{fmtDay(d)}</Text>
              <View style={styles.dotBadge}>
                <Text style={styles.dotBadgeText}>{(shiftsByDay[toISO(d)] || []).length}</Text>
              </View>
            </Pressable>
            <DayList
              day={d}
              shifts={shiftsByDay[toISO(d)] || []}
              onEdit={openEdit}
              onClockIn={clockIn}
              onClockOut={clockOut}
              caregiverName={caregiverName}
              clientName={clientName}
              currentUserId={user?.id}
              isAdmin={isAdmin}
              compact
            />
          </View>
        ))}
      </ScrollView>

      {/* Editor sheet */}
      <Modal visible={!!editor} transparent animationType="slide" onRequestClose={() => setEditor(null)}>
        <KeyboardAvoidingView behavior={Platform.OS === "ios" ? "padding" : undefined} style={styles.modalRoot}>
          <Pressable style={styles.backdrop} onPress={() => setEditor(null)} />
          <View style={styles.sheet}>
            <View style={styles.sheetHandle} />
            <ScrollView showsVerticalScrollIndicator={false} contentContainerStyle={{ paddingBottom: 16, gap: 12 }}>
              <Text style={styles.sheetTitle}>{editor?.id ? "Edit shift" : "New shift"}</Text>

              {/* Kind toggle (only for new shifts) */}
              {!editor?.id && (
                <View style={styles.segment}>
                  {(["one_off", "recurring"] as const).map((k) => (
                    <Pressable key={k} testID={`kind-${k}`} onPress={() => setEditor((e) => ({ ...(e || {}), kind: k }))} style={[styles.segBtn, editor?.kind === k && styles.segBtnOn]}>
                      <Text style={[styles.segBtnText, editor?.kind === k && styles.segBtnTextOn]}>{k === "one_off" ? "One-off" : "Recurring"}</Text>
                    </Pressable>
                  ))}
                </View>
              )}

              {/* Caregiver */}
              {isAdmin && (
                <View>
                  <Text style={styles.fieldLabel}>Caregiver</Text>
                  <View style={styles.chipsRow}>
                    {caregivers.map((c) => (
                      <Pressable key={c.id} testID={`pick-cg-${c.id}`} onPress={() => setEditor((e) => ({ ...(e || {}), caregiver_id: c.id }))} style={[styles.chip, editor?.caregiver_id === c.id && styles.chipOn]}>
                        <Text style={[styles.chipText, editor?.caregiver_id === c.id && styles.chipTextOn]} numberOfLines={1}>{c.name}</Text>
                      </Pressable>
                    ))}
                  </View>
                </View>
              )}

              {/* Client */}
              <View>
                <Text style={styles.fieldLabel}>Client</Text>
                <View style={styles.chipsRow}>
                  {clients.map((c) => (
                    <Pressable key={c.id} testID={`pick-cl-${c.id}`} onPress={() => setEditor((e) => ({ ...(e || {}), client_id: c.id }))} style={[styles.chip, editor?.client_id === c.id && styles.chipOn]}>
                      <Text style={[styles.chipText, editor?.client_id === c.id && styles.chipTextOn]} numberOfLines={1}>{c.name}</Text>
                    </Pressable>
                  ))}
                </View>
              </View>

              {/* Date / weekdays */}
              {editor?.kind === "recurring" ? (
                <>
                  <View>
                    <Text style={styles.fieldLabel}>Weekdays</Text>
                    <View style={styles.chipsRow}>
                      {WEEKDAYS.map((w) => {
                        const on = (editor?.weekdays || []).includes(w);
                        return (
                          <Pressable key={w} testID={`wd-${w}`} onPress={() => setEditor((e) => {
                            const cur = (e?.weekdays || []) as string[];
                            const next = on ? cur.filter((x) => x !== w) : [...cur, w];
                            return { ...(e || {}), weekdays: next };
                          })} style={[styles.chip, on && styles.chipOn]}>
                            <Text style={[styles.chipText, on && styles.chipTextOn]}>{w}</Text>
                          </Pressable>
                        );
                      })}
                    </View>
                  </View>
                  <View style={{ flexDirection: "row", gap: 8 }}>
                    <View style={{ flex: 1 }}>
                      <Text style={styles.fieldLabel}>Start date</Text>
                      <TextInput value={editor?.date || ""} onChangeText={(v) => setEditor((e) => ({ ...(e || {}), date: v }))} placeholder="YYYY-MM-DD" placeholderTextColor={theme.colors.muted} style={styles.input} testID="recurring-from" />
                    </View>
                    <View style={{ flex: 1 }}>
                      <Text style={styles.fieldLabel}>Until</Text>
                      <TextInput value={editor?.recurring_until || ""} onChangeText={(v) => setEditor((e) => ({ ...(e || {}), recurring_until: v }))} placeholder="YYYY-MM-DD" placeholderTextColor={theme.colors.muted} style={styles.input} testID="recurring-until" />
                    </View>
                  </View>
                </>
              ) : (
                <View>
                  <Text style={styles.fieldLabel}>Date</Text>
                  <TextInput value={editor?.date || ""} onChangeText={(v) => setEditor((e) => ({ ...(e || {}), date: v }))} placeholder="YYYY-MM-DD" placeholderTextColor={theme.colors.muted} style={styles.input} testID="shift-date" />
                </View>
              )}

              {/* Times */}
              <View style={{ flexDirection: "row", gap: 8 }}>
                <View style={{ flex: 1 }}>
                  <Text style={styles.fieldLabel}>Start time</Text>
                  <TextInput value={editor?.start_time || ""} onChangeText={(v) => setEditor((e) => ({ ...(e || {}), start_time: v }))} placeholder="09:00" placeholderTextColor={theme.colors.muted} style={styles.input} testID="shift-start" />
                </View>
                <View style={{ flex: 1 }}>
                  <Text style={styles.fieldLabel}>End time</Text>
                  <TextInput value={editor?.end_time || ""} onChangeText={(v) => setEditor((e) => ({ ...(e || {}), end_time: v }))} placeholder="13:00" placeholderTextColor={theme.colors.muted} style={styles.input} testID="shift-end" />
                </View>
              </View>

              {/* Service type */}
              <View>
                <Text style={styles.fieldLabel}>Service type</Text>
                <View style={styles.chipsRow}>
                  {SERVICE_TYPES.map((s) => (
                    <Pressable key={s} testID={`svc-${s}`} onPress={() => setEditor((e) => ({ ...(e || {}), service_type: s }))} style={[styles.chip, editor?.service_type === s && styles.chipOn]}>
                      <Text style={[styles.chipText, editor?.service_type === s && styles.chipTextOn]}>{s}</Text>
                    </Pressable>
                  ))}
                </View>
              </View>

              {/* Notes */}
              <View>
                <Text style={styles.fieldLabel}>Notes / instructions</Text>
                <TextInput value={editor?.notes || ""} onChangeText={(v) => setEditor((e) => ({ ...(e || {}), notes: v }))} placeholder="e.g. Use side entrance, lockbox 1234…" placeholderTextColor={theme.colors.muted} style={[styles.input, { minHeight: 64 }]} multiline testID="shift-notes" />
              </View>

              {/* Actions */}
              <View style={{ flexDirection: "row", gap: 8 }}>
                <Pressable onPress={() => setEditor(null)} style={[styles.secondaryBtn, { flex: 1 }]}><Text style={styles.secondaryBtnText}>Close</Text></Pressable>
                {editor?.id && isAdmin && (
                  <Pressable testID="delete-shift" onPress={() => cancelShift(editor as Shift)} style={[styles.dangerBtn, { flex: 1 }]}>
                    <Text style={styles.dangerBtnText}>Cancel shift</Text>
                  </Pressable>
                )}
                <Pressable testID="save-shift" disabled={busy} onPress={saveShift} style={[styles.primaryBtn, { flex: 1, opacity: busy ? 0.6 : 1 }]}>
                  {busy ? <ActivityIndicator color="#fff" /> : <Text style={styles.primaryBtnText}>{editor?.id ? "Save" : "Schedule"}</Text>}
                </Pressable>
              </View>
            </ScrollView>
          </View>
        </KeyboardAvoidingView>
      </Modal>
    </SafeAreaView>
  );
}

function DayList({ day, shifts, onEdit, onClockIn, onClockOut, caregiverName, clientName, currentUserId, isAdmin, compact }: any) {
  if (shifts.length === 0) {
    return (
      <View style={styles.emptyCard}>
        <Ionicons name="calendar-clear-outline" size={22} color={theme.colors.muted} />
        <Text style={styles.emptyText}>No shifts scheduled.</Text>
      </View>
    );
  }
  return (
    <View style={{ gap: 8 }}>
      {shifts.map((s: Shift) => {
        const mine = s.caregiver_id === currentUserId;
        const canClockIn = !s.clocked_in_at && mine;
        const canClockOut = !!s.clocked_in_at && !s.clocked_out_at && mine;
        const statusColor =
          s.status === "completed" ? theme.colors.success :
          s.status === "in_progress" ? theme.colors.brandPrimary :
          s.status === "cancelled" ? "#888" :
          theme.colors.brandSecondary;
        return (
          <Pressable key={s.id} testID={`shift-${s.id}`} onPress={() => onEdit(s)} style={[styles.shiftCard, compact && { padding: 12 }]}>
            <View style={{ flex: 1, gap: 4 }}>
              <View style={{ flexDirection: "row", alignItems: "center", gap: 8 }}>
                <Text style={styles.shiftTime}>{s.start_time}–{s.end_time}</Text>
                <View style={[styles.statusDot, { backgroundColor: statusColor }]} />
                <Text style={[styles.statusLabel, { color: statusColor }]}>{s.status.replace("_", " ")}</Text>
              </View>
              <Text style={styles.shiftMain}>{clientName(s.client_id)} · {caregiverName(s.caregiver_id)}</Text>
              {!!s.service_type && <Text style={styles.shiftSub}>{s.service_type}</Text>}
              {!!s.notes && <Text style={styles.shiftNotes} numberOfLines={2}>{s.notes}</Text>}
            </View>
            <View style={{ alignItems: "flex-end", gap: 6 }}>
              {canClockIn && (
                <Pressable testID={`clock-in-${s.id}`} onPress={(e) => { e.stopPropagation(); onClockIn(s); }} style={styles.clockInBtn}>
                  <Ionicons name="play" size={12} color="#fff" />
                  <Text style={styles.clockBtnText}>Clock in</Text>
                </Pressable>
              )}
              {canClockOut && (
                <Pressable testID={`clock-out-${s.id}`} onPress={(e) => { e.stopPropagation(); onClockOut(s); }} style={styles.clockOutBtn}>
                  <Ionicons name="stop" size={12} color="#fff" />
                  <Text style={styles.clockBtnText}>Clock out</Text>
                </Pressable>
              )}
              {isAdmin && <Ionicons name="create-outline" size={16} color={theme.colors.muted} />}
            </View>
          </Pressable>
        );
      })}
    </View>
  );
}

const styles = StyleSheet.create({
  safe: { flex: 1, backgroundColor: theme.colors.background },
  loadingWrap: { flex: 1, alignItems: "center", justifyContent: "center" },
  header: { paddingHorizontal: 16, paddingTop: 12, paddingBottom: 8, flexDirection: "row", alignItems: "center", justifyContent: "space-between" },
  title: { fontSize: 26, fontWeight: "800", color: theme.colors.onSurface, letterSpacing: -0.4 },
  subtitle: { fontSize: 13, color: theme.colors.muted, marginTop: 2 },
  addBtn: { flexDirection: "row", alignItems: "center", gap: 6, backgroundColor: theme.colors.brandPrimary, paddingHorizontal: 14, paddingVertical: 10, borderRadius: 999 },
  addBtnText: { color: "#fff", fontWeight: "700", fontSize: 13 },
  toolbar: { paddingHorizontal: 16, paddingVertical: 8, flexDirection: "row", justifyContent: "space-between", alignItems: "center" },
  segment: { flexDirection: "row", backgroundColor: theme.colors.surfaceSecondary, borderRadius: 10, padding: 3 },
  segBtn: { paddingHorizontal: 14, paddingVertical: 6, borderRadius: 8 },
  segBtnOn: { backgroundColor: theme.colors.brandPrimary },
  segBtnText: { color: theme.colors.muted, fontWeight: "700", fontSize: 12 },
  segBtnTextOn: { color: "#fff" },
  navRow: { flexDirection: "row", alignItems: "center", gap: 6 },
  navBtn: { width: 32, height: 32, borderRadius: 8, backgroundColor: theme.colors.brandTertiary, alignItems: "center", justifyContent: "center" },
  todayBtn: { paddingHorizontal: 10, height: 32, borderRadius: 8, alignItems: "center", justifyContent: "center", backgroundColor: theme.colors.brandTertiary },
  todayText: { color: theme.colors.brandPrimary, fontWeight: "700", fontSize: 12 },
  weekHead: { flexDirection: "row", alignItems: "center", justifyContent: "space-between", paddingBottom: 6 },
  weekHeadText: { fontSize: 13, fontWeight: "700", color: theme.colors.onSurface },
  dotBadge: { backgroundColor: theme.colors.brandPrimary, paddingHorizontal: 8, height: 18, borderRadius: 9, alignItems: "center", justifyContent: "center" },
  dotBadgeText: { color: "#fff", fontSize: 11, fontWeight: "700" },
  emptyCard: { padding: 18, alignItems: "center", flexDirection: "row", gap: 8, backgroundColor: theme.colors.surfaceSecondary, borderRadius: 12, justifyContent: "center" },
  emptyText: { color: theme.colors.muted, fontSize: 13 },
  shiftCard: { padding: 14, backgroundColor: theme.colors.surface, borderRadius: 14, borderWidth: 1, borderColor: theme.colors.border, flexDirection: "row", alignItems: "center", gap: 12 },
  shiftTime: { fontSize: 14, fontWeight: "700", color: theme.colors.onSurface },
  shiftMain: { fontSize: 13, fontWeight: "600", color: theme.colors.onSurface },
  shiftSub: { fontSize: 12, color: theme.colors.brandPrimary, fontWeight: "600" },
  shiftNotes: { fontSize: 12, color: theme.colors.muted, marginTop: 2 },
  statusDot: { width: 8, height: 8, borderRadius: 4 },
  statusLabel: { fontSize: 10, fontWeight: "700", textTransform: "uppercase", letterSpacing: 0.4 },
  clockInBtn: { flexDirection: "row", alignItems: "center", gap: 4, backgroundColor: theme.colors.success, paddingHorizontal: 10, paddingVertical: 6, borderRadius: 999 },
  clockOutBtn: { flexDirection: "row", alignItems: "center", gap: 4, backgroundColor: theme.colors.error, paddingHorizontal: 10, paddingVertical: 6, borderRadius: 999 },
  clockBtnText: { color: "#fff", fontWeight: "700", fontSize: 11 },
  modalRoot: { flex: 1, justifyContent: "flex-end" },
  backdrop: { ...StyleSheet.absoluteFillObject, backgroundColor: "rgba(0,0,0,0.4)" },
  sheet: { backgroundColor: theme.colors.surface, borderTopLeftRadius: 24, borderTopRightRadius: 24, padding: 16, maxHeight: "92%" },
  sheetHandle: { width: 40, height: 4, borderRadius: 2, backgroundColor: theme.colors.border, alignSelf: "center", marginBottom: 8 },
  sheetTitle: { fontSize: 18, fontWeight: "700", color: theme.colors.onSurface, marginBottom: 4 },
  fieldLabel: { fontSize: 12, fontWeight: "700", color: theme.colors.muted, textTransform: "uppercase", letterSpacing: 0.4, marginBottom: 6 },
  input: { backgroundColor: theme.colors.surfaceSecondary, borderRadius: 10, padding: 12, color: theme.colors.onSurface, borderWidth: 1, borderColor: theme.colors.border },
  chipsRow: { flexDirection: "row", flexWrap: "wrap", gap: 6 },
  chip: { backgroundColor: theme.colors.surfaceSecondary, borderRadius: 999, paddingHorizontal: 12, paddingVertical: 7, borderWidth: 1, borderColor: theme.colors.border, maxWidth: 220 },
  chipOn: { backgroundColor: theme.colors.brandPrimary, borderColor: theme.colors.brandPrimary },
  chipText: { fontSize: 12, fontWeight: "600", color: theme.colors.onSurface },
  chipTextOn: { color: "#fff" },
  primaryBtn: { backgroundColor: theme.colors.brandPrimary, padding: 14, borderRadius: 12, alignItems: "center" },
  primaryBtnText: { color: "#fff", fontWeight: "700", fontSize: 14 },
  secondaryBtn: { backgroundColor: theme.colors.surfaceSecondary, padding: 14, borderRadius: 12, alignItems: "center", borderWidth: 1, borderColor: theme.colors.border },
  secondaryBtnText: { color: theme.colors.onSurface, fontWeight: "700", fontSize: 14 },
  dangerBtn: { backgroundColor: theme.colors.danger, padding: 14, borderRadius: 12, alignItems: "center" },
  dangerBtnText: { color: "#fff", fontWeight: "700", fontSize: 14 },
});
