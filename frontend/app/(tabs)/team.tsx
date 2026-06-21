import { useCallback, useState } from "react";
import {
  View, Text, StyleSheet, ScrollView, Pressable, TextInput, Modal,
  KeyboardAvoidingView, Platform, RefreshControl, FlatList,
} from "react-native";
import { Ionicons } from "@expo/vector-icons";
import { SafeAreaView } from "react-native-safe-area-context";
import { useFocusEffect, useRouter } from "expo-router";
import { useAuth } from "@/src/context/AuthContext";
import { apiGet, apiPost, apiDelete } from "@/src/api/client";
import { theme } from "@/src/theme";

type Client = { id: string; name: string; address?: string; phone?: string };
type Caregiver = { id: string; name: string; email: string };
type Assignment = { id: string; caregiver_id: string; client_id: string; schedule?: string; notes?: string };
type Step = { id: string; caregiver_id: string; title: string; description?: string; completed: boolean };

type Tab = "clients" | "caregivers" | "onboarding";

export default function Team() {
  const { user } = useAuth();
  const router = useRouter();
  const isAdmin = user?.role === "admin";
  const [tab, setTab] = useState<Tab>(isAdmin ? "clients" : "onboarding");
  const [clients, setClients] = useState<Client[]>([]);
  const [caregivers, setCaregivers] = useState<Caregiver[]>([]);
  const [assignments, setAssignments] = useState<Assignment[]>([]);
  const [steps, setSteps] = useState<Step[]>([]);
  const [loading, setLoading] = useState(false);

  // modals
  const [showClient, setShowClient] = useState(false);
  const [showAssign, setShowAssign] = useState(false);
  const [showStep, setShowStep] = useState(false);

  const [cName, setCName] = useState("");
  const [cAddress, setCAddress] = useState("");
  const [cPhone, setCPhone] = useState("");

  const [aCaregiver, setACaregiver] = useState<string>("");
  const [aClient, setAClient] = useState<string>("");
  const [aSchedule, setASchedule] = useState("");

  const [sCaregiver, setSCaregiver] = useState<string>("");
  const [sTitle, setSTitle] = useState("");
  const [sDesc, setSDesc] = useState("");

  const load = useCallback(async () => {
    setLoading(true);
    try {
      const [cl, cg, as, st] = await Promise.all([
        apiGet<Client[]>("/clients"),
        isAdmin ? apiGet<Caregiver[]>("/caregivers") : Promise.resolve([] as Caregiver[]),
        apiGet<Assignment[]>("/assignments"),
        apiGet<Step[]>("/onboarding"),
      ]);
      setClients(cl); setCaregivers(cg); setAssignments(as); setSteps(st);
    } catch (e) { console.log(e); }
    finally { setLoading(false); }
  }, [isAdmin]);

  useFocusEffect(useCallback(() => { load(); }, [load]));

  const addClient = async () => {
    if (!cName.trim()) return;
    await apiPost("/clients", { name: cName, address: cAddress, phone: cPhone });
    setCName(""); setCAddress(""); setCPhone(""); setShowClient(false);
    load();
  };

  const addAssignment = async () => {
    if (!aCaregiver || !aClient) return;
    await apiPost("/assignments", { caregiver_id: aCaregiver, client_id: aClient, schedule: aSchedule });
    setACaregiver(""); setAClient(""); setASchedule(""); setShowAssign(false);
    load();
  };

  const addStep = async () => {
    if (!sCaregiver || !sTitle.trim()) return;
    await apiPost("/onboarding", { caregiver_id: sCaregiver, title: sTitle, description: sDesc });
    setSCaregiver(""); setSTitle(""); setSDesc(""); setShowStep(false);
    load();
  };

  const toggleStep = async (id: string) => {
    await apiPost(`/onboarding/${id}/toggle`, {});
    load();
  };

  const tabs: { key: Tab; label: string }[] = isAdmin
    ? [
        { key: "clients", label: "Clients" },
        { key: "caregivers", label: "Caregivers" },
        { key: "onboarding", label: "Onboarding" },
      ]
    : [
        { key: "onboarding", label: "My Onboarding" },
        { key: "clients", label: "My Clients" },
      ];

  const myAssignedClients = clients.filter((c) =>
    assignments.some((a) => a.client_id === c.id)
  );

  return (
    <SafeAreaView edges={["top"]} style={styles.root}>
      <View style={styles.header}>
        <View>
          <Text style={styles.title}>Team</Text>
          <Text style={styles.subtitle}>
            {isAdmin ? "Manage clients, caregivers & assignments" : "Your assignments & onboarding"}
          </Text>
        </View>
        {isAdmin && (
          <Pressable
            testID="team-add-button"
            onPress={() =>
              tab === "clients" ? setShowClient(true) :
              tab === "caregivers" ? setShowAssign(true) :
              setShowStep(true)
            }
            style={styles.addBtn}
          >
            <Ionicons name="add" size={22} color="#fff" />
          </Pressable>
        )}
      </View>

      <View style={styles.tabsWrap}>
        <ScrollView horizontal showsHorizontalScrollIndicator={false} contentContainerStyle={styles.tabsRow}>
          {tabs.map((t) => (
            <Pressable
              key={t.key}
              testID={`team-tab-${t.key}`}
              onPress={() => setTab(t.key)}
              style={[styles.chip, tab === t.key && styles.chipActive]}
            >
              <Text style={[styles.chipText, tab === t.key && styles.chipTextActive]}>{t.label}</Text>
            </Pressable>
          ))}
        </ScrollView>
      </View>

      {tab === "clients" && (
        <FlatList
          data={isAdmin ? clients : myAssignedClients}
          keyExtractor={(i) => i.id}
          contentContainerStyle={styles.list}
          ItemSeparatorComponent={() => <View style={{ height: 10 }} />}
          refreshControl={<RefreshControl refreshing={loading} onRefresh={load} />}
          ListEmptyComponent={!loading ? <Empty text="No clients yet" /> : null}
          renderItem={({ item }) => (
            <Pressable
              testID={`client-${item.id}`}
              onPress={() => router.push(`/client/${item.id}`)}
              style={styles.row}
            >
              <View style={[styles.rowIcon, { backgroundColor: theme.colors.brandTertiary }]}>
                <Ionicons name="person-outline" size={18} color={theme.colors.brandPrimary} />
              </View>
              <View style={{ flex: 1 }}>
                <Text style={styles.rowTitle}>{item.name}</Text>
                {!!item.address && <Text style={styles.rowSub}>{item.address}</Text>}
              </View>
              {isAdmin && (
                <Pressable
                  testID={`bulk-assign-client-${item.id}`}
                  onPress={async (e) => {
                    e.stopPropagation();
                    await apiPost(`/clients/${item.id}/bulk-assign-onboarding`, {});
                    load();
                  }}
                  style={{ paddingHorizontal: 10, paddingVertical: 8, borderRadius: 10, backgroundColor: theme.colors.brandPrimary }}
                  hitSlop={6}
                >
                  <Text style={{ color: "#fff", fontSize: 11, fontWeight: "700" }}>Assign Packet</Text>
                </Pressable>
              )}
              {isAdmin && (
                <Pressable
                  testID={`delete-client-${item.id}`}
                  onPress={(e) => { e.stopPropagation(); apiDelete(`/clients/${item.id}`).then(load); }}
                  hitSlop={10}
                >
                  <Ionicons name="trash-outline" size={18} color={theme.colors.error} />
                </Pressable>
              )}
            </Pressable>
          )}
        />
      )}

      {tab === "caregivers" && isAdmin && (
        <FlatList
          data={caregivers}
          keyExtractor={(i) => i.id}
          contentContainerStyle={styles.list}
          ItemSeparatorComponent={() => <View style={{ height: 10 }} />}
          refreshControl={<RefreshControl refreshing={loading} onRefresh={load} />}
          ListEmptyComponent={!loading ? <Empty text="No caregivers yet" /> : null}
          renderItem={({ item }) => {
            const assigned = assignments.filter((a) => a.caregiver_id === item.id);
            const myStepCount = steps.filter((s) => s.caregiver_id === item.id).length;
            return (
              <Pressable
                testID={`caregiver-${item.id}`}
                onPress={() => router.push(`/caregiver/${item.id}`)}
                style={styles.row}
              >
                <View style={[styles.rowIcon, { backgroundColor: theme.colors.brandTertiary }]}>
                  <Ionicons name="medkit-outline" size={18} color={theme.colors.brandPrimary} />
                </View>
                <View style={{ flex: 1 }}>
                  <Text style={styles.rowTitle}>{item.name}</Text>
                  <Text style={styles.rowSub}>{item.email}</Text>
                  <Text style={[styles.rowSub, { marginTop: 4, fontWeight: "600" }]}>
                    {assigned.length} assignment{assigned.length === 1 ? "" : "s"} · {myStepCount} onboarding step{myStepCount === 1 ? "" : "s"}
                  </Text>
                </View>
                <Pressable
                  testID={`bulk-assign-${item.id}`}
                  onPress={async (e) => {
                    e.stopPropagation();
                    await apiPost(`/onboarding/bulk-assign`, { caregiver_id: item.id });
                    load();
                  }}
                  style={{ paddingHorizontal: 10, paddingVertical: 8, borderRadius: 10, backgroundColor: theme.colors.brandPrimary }}
                  hitSlop={6}
                >
                  <Text style={{ color: "#fff", fontSize: 11, fontWeight: "700" }}>Assign Packet</Text>
                </Pressable>
              </Pressable>
            );
          }}
        />
      )}

      {tab === "onboarding" && (
        <FlatList
          data={steps}
          keyExtractor={(i) => i.id}
          contentContainerStyle={styles.list}
          ItemSeparatorComponent={() => <View style={{ height: 10 }} />}
          refreshControl={<RefreshControl refreshing={loading} onRefresh={load} />}
          ListEmptyComponent={!loading ? <Empty text="No onboarding steps yet" /> : null}
          renderItem={({ item }) => {
            const cg = caregivers.find((c) => c.id === item.caregiver_id);
            return (
              <Pressable
                testID={`step-${item.id}`}
                onPress={() => toggleStep(item.id)}
                style={styles.row}
              >
                <View style={[styles.checkbox, item.completed && styles.checkboxOn]}>
                  {item.completed && <Ionicons name="checkmark" size={16} color="#fff" />}
                </View>
                <View style={{ flex: 1 }}>
                  <Text style={[styles.rowTitle, item.completed && { textDecorationLine: "line-through", color: theme.colors.muted }]}>
                    {item.title}
                  </Text>
                  {isAdmin && cg && <Text style={styles.rowSub}>For {cg.name}</Text>}
                  {!!item.description && <Text style={styles.rowSub}>{item.description}</Text>}
                </View>
                {isAdmin && (
                  <Pressable
                    testID={`delete-step-${item.id}`}
                    onPress={() => apiDelete(`/onboarding/${item.id}`).then(load)}
                    hitSlop={10}
                  >
                    <Ionicons name="trash-outline" size={18} color={theme.colors.error} />
                  </Pressable>
                )}
              </Pressable>
            );
          }}
        />
      )}

      {/* Training link */}
      <Pressable
        testID="open-training-button"
        onPress={() => router.push("/training")}
        style={styles.floatLink}
      >
        <Ionicons name="school" size={16} color="#fff" />
        <Text style={styles.floatLinkText}>Training Library</Text>
      </Pressable>

      {/* Add Client */}
      <BottomSheet visible={showClient} onClose={() => setShowClient(false)} title="Add client">
        <Field label="Name">
          <TextInput
            testID="client-name-input"
            value={cName} onChangeText={setCName}
            placeholder="Client full name"
            placeholderTextColor={theme.colors.muted}
            style={styles.input}
          />
        </Field>
        <Field label="Address">
          <TextInput value={cAddress} onChangeText={setCAddress} style={styles.input} placeholder="123 Main St" placeholderTextColor={theme.colors.muted} />
        </Field>
        <Field label="Phone">
          <TextInput value={cPhone} onChangeText={setCPhone} style={styles.input} placeholder="(555) 555-5555" placeholderTextColor={theme.colors.muted} keyboardType="phone-pad" />
        </Field>
        <Primary onPress={addClient} testID="submit-client-button" label="Save client" disabled={!cName.trim()} />
      </BottomSheet>

      {/* Add Assignment */}
      <BottomSheet visible={showAssign} onClose={() => setShowAssign(false)} title="Assign caregiver">
        <Field label="Caregiver">
          <Picker
            options={caregivers.map((c) => ({ id: c.id, label: c.name }))}
            value={aCaregiver}
            onChange={setACaregiver}
            testID="pick-caregiver"
          />
        </Field>
        <Field label="Client">
          <Picker
            options={clients.map((c) => ({ id: c.id, label: c.name }))}
            value={aClient}
            onChange={setAClient}
            testID="pick-client"
          />
        </Field>
        <Field label="Schedule">
          <TextInput value={aSchedule} onChangeText={setASchedule} style={styles.input} placeholder="Mon/Wed/Fri 9am–12pm" placeholderTextColor={theme.colors.muted} />
        </Field>
        <Primary onPress={addAssignment} testID="submit-assign-button" label="Create assignment" disabled={!aCaregiver || !aClient} />
      </BottomSheet>

      {/* Add Step */}
      <BottomSheet visible={showStep} onClose={() => setShowStep(false)} title="New onboarding step">
        <Field label="Caregiver">
          <Picker
            options={caregivers.map((c) => ({ id: c.id, label: c.name }))}
            value={sCaregiver}
            onChange={setSCaregiver}
            testID="pick-step-caregiver"
          />
        </Field>
        <Field label="Step title">
          <TextInput
            testID="step-title-input"
            value={sTitle} onChangeText={setSTitle}
            style={styles.input}
            placeholder="e.g. Complete I-9 form"
            placeholderTextColor={theme.colors.muted}
          />
        </Field>
        <Field label="Description">
          <TextInput
            value={sDesc} onChangeText={setSDesc}
            style={[styles.input, { minHeight: 60, textAlignVertical: "top" }]}
            multiline
            placeholder="Optional details"
            placeholderTextColor={theme.colors.muted}
          />
        </Field>
        <Primary onPress={addStep} testID="submit-step-button" label="Add step" disabled={!sCaregiver || !sTitle.trim()} />
      </BottomSheet>
    </SafeAreaView>
  );
}

function Empty({ text }: { text: string }) {
  return (
    <View style={styles.empty}>
      <View style={styles.emptyIcon}>
        <Ionicons name="albums-outline" size={36} color={theme.colors.brandPrimary} />
      </View>
      <Text style={styles.emptyTitle}>{text}</Text>
    </View>
  );
}

function Field({ label, children }: any) {
  return (
    <View style={{ gap: 6 }}>
      <Text style={styles.fieldLabel}>{label}</Text>
      {children}
    </View>
  );
}

function Primary({ onPress, label, disabled, testID }: any) {
  return (
    <Pressable
      testID={testID}
      onPress={onPress}
      disabled={disabled}
      style={[styles.primaryBtn, disabled && { opacity: 0.5 }]}
    >
      <Text style={styles.primaryBtnText}>{label}</Text>
    </Pressable>
  );
}

function Picker({ options, value, onChange, testID }: any) {
  return (
    <ScrollView horizontal showsHorizontalScrollIndicator={false} contentContainerStyle={{ gap: 8 }}>
      {options.length === 0 && <Text style={{ color: theme.colors.muted, fontSize: 12 }}>None available</Text>}
      {options.map((o: any) => {
        const active = value === o.id;
        return (
          <Pressable
            key={o.id}
            testID={`${testID}-${o.id}`}
            onPress={() => onChange(o.id)}
            style={[styles.chip, active && styles.chipActive]}
          >
            <Text style={[styles.chipText, active && styles.chipTextActive]}>{o.label}</Text>
          </Pressable>
        );
      })}
    </ScrollView>
  );
}

function BottomSheet({ visible, onClose, title, children }: any) {
  return (
    <Modal visible={visible} transparent animationType="slide" onRequestClose={onClose}>
      <KeyboardAvoidingView
        behavior={Platform.OS === "ios" ? "padding" : undefined}
        style={{ flex: 1, justifyContent: "flex-end" }}
      >
        <Pressable style={StyleSheet.absoluteFill} onPress={onClose} />
        <View style={styles.sheet}>
          <View style={styles.sheetHandle} />
          <Text style={styles.sheetTitle}>{title}</Text>
          <ScrollView contentContainerStyle={{ gap: 12 }} keyboardShouldPersistTaps="handled">
            {children}
          </ScrollView>
        </View>
      </KeyboardAvoidingView>
    </Modal>
  );
}

const styles = StyleSheet.create({
  root: { flex: 1, backgroundColor: theme.colors.surface },
  header: { paddingHorizontal: 20, paddingTop: 8, paddingBottom: 12, flexDirection: "row", justifyContent: "space-between", alignItems: "center" },
  title: { fontSize: 26, fontWeight: "700", color: theme.colors.onSurface },
  subtitle: { fontSize: 12, color: theme.colors.muted, marginTop: 2 },
  addBtn: { width: 40, height: 40, borderRadius: 12, backgroundColor: theme.colors.brandPrimary, alignItems: "center", justifyContent: "center" },
  tabsWrap: { height: 56, justifyContent: "center" },
  tabsRow: { paddingHorizontal: 20, gap: 8, alignItems: "center" },
  chip: { height: 36, paddingHorizontal: 14, borderRadius: 999, backgroundColor: theme.colors.surfaceSecondary, borderWidth: 1, borderColor: theme.colors.border, alignItems: "center", justifyContent: "center", flexShrink: 0, flexDirection: "row" },
  chipActive: { backgroundColor: theme.colors.brandPrimary, borderColor: theme.colors.brandPrimary },
  chipText: { fontSize: 13, fontWeight: "600", color: theme.colors.onSurfaceTertiary },
  chipTextActive: { color: "#fff" },
  list: { paddingHorizontal: 20, paddingTop: 8, paddingBottom: 80 },
  row: { flexDirection: "row", alignItems: "center", gap: 12, backgroundColor: theme.colors.surfaceSecondary, padding: 14, borderRadius: 14, borderWidth: 1, borderColor: theme.colors.border },
  rowIcon: { width: 40, height: 40, borderRadius: 10, alignItems: "center", justifyContent: "center" },
  rowTitle: { fontSize: 15, fontWeight: "600", color: theme.colors.onSurface },
  rowSub: { fontSize: 12, color: theme.colors.muted, marginTop: 2 },
  checkbox: { width: 26, height: 26, borderRadius: 13, borderWidth: 2, borderColor: theme.colors.borderStrong, alignItems: "center", justifyContent: "center" },
  checkboxOn: { backgroundColor: theme.colors.success, borderColor: theme.colors.success },
  empty: { alignItems: "center", paddingVertical: 60, gap: 12 },
  emptyIcon: { width: 72, height: 72, borderRadius: 22, backgroundColor: theme.colors.brandTertiary, alignItems: "center", justifyContent: "center" },
  emptyTitle: { fontSize: 15, fontWeight: "700", color: theme.colors.onSurface },
  sheet: { backgroundColor: theme.colors.surface, borderTopLeftRadius: 24, borderTopRightRadius: 24, padding: 20, paddingBottom: 32, gap: 12, maxHeight: "85%" },
  sheetHandle: { width: 40, height: 4, borderRadius: 2, backgroundColor: theme.colors.border, alignSelf: "center" },
  sheetTitle: { fontSize: 20, fontWeight: "700", color: theme.colors.onSurface },
  fieldLabel: { fontSize: 12, fontWeight: "700", color: theme.colors.muted, textTransform: "uppercase", letterSpacing: 0.8 },
  input: { backgroundColor: theme.colors.surfaceSecondary, borderWidth: 1, borderColor: theme.colors.border, borderRadius: 12, paddingHorizontal: 14, paddingVertical: 12, fontSize: 15, color: theme.colors.onSurface },
  primaryBtn: { backgroundColor: theme.colors.brandPrimary, padding: 16, borderRadius: 12, alignItems: "center", marginTop: 6 },
  primaryBtnText: { color: "#fff", fontWeight: "700", fontSize: 15 },
  floatLink: { position: "absolute", bottom: 20, alignSelf: "center", backgroundColor: theme.colors.brand, paddingHorizontal: 16, paddingVertical: 12, borderRadius: 999, flexDirection: "row", gap: 6, alignItems: "center" },
  floatLinkText: { color: "#fff", fontWeight: "700", fontSize: 13 },
});
