import { useCallback, useMemo, useRef, useState } from "react";
import {
  View, Text, StyleSheet, ScrollView, Pressable, TextInput, Modal,
  ActivityIndicator, RefreshControl, KeyboardAvoidingView, Platform, FlatList,
  Linking,
} from "react-native";
import { Ionicons } from "@expo/vector-icons";
import { SafeAreaView } from "react-native-safe-area-context";
import * as DocumentPicker from "expo-document-picker";
import { useFocusEffect } from "expo-router";
import SignatureScreen, { SignatureViewRef } from "react-native-signature-canvas";
import { useAuth } from "@/src/context/AuthContext";
import { apiGet, apiPost, apiDelete, API_BASE } from "@/src/api/client";
import AsyncStorage from "@react-native-async-storage/async-storage";
import { openAuthedFile } from "@/src/utils/open-file";
import { theme } from "@/src/theme";

type DocCategory = "client" | "caregiver" | "client_onboarding" | "caregiver_onboarding" | "credential" | "training" | "policy";

type DocItem = {
  id: string;
  title: string;
  category: DocCategory;
  notes?: string;
  uploaded_at: string;
  file_base64?: string | null;
  mime_type?: string;
  expires_at?: string | null;
  seq?: number | null;
  is_template?: boolean;
  owner_id?: string | null;
};

const CATEGORIES: { key: DocCategory | "all"; label: string; icon: any }[] = [
  { key: "all", label: "All", icon: "albums-outline" },
  { key: "client_onboarding", label: "Client Onboarding", icon: "clipboard-outline" },
  { key: "caregiver_onboarding", label: "Caregiver Onboarding", icon: "person-add-outline" },
  { key: "credential", label: "Credentials", icon: "ribbon-outline" },
  { key: "client", label: "Client Files", icon: "people-outline" },
  { key: "caregiver", label: "Caregiver Files", icon: "medkit-outline" },
  { key: "training", label: "Training", icon: "school-outline" },
];

export default function Documents() {
  const { user } = useAuth();
  const [docs, setDocs] = useState<DocItem[]>([]);
  const [filter, setFilter] = useState<DocCategory | "all">("all");
  const [loading, setLoading] = useState(false);
  const [showAdd, setShowAdd] = useState(false);
  const [title, setTitle] = useState("");
  const [notes, setNotes] = useState("");
  const [category, setCategory] = useState<DocCategory>("credential");
  const [pickedFile, setPickedFile] = useState<{ base64: string; mime: string; name: string } | null>(null);
  const [expiresAt, setExpiresAt] = useState("");
  const [credTemplates, setCredTemplates] = useState<string[]>([]);
  const [seeding, setSeeding] = useState(false);
  const [submitting, setSubmitting] = useState(false);

  // signature
  const [signDoc, setSignDoc] = useState<DocItem | null>(null);
  const sigRef = useRef<SignatureViewRef>(null);

  // packet share
  const [showShare, setShowShare] = useState(false);
  const [shareName, setShareName] = useState("");
  const [shareEmail, setShareEmail] = useState("");
  const [shareCategory, setShareCategory] = useState<"client_onboarding" | "caregiver_onboarding">("client_onboarding");
  const [shareLink, setShareLink] = useState<string | null>(null);

  const load = useCallback(async () => {
    setLoading(true);
    try {
      const d = await apiGet<DocItem[]>("/documents");
      setDocs(d);
      try {
        const t = await apiGet<{ titles: string[] }>("/credentials/templates");
        setCredTemplates(t.titles);
      } catch { /* non-blocking */ }
    } catch (e) {
      console.log(e);
    } finally {
      setLoading(false);
    }
  }, []);

  useFocusEffect(useCallback(() => { load(); }, [load]));

  const filtered = useMemo(
    () => {
      const nonPolicy = docs.filter((d) => d.category !== "policy");
      return filter === "all" ? nonPolicy : nonPolicy.filter((d) => d.category === filter);
    },
    [docs, filter]
  );

  const pickFile = async () => {
    const res = await DocumentPicker.getDocumentAsync({ base64: true, copyToCacheDirectory: true });
    if (res.canceled || !res.assets?.length) return;
    const a = res.assets[0];
    let base64 = a.base64;
    if (!base64 && a.uri) {
      try {
        const r = await fetch(a.uri);
        const b = await r.blob();
        base64 = await new Promise<string>((resolve, reject) => {
          const fr = new FileReader();
          fr.onloadend = () => {
            const result = (fr.result as string) || "";
            resolve(result.split(",")[1] || "");
          };
          fr.onerror = reject;
          fr.readAsDataURL(b);
        });
      } catch (e) { console.log("read error", e); }
    }
    if (!base64) return;
    setPickedFile({ base64, mime: a.mimeType || "application/octet-stream", name: a.name });
  };

  const submit = async () => {
    if (!title.trim()) return;
    setSubmitting(true);
    try {
      const expiry = expiresAt.trim();
      const isoExpiry = expiry && /^\d{4}-\d{2}-\d{2}$/.test(expiry)
        ? new Date(`${expiry}T00:00:00Z`).toISOString()
        : null;
      const isCred = category === "credential";
      await apiPost("/documents", {
        title: title.trim(),
        category,
        notes,
        owner_type: isCred ? "caregiver" : "agency",
        owner_id: isCred ? user?.id : null,
        file_base64: pickedFile?.base64 || null,
        mime_type: pickedFile?.mime || "application/pdf",
        expires_at: isoExpiry,
      });
      setShowAdd(false);
      setTitle(""); setNotes(""); setPickedFile(null);
      setExpiresAt(""); setCategory("credential");
      await load();
    } catch (e) {
      console.log(e);
    } finally {
      setSubmitting(false);
    }
  };

  const seedTemplates = async () => {
    setSeeding(true);
    try {
      await apiPost<{ created: number }>("/documents/seed-templates", {});
      await load();
    } catch (e) {
      console.log(e);
    } finally {
      setSeeding(false);
    }
  };

  const removeDoc = async (id: string) => {
    await apiDelete(`/documents/${id}`);
    load();
  };

  return (
    <SafeAreaView edges={["top"]} style={styles.root}>
      <View style={styles.header}>
        <View>
          <Text style={styles.title}>Document Vault</Text>
          <Text style={styles.subtitle}>{docs.length} item{docs.length === 1 ? "" : "s"}</Text>
        </View>
        {user?.role === "admin" && (
          <View style={{ flexDirection: "row", gap: 8 }}>
            <Pressable
              testID="audit-binder-button"
              onPress={async () => {
                try {
                  await openAuthedFile("/reports/audit-binder", "SisterToSister_AuditBinder.pdf");
                } catch (e) { console.log("binder", e); }
              }}
              style={[styles.seedBtn, { backgroundColor: theme.colors.brand }]}
            >
              <Ionicons name="document-text-outline" size={16} color="#fff" />
              <Text style={[styles.seedBtnText, { color: "#fff" }]}>Audit Binder</Text>
            </Pressable>
            <Pressable
              testID="share-packet-button"
              onPress={() => { setShareLink(null); setShowShare(true); }}
              style={styles.seedBtn}
            >
              <Ionicons name="paper-plane-outline" size={16} color={theme.colors.brandPrimary} />
              <Text style={styles.seedBtnText}>Share Packet</Text>
            </Pressable>
            <Pressable
              testID="seed-templates-button"
              onPress={seedTemplates}
              disabled={seeding}
              style={styles.seedBtn}
            >
              {seeding ? (
                <ActivityIndicator size="small" color={theme.colors.brandPrimary} />
              ) : (
                <>
                  <Ionicons name="download-outline" size={16} color={theme.colors.brandPrimary} />
                  <Text style={styles.seedBtnText}>Templates</Text>
                </>
              )}
            </Pressable>
            <Pressable testID="add-document-button" onPress={() => setShowAdd(true)} style={styles.addBtn}>
              <Ionicons name="add" size={22} color="#fff" />
            </Pressable>
          </View>
        )}
        {user?.role === "caregiver" && (
          <Pressable testID="add-document-button" onPress={() => { setCategory("credential"); setShowAdd(true); }} style={styles.addBtn}>
            <Ionicons name="add" size={22} color="#fff" />
          </Pressable>
        )}
      </View>

      <View style={styles.chipsWrap}>
        <ScrollView
          horizontal showsHorizontalScrollIndicator={false}
          contentContainerStyle={styles.chipsRow}
        >
          {CATEGORIES.map((c) => {
            const active = filter === c.key;
            return (
              <Pressable
                key={c.key}
                testID={`chip-${c.key}`}
                onPress={() => setFilter(c.key)}
                style={[styles.chip, active && styles.chipActive]}
              >
                <Ionicons
                  name={c.icon}
                  size={14}
                  color={active ? "#fff" : theme.colors.onSurfaceTertiary}
                />
                <Text style={[styles.chipText, active && styles.chipTextActive]}>{c.label}</Text>
              </Pressable>
            );
          })}
        </ScrollView>
      </View>

      <FlatList
        data={filtered}
        keyExtractor={(i) => i.id}
        contentContainerStyle={{ paddingHorizontal: 20, paddingTop: 8, paddingBottom: 32 }}
        refreshControl={<RefreshControl refreshing={loading} onRefresh={load} />}
        ItemSeparatorComponent={() => <View style={{ height: 10 }} />}
        ListEmptyComponent={
          loading ? null : (
            <View style={styles.empty}>
              <View style={styles.emptyIcon}>
                <Ionicons name="folder-open-outline" size={40} color={theme.colors.brandPrimary} />
              </View>
              <Text style={styles.emptyTitle}>No documents yet</Text>
              <Text style={styles.emptySubtitle}>
                {user?.role === "admin"
                  ? "Tap + to upload your first compliance document"
                  : "Your admin will share documents here"}
              </Text>
            </View>
          )
        }
        renderItem={({ item }) => {
          const expiry = item.expires_at ? new Date(item.expires_at) : null;
          const expSoon =
            expiry && expiry.getTime() - Date.now() < 60 * 24 * 60 * 60 * 1000;
          const expired = expiry && expiry.getTime() < Date.now();
          const hasFile = !!item.file_base64;
          const open = async () => {
            if (!hasFile) return;
            try {
              await openAuthedFile(`/documents/${item.id}/stamped`, `${item.title}.pdf`);
            } catch (e) { console.log("open error", e); }
          };
          return (
            <Pressable
              onPress={hasFile ? open : undefined}
              style={styles.docCard}
              testID={`doc-${item.id}`}
            >
              <View style={[styles.docIcon, hasFile && { backgroundColor: theme.colors.success }]}>
                <Ionicons
                  name={
                    hasFile ? "document-attach" :
                    item.is_template ? "document-outline" :
                    item.category === "credential" ? "ribbon-outline" :
                    "document-text-outline"
                  }
                  size={20}
                  color={hasFile ? "#fff" : theme.colors.brandPrimary}
                />
              </View>
              <View style={{ flex: 1 }}>
                <Text style={styles.docTitle} numberOfLines={1}>{item.title}</Text>
                <Text style={styles.docMeta}>
                  {item.category.replace("_", " ").toUpperCase()} · {hasFile ? "PDF" : "No file"} · {new Date(item.uploaded_at).toLocaleDateString()}
                </Text>
                {!!expiry && (
                  <View style={[
                    styles.expBadge,
                    expired ? { backgroundColor: theme.colors.error } :
                    expSoon ? { backgroundColor: theme.colors.warning } :
                    { backgroundColor: theme.colors.success }
                  ]}>
                    <Ionicons name="time-outline" size={11} color="#fff" />
                    <Text style={styles.expBadgeText}>
                      {expired ? "EXPIRED" : "Expires"} {expiry.toLocaleDateString()}
                    </Text>
                  </View>
                )}
                {!!item.notes && <Text style={styles.docNotes} numberOfLines={2}>{item.notes}</Text>}
              </View>
              {hasFile && (
                <Pressable testID={`view-doc-${item.id}`} onPress={open} hitSlop={10} style={styles.viewBtn}>
                  <Ionicons name="eye-outline" size={18} color={theme.colors.brandPrimary} />
                </Pressable>
              )}
              {hasFile && item.mime_type === "application/pdf" && (
                <Pressable
                  testID={`sign-doc-${item.id}`}
                  onPress={() => setSignDoc(item)}
                  hitSlop={10}
                  style={[styles.viewBtn, { backgroundColor: theme.colors.success }]}
                >
                  <Ionicons name="create-outline" size={18} color="#fff" />
                </Pressable>
              )}
              {(user?.role === "admin" || item.owner_id === user?.id) && (
                <Pressable
                  testID={`delete-doc-${item.id}`}
                  onPress={() => removeDoc(item.id)}
                  hitSlop={10}
                >
                  <Ionicons name="trash-outline" size={18} color={theme.colors.error} />
                </Pressable>
              )}
            </Pressable>
          );
        }}
      />

      <Modal visible={showAdd} animationType="slide" transparent onRequestClose={() => setShowAdd(false)}>
        <KeyboardAvoidingView
          behavior={Platform.OS === "ios" ? "padding" : undefined}
          style={styles.modalRoot}
        >
          <Pressable style={styles.backdrop} onPress={() => setShowAdd(false)} />
          <View style={styles.sheet}>
            <View style={styles.sheetHandle} />
            <Text style={styles.sheetTitle}>Upload document</Text>

            <Text style={styles.fieldLabel}>Title</Text>
            <TextInput
              testID="doc-title-input"
              value={title} onChangeText={setTitle}
              placeholder="e.g. Background check - John Doe"
              placeholderTextColor={theme.colors.muted}
              style={styles.input}
            />

            <Text style={styles.fieldLabel}>Category</Text>
            <ScrollView horizontal showsHorizontalScrollIndicator={false} contentContainerStyle={{ gap: 8 }}>
              {CATEGORIES.filter((c) => c.key !== "all" && (user?.role === "admin" || c.key === "credential")).map((c) => {
                const active = category === c.key;
                return (
                  <Pressable
                    key={c.key}
                    onPress={() => setCategory(c.key as DocCategory)}
                    style={[styles.chip, active && styles.chipActive]}
                    testID={`pick-cat-${c.key}`}
                  >
                    <Text style={[styles.chipText, active && styles.chipTextActive]}>{c.label}</Text>
                  </Pressable>
                );
              })}
            </ScrollView>

            {category === "credential" && credTemplates.length > 0 && (
              <>
                <Text style={styles.fieldLabel}>Suggested credentials</Text>
                <ScrollView horizontal showsHorizontalScrollIndicator={false} contentContainerStyle={{ gap: 8 }}>
                  {credTemplates.map((t) => (
                    <Pressable
                      key={t}
                      testID={`cred-suggest-${t.slice(0, 10)}`}
                      onPress={() => setTitle(t)}
                      style={[styles.chip, { backgroundColor: theme.colors.brandTertiary, borderColor: theme.colors.brandPrimary }]}
                    >
                      <Text style={[styles.chipText, { color: theme.colors.brandPrimary }]}>{t}</Text>
                    </Pressable>
                  ))}
                </ScrollView>
              </>
            )}

            {(category === "credential" || category === "caregiver_onboarding" || category === "client_onboarding") && (
              <>
                <Text style={styles.fieldLabel}>Expiration date (YYYY-MM-DD, optional)</Text>
                <TextInput
                  testID="doc-expiry-input"
                  value={expiresAt}
                  onChangeText={setExpiresAt}
                  placeholder="2026-12-31"
                  placeholderTextColor={theme.colors.muted}
                  autoCapitalize="none"
                  style={styles.input}
                />
              </>
            )}

            <Text style={styles.fieldLabel}>Notes (optional)</Text>
            <TextInput
              value={notes} onChangeText={setNotes}
              placeholder="Add context or expiration date"
              placeholderTextColor={theme.colors.muted}
              multiline
              style={[styles.input, { minHeight: 64, textAlignVertical: "top" }]}
            />

            <Pressable testID="doc-pick-file" onPress={pickFile} style={styles.pickBtn}>
              <Ionicons name="cloud-upload-outline" size={18} color={theme.colors.brandPrimary} />
              <Text style={styles.pickBtnText}>
                {pickedFile ? pickedFile.name : "Attach file (optional)"}
              </Text>
            </Pressable>

            <Pressable
              testID="submit-doc-button"
              onPress={submit}
              disabled={submitting || !title.trim()}
              style={[styles.primaryBtn, (!title.trim() || submitting) && { opacity: 0.6 }]}
            >
              {submitting ? <ActivityIndicator color="#fff" /> : <Text style={styles.primaryBtnText}>Save</Text>}
            </Pressable>
          </View>
        </KeyboardAvoidingView>
      </Modal>

      {/* Signature modal */}
      <Modal visible={!!signDoc} transparent animationType="slide" onRequestClose={() => setSignDoc(null)}>
        <View style={styles.modalRoot}>
          <Pressable style={styles.backdrop} onPress={() => setSignDoc(null)} />
          <View style={[styles.sheet, { height: 460 }]}>
            <View style={styles.sheetHandle} />
            <Text style={styles.sheetTitle} numberOfLines={1}>Sign: {signDoc?.title}</Text>
            <Text style={{ fontSize: 12, color: theme.colors.muted, marginBottom: 6 }}>
              Draw your signature below. It will be applied to the bottom-right of the last page.
            </Text>
            <View style={{ flex: 1, borderWidth: 1, borderColor: theme.colors.border, borderRadius: 12, overflow: "hidden" }}>
              <SignatureScreen
                ref={sigRef}
                onOK={async (sig: string) => {
                  if (!signDoc) return;
                  try {
                    await apiPost(`/documents/${signDoc.id}/sign`, { signature_base64: sig });
                    setSignDoc(null);
                    await load();
                  } catch (e) { console.log("sign error", e); }
                }}
                onEmpty={() => console.log("empty sig")}
                webStyle={`.m-signature-pad--footer {display: none;} .m-signature-pad {box-shadow:none; border:none;} body,html { background:#fff; }`}
                descriptionText=""
                clearText="Clear"
                confirmText="Save"
              />
            </View>
            <View style={{ flexDirection: "row", gap: 10, marginTop: 12 }}>
              <Pressable
                testID="sig-clear"
                onPress={() => sigRef.current?.clearSignature()}
                style={[styles.primaryBtn, { flex: 1, backgroundColor: theme.colors.surfaceTertiary }]}
              >
                <Text style={[styles.primaryBtnText, { color: theme.colors.onSurface }]}>Clear</Text>
              </Pressable>
              <Pressable
                testID="sig-save"
                onPress={() => sigRef.current?.readSignature()}
                style={[styles.primaryBtn, { flex: 1 }]}
              >
                <Text style={styles.primaryBtnText}>Sign & Save</Text>
              </Pressable>
            </View>
          </View>
        </View>
      </Modal>

      {/* Share packet modal */}
      <Modal visible={showShare} transparent animationType="slide" onRequestClose={() => setShowShare(false)}>
        <KeyboardAvoidingView
          behavior={Platform.OS === "ios" ? "padding" : undefined}
          style={styles.modalRoot}
        >
          <Pressable style={styles.backdrop} onPress={() => setShowShare(false)} />
          <View style={styles.sheet}>
            <View style={styles.sheetHandle} />
            <Text style={styles.sheetTitle}>Share onboarding packet</Text>
            <Text style={{ fontSize: 12, color: theme.colors.muted }}>
              Generate a personalized link the recipient can use to view & sign the entire numbered packet on any device — no login required.
            </Text>

            <Text style={styles.fieldLabel}>Packet type</Text>
            <View style={{ flexDirection: "row", gap: 8 }}>
              {[
                { k: "client_onboarding", l: "Client (13)" },
                { k: "caregiver_onboarding", l: "Caregiver (14)" },
              ].map((p) => {
                const active = shareCategory === p.k;
                return (
                  <Pressable
                    key={p.k}
                    testID={`share-cat-${p.k}`}
                    onPress={() => setShareCategory(p.k as any)}
                    style={[styles.chip, active && styles.chipActive, { flex: 1, justifyContent: "center" }]}
                  >
                    <Text style={[styles.chipText, active && styles.chipTextActive]}>{p.l}</Text>
                  </Pressable>
                );
              })}
            </View>

            <Text style={styles.fieldLabel}>Recipient name</Text>
            <TextInput
              testID="share-name-input"
              value={shareName} onChangeText={setShareName}
              placeholder="Jane Doe"
              placeholderTextColor={theme.colors.muted}
              style={styles.input}
            />
            <Text style={styles.fieldLabel}>Recipient email (optional)</Text>
            <TextInput
              testID="share-email-input"
              value={shareEmail} onChangeText={setShareEmail}
              placeholder="jane@example.com"
              placeholderTextColor={theme.colors.muted}
              autoCapitalize="none"
              keyboardType="email-address"
              style={styles.input}
            />

            {!shareLink ? (
              <Pressable
                testID="share-generate-btn"
                disabled={!shareName.trim()}
                onPress={async () => {
                  try {
                    const r = await apiPost<{ link: string }>("/packets/share", {
                      recipient_name: shareName.trim(),
                      recipient_role: shareCategory === "client_onboarding" ? "client" : "caregiver",
                      category: shareCategory,
                      delivery: "link",
                      recipient_email: shareEmail.trim() || null,
                    });
                    setShareLink(r.link);
                  } catch (e) { console.log("share err", e); }
                }}
                style={[styles.primaryBtn, !shareName.trim() && { opacity: 0.5 }]}
              >
                <Text style={styles.primaryBtnText}>Generate link</Text>
              </Pressable>
            ) : (
              <View style={{ gap: 8 }}>
                <Text style={styles.fieldLabel}>Personalized link (tap to copy)</Text>
                <Pressable
                  testID="share-link-copy"
                  onPress={() => {
                    if (Platform.OS === "web" && typeof navigator !== "undefined" && navigator.clipboard) {
                      navigator.clipboard.writeText(shareLink);
                    }
                  }}
                  style={[styles.input, { backgroundColor: theme.colors.brandTertiary, borderColor: theme.colors.brandPrimary }]}
                >
                  <Text selectable style={{ fontSize: 13, color: theme.colors.brandPrimary, fontWeight: "600" }}>
                    {shareLink}
                  </Text>
                </Pressable>
                <Pressable
                  testID="share-open-btn"
                  onPress={() => Linking.openURL(shareLink)}
                  style={[styles.primaryBtn, { backgroundColor: theme.colors.success }]}
                >
                  <Text style={styles.primaryBtnText}>Open preview</Text>
                </Pressable>
                <Pressable
                  onPress={() => { setShareLink(null); setShareName(""); setShareEmail(""); }}
                  style={styles.secondaryBtn}
                >
                  <Text style={styles.secondaryBtnText}>Generate another</Text>
                </Pressable>
              </View>
            )}
          </View>
        </KeyboardAvoidingView>
      </Modal>
    </SafeAreaView>
  );
}

const styles = StyleSheet.create({
  root: { flex: 1, backgroundColor: theme.colors.surface },
  header: { paddingHorizontal: 20, paddingTop: 8, paddingBottom: 12, flexDirection: "row", justifyContent: "space-between", alignItems: "center" },
  title: { fontSize: 26, fontWeight: "700", color: theme.colors.onSurface },
  subtitle: { fontSize: 12, color: theme.colors.muted, marginTop: 2 },
  addBtn: { width: 40, height: 40, borderRadius: 12, backgroundColor: theme.colors.brandPrimary, alignItems: "center", justifyContent: "center" },
  seedBtn: {
    flexDirection: "row", alignItems: "center", gap: 4,
    paddingHorizontal: 12, height: 40, borderRadius: 12,
    backgroundColor: theme.colors.brandTertiary, borderWidth: 1, borderColor: theme.colors.brandPrimary,
  },
  seedBtnText: { color: theme.colors.brandPrimary, fontWeight: "700", fontSize: 12 },
  expBadge: {
    flexDirection: "row", alignItems: "center", gap: 4,
    paddingHorizontal: 8, paddingVertical: 3, borderRadius: 999,
    alignSelf: "flex-start", marginTop: 4,
  },
  expBadgeText: { color: "#fff", fontSize: 10, fontWeight: "700", letterSpacing: 0.4 },
  viewBtn: {
    width: 32, height: 32, borderRadius: 8,
    backgroundColor: theme.colors.brandTertiary,
    alignItems: "center", justifyContent: "center", marginRight: 4,
  },
  modalRoot: { flex: 1, justifyContent: "flex-end" },
  backdrop: { ...StyleSheet.absoluteFillObject, backgroundColor: "rgba(0,0,0,0.4)" },
  secondaryBtn: { padding: 12, alignItems: "center" },
  secondaryBtnText: { color: theme.colors.brandPrimary, fontWeight: "600", fontSize: 14 },
  chipsWrap: { height: 56, justifyContent: "center" },
  chipsRow: { paddingHorizontal: 20, gap: 8, alignItems: "center" },
  chip: {
    height: 36, paddingHorizontal: 12,
    borderRadius: 999, backgroundColor: theme.colors.surfaceSecondary,
    borderWidth: 1, borderColor: theme.colors.border,
    flexDirection: "row", alignItems: "center", gap: 6,
    flexShrink: 0,
  },
  chipActive: { backgroundColor: theme.colors.brandPrimary, borderColor: theme.colors.brandPrimary },
  chipText: { fontSize: 13, fontWeight: "600", color: theme.colors.onSurfaceTertiary },
  chipTextActive: { color: "#fff" },
  docCard: {
    flexDirection: "row", alignItems: "center", gap: 12,
    backgroundColor: theme.colors.surfaceSecondary, padding: 14, borderRadius: 14,
    borderWidth: 1, borderColor: theme.colors.border,
  },
  docIcon: {
    width: 44, height: 44, borderRadius: 10,
    backgroundColor: theme.colors.brandTertiary,
    alignItems: "center", justifyContent: "center",
  },
  docTitle: { fontSize: 15, fontWeight: "600", color: theme.colors.onSurface },
  docMeta: { fontSize: 11, color: theme.colors.muted, marginTop: 2, fontWeight: "600", letterSpacing: 0.5 },
  docNotes: { fontSize: 12, color: theme.colors.onSurfaceTertiary, marginTop: 4 },
  empty: { alignItems: "center", paddingVertical: 80, gap: 8 },
  emptyIcon: { width: 80, height: 80, borderRadius: 24, backgroundColor: theme.colors.brandTertiary, alignItems: "center", justifyContent: "center" },
  emptyTitle: { fontSize: 16, fontWeight: "700", color: theme.colors.onSurface, marginTop: 12 },
  emptySubtitle: { fontSize: 13, color: theme.colors.muted, textAlign: "center", paddingHorizontal: 40 },
  modalRoot: { flex: 1, justifyContent: "flex-end" },
  backdrop: { ...StyleSheet.absoluteFillObject, backgroundColor: "rgba(0,0,0,0.4)" },
  sheet: {
    backgroundColor: theme.colors.surface,
    borderTopLeftRadius: 24, borderTopRightRadius: 24,
    padding: 20, paddingBottom: 32, gap: 10,
  },
  sheetHandle: { width: 40, height: 4, borderRadius: 2, backgroundColor: theme.colors.border, alignSelf: "center", marginBottom: 8 },
  sheetTitle: { fontSize: 20, fontWeight: "700", color: theme.colors.onSurface },
  fieldLabel: { fontSize: 12, fontWeight: "700", color: theme.colors.muted, textTransform: "uppercase", letterSpacing: 0.8, marginTop: 6 },
  input: {
    backgroundColor: theme.colors.surfaceSecondary,
    borderWidth: 1, borderColor: theme.colors.border,
    borderRadius: 12, paddingHorizontal: 14, paddingVertical: 12,
    fontSize: 15, color: theme.colors.onSurface,
  },
  pickBtn: {
    flexDirection: "row", alignItems: "center", gap: 8,
    borderWidth: 1, borderStyle: "dashed", borderColor: theme.colors.brandPrimary,
    backgroundColor: theme.colors.brandTertiary, padding: 14, borderRadius: 12, marginTop: 6,
  },
  pickBtnText: { color: theme.colors.brandPrimary, fontWeight: "600", fontSize: 14, flex: 1 },
  primaryBtn: {
    backgroundColor: theme.colors.brandPrimary, padding: 16,
    borderRadius: 12, alignItems: "center", marginTop: 10,
  },
  primaryBtnText: { color: "#fff", fontWeight: "700", fontSize: 15 },
});
