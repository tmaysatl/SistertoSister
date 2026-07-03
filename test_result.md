#====================================================================================================
# START - Testing Protocol - DO NOT EDIT OR REMOVE THIS SECTION
#====================================================================================================

# THIS SECTION CONTAINS CRITICAL TESTING INSTRUCTIONS FOR BOTH AGENTS
# BOTH MAIN_AGENT AND TESTING_AGENT MUST PRESERVE THIS ENTIRE BLOCK

# Communication Protocol:
# If the `testing_agent` is available, main agent should delegate all testing tasks to it.
#
# You have access to a file called `test_result.md`. This file contains the complete testing state
# and history, and is the primary means of communication between main and the testing agent.
#
# Main and testing agents must follow this exact format to maintain testing data. 
# The testing data must be entered in yaml format Below is the data structure:
# 
## user_problem_statement: {problem_statement}
## backend:
##   - task: "Task name"
##     implemented: true
##     working: true  # or false or "NA"
##     file: "file_path.py"
##     stuck_count: 0
##     priority: "high"  # or "medium" or "low"
##     needs_retesting: false
##     status_history:
##         -working: true  # or false or "NA"
##         -agent: "main"  # or "testing" or "user"
##         -comment: "Detailed comment about status"
##
## frontend:
##   - task: "Task name"
##     implemented: true
##     working: true  # or false or "NA"
##     file: "file_path.js"
##     stuck_count: 0
##     priority: "high"  # or "medium" or "low"
##     needs_retesting: false
##     status_history:
##         -working: true  # or false or "NA"
##         -agent: "main"  # or "testing" or "user"
##         -comment: "Detailed comment about status"
##
## metadata:
##   created_by: "main_agent"
##   version: "1.0"
##   test_sequence: 0
##   run_ui: false
##
## test_plan:
##   current_focus:
##     - "Task name 1"
##     - "Task name 2"
##   stuck_tasks:
##     - "Task name with persistent issues"
##   test_all: false
##   test_priority: "high_first"  # or "sequential" or "stuck_first"
##
## agent_communication:
##     -agent: "main"  # or "testing" or "user"
##     -message: "Communication message between agents"

# Protocol Guidelines for Main agent
#
# 1. Update Test Result File Before Testing:
#    - Main agent must always update the `test_result.md` file before calling the testing agent
#    - Add implementation details to the status_history
#    - Set `needs_retesting` to true for tasks that need testing
#    - Update the `test_plan` section to guide testing priorities
#    - Add a message to `agent_communication` explaining what you've done
#
# 2. Incorporate User Feedback:
#    - When a user provides feedback that something is or isn't working, add this information to the relevant task's status_history
#    - Update the working status based on user feedback
#    - If a user reports an issue with a task that was marked as working, increment the stuck_count
#    - Whenever user reports issue in the app, if we have testing agent and task_result.md file so find the appropriate task for that and append in status_history of that task to contain the user concern and problem as well 
#
# 3. Track Stuck Tasks:
#    - Monitor which tasks have high stuck_count values or where you are fixing same issue again and again, analyze that when you read task_result.md
#    - For persistent issues, use websearch tool to find solutions
#    - Pay special attention to tasks in the stuck_tasks list
#    - When you fix an issue with a stuck task, don't reset the stuck_count until the testing agent confirms it's working
#
# 4. Provide Context to Testing Agent:
#    - When calling the testing agent, provide clear instructions about:
#      - Which tasks need testing (reference the test_plan)
#      - Any authentication details or configuration needed
#      - Specific test scenarios to focus on
#      - Any known issues or edge cases to verify
#
# 5. Call the testing agent with specific instructions referring to test_result.md
#
# IMPORTANT: Main agent must ALWAYS update test_result.md BEFORE calling the testing agent, as it relies on this file to understand what to test next.

#====================================================================================================
# END - Testing Protocol - DO NOT EDIT OR REMOVE THIS SECTION
#====================================================================================================



#====================================================================================================
# Testing Data - Main Agent and testing sub agent both should log testing data below this section
#====================================================================================================

user_problem_statement: |
  Build a HIPAA-ready home health agency compliance app. Currently in the middle of a
  multi-phase database migration from MongoDB to Supabase (Postgres + Auth + Storage).
  Dual-write pattern keeps Mongo authoritative until Phase 7 cutover.

backend:
  - task: "Phase 1 — PDF field extraction pipeline (pdf_parser.py + upload hook + /schema)"
    implemented: true
    working: true
    file: "/app/backend/pdf_parser.py, /app/backend/server.py, /app/backend/requirements.txt"
    stuck_count: 0
    priority: "high"
    needs_retesting: false
    status_history:
      - working: "NA"
        agent: "main"
        comment: |
          Phase 1 backend-only feature. Frontend NOT touched.

          NEW MODULE `/app/backend/pdf_parser.py` exports 3 functions:
            • extract_acroform_fields(pdf_path) — walks pymupdf page.widgets()
              on every page, returns
              [{field_name, field_type, page (1-idx), position:{x0,y0,x1,y1},
                options, required, value, source:'acroform'}, ...]
              field_type normalises pymupdf's field_type_string to one of
              text | checkbox | radio | combobox | listbox | signature | button.
              For choice fields, `choice_values` is unpacked into options.
              Empty list when the PDF has no widget annotations.
            • extract_fields_from_text(pdf_path) — text fallback for flat PDFs.
              Regex `_LABEL_UNDERSCORE_RE` catches "Label: ______" AND
              "Label ______" AND grouped date runs like "D.O.B ___/___/___".
              Checkbox markers ("☐","□","[ ]","hh ") emit type=checkbox with
              options=['YES','NO'] when those tokens appear on the line.
              Duplicates deduplicated via "Name (2)", "Name (3)", ...
              Position approximated via page.search_for(label).
            • parse_pdf(pdf_path) — top-level. Tries AcroForm, falls back to
              text extraction if empty. Never raises.

          NEW DB COLLECTION `field_schemas` keyed by document_id with shape:
            {document_id, fields, field_count, source, extracted_at, parser_version}

          UPLOAD HOOK — POST /api/documents now calls
          _extract_and_store_schema() after the Mongo insert + Postgres mirror.
          Writes the base64 blob to a NamedTemporaryFile, runs parse_pdf,
          upserts the schema row. Best-effort; parse failures never block
          the upload response (logged only).

          NEW ENDPOINT — GET /api/documents/{document_id}/schema returns the
          cached schema. If no cached row exists (older doc that pre-dates
          this feature), lazily backfills from the stored file_base64 on
          first read so historical docs also get a schema. Returns 404 only
          when the parent document is missing. For non-PDFs / PDFs with zero
          detectable fields, returns the empty envelope
          {..., fields: [], field_count: 0, source: 'empty'}.

          DEPENDENCIES:
            • pymupdf==1.28.0 installed via `pip install pymupdf`
            • added to /app/backend/requirements.txt

          END-TO-END SMOKE PASS (`SKilleRN-Fillable.pdf`, 289 KB):
            • Upload → 200 OK
            • GET /schema → 200, field_count=182, source=acroform
              (142 text, 33 checkbox, 6 radio, 1 signature)
            • Bogus doc id → 404 Document not found
            • Mongo `field_schemas` row present with matching field_count
            • Delete cleaned up both stores.

  - task: "PDF stamped endpoint — Unicode-safe Content-Disposition (Latin-1 crash)"
    implemented: true
    working: "NA"
    file: "/app/backend/server.py"
    stuck_count: 0
    priority: "high"
    needs_retesting: true
    status_history:
      - working: "NA"
        agent: "main"
        comment: |
          USER REPORT: "the PDF documents are not the same ones loaded".
          ROOT CAUSE #1 (backend): GET /api/documents/{id}/stamped and the
          packet doc endpoint built `Content-Disposition: inline; filename="{title}.pdf"`
          using the raw document title. Starlette encodes response headers as
          Latin-1, so any title containing a non-ASCII glyph (em-dash, smart
          quotes, accented letters — common in our seed data, e.g. "Policy &
          Procedure Handbook — Sister to Sister, PHCP") raised
          UnicodeEncodeError mid-response → 500 with NO PDF body. The browser
          then either showed the previously-cached PDF (the WRONG document)
          or a blank pane, which is exactly the "documents are not the same
          ones loaded" symptom.
          FIX: Added `_safe_disposition()` helper in server.py that emits
          RFC-5987 compliant Content-Disposition with both an ASCII
          `filename=` (non-ASCII bytes replaced with `_`) AND a
          `filename*=UTF-8''<percent-encoded>` form. Both PDF stamped routes
          (lines ~774 and ~1430) now use the helper. Verified end-to-end
          with the em-dash doc: HTTP 200, %PDF-1.7 body, 21 MB payload,
          Content-Disposition includes both forms.

  - task: "Phase 5 Slice J — MS Graph integrations dual-write (Mongo + Postgres)"
    implemented: true
    working: true
    file: "/app/backend/routers/ms_graph.py, /app/backend/core/supa_data.py"
    stuck_count: 0
    priority: "high"
    needs_retesting: true
    status_history:
      - working: true
        agent: "main"
        comment: |
          Slice J complete. /api/ms/email-recipients, /api/ms/disconnect, and
          internal _ms_save_tokens dual-write to public.integrations
          (provider='microsoft_graph'). Mongo db.integrations still authoritative
          for /api/ms/status reads. Smoke test _smoke_slice_j.py PASSES.

frontend:
  - task: "Safe-area / status-bar overlap fix on Modal-based screens (root StatusBar + ScreenContainer/ScreenHeader helpers + DynamicFormRenderer/SignatureCaptureModal patched)"
    implemented: true
    working: "NA"
    file: "/app/frontend/app/_layout.tsx, /app/frontend/src/components/ScreenContainer.tsx, /app/frontend/src/components/ScreenHeader.tsx, /app/frontend/src/components/DynamicFormRenderer.tsx"
    stuck_count: 0
    priority: "high"
    needs_retesting: true
    status_history:
      - working: "NA"
        agent: "main"
        comment: |
          USER REPORT: Status bar (time / wifi / battery / return chip) is
          overlapping the app header on iOS. App is not reserving the top
          safe-area inset. Fix must be at the root, not per-screen.

          INVESTIGATION:
          - Deps already installed (react-native-safe-area-context 5.6.0,
            expo-status-bar 3.0.9).
          - `SafeAreaProvider` already wraps app root in _layout.tsx.
          - No `SafeAreaView` is imported from `react-native` anywhere —
            all screens already use the correct one from `react-native-safe-area-context`.
          - Every TAB SCREEN correctly wraps its content in
            `<SafeAreaView edges={["top"]}>` — those work.
          - BUG SOURCE: The Modal-based screens (DynamicFormRenderer and
            its inner SignatureCaptureModal) put their coloured green
            header at y=0 with NO inset reservation. React Native Modals
            are a separate presentation context on iOS and do NOT inherit
            the parent's inset — hence the overlap.
          - Root StatusBar was set to `style="dark"` — unreadable text over
            the dark-green header on iOS.

          FIX:
          1. `/app/frontend/app/_layout.tsx`
               `<StatusBar style="dark" />` → `<StatusBar style="light" translucent backgroundColor="transparent" />`.
               Documented rationale inline.
          2. NEW `/app/frontend/src/components/ScreenContainer.tsx`
               Shared root wrapper. Uses `SafeAreaView` from
               `react-native-safe-area-context` with `edges={['top','left','right']}`
               by default. Available for any future screen so we don't
               duplicate the pattern per file.
          3. NEW `/app/frontend/src/components/ScreenHeader.tsx`
               Reusable coloured header component that reserves `insets.top`.
               Exports `HEADER_CONTENT_HEIGHT = 52`. Total header height =
               `HEADER_CONTENT_HEIGHT + insets.top`. Content row is
               vertically centered BELOW the inset.
          4. `/app/frontend/src/components/DynamicFormRenderer.tsx`
               - Imports `useSafeAreaInsets` from `react-native-safe-area-context`
                 and `HEADER_CONTENT_HEIGHT` from ScreenHeader.
               - Main modal header now uses
                 `{paddingTop: insets.top, height: HEADER_CONTENT_HEIGHT + insets.top}`.
                 Title and Submit sit below the inset, vertically centered.
               - Main modal ScrollView content now uses
                 `paddingBottom: 60 + insets.bottom` so the footer submit
                 button never sits under the home indicator.
               - Signature capture sub-modal: same header treatment, plus
                 `paddingBottom: 12 + insets.bottom` on its footer.

          NOT TOUCHED (intentional):
          - PdfViewerModal already correctly wraps in
            `<SafeAreaView edges={["top"]}>` — inset is respected.
          - Tab screens already wrap in `<SafeAreaView edges={["top"]}>` from
            `react-native-safe-area-context` — inset is respected.
          - `_LegacyEmploymentForm.tsx` remains as-is (rollback path, not
            mounted).

          Android: `app.json` already declares `edgeToEdgeEnabled: true`,
          which combined with root `<StatusBar translucent>` gives the
          expected inset behaviour and keeps content out from under the
          status bar / gesture handle.

          Lint clean on all 4 files. Not yet verified via testing_agent.

  - task: "Phase 2 — DynamicFormRenderer + wire-up (legacy form preserved as rollback)"
    implemented: true
    working: true
    file: "/app/frontend/src/components/DynamicFormRenderer.tsx, /app/frontend/src/components/_LegacyEmploymentForm.tsx, /app/frontend/app/(tabs)/documents.tsx"
    stuck_count: 0
    priority: "high"
    needs_retesting: false
    status_history:
      - working: "NA"
        agent: "main"
        comment: |
          Phase 2 frontend feature (frontend Phase 2 only; Phase 1 backend
          parser + /schema endpoint untouched per user directive).

          FILES:
          1. RENAMED /app/frontend/src/components/FillableFormModal.tsx →
             /app/frontend/src/components/_LegacyEmploymentForm.tsx. Kept
             `export function FillableFormModal` symbol for rollback via
             re-import. NOT wired into any screen anymore. Docstring at the
             top warns "Preserved as a rollback for Phase 2".
          2. NEW /app/frontend/src/components/DynamicFormRenderer.tsx —
             schema-driven Modal component (React Native primitives only,
             no HTML, no web form libs). Props:
               { documentId, documentTitle?, visible, onClose, onSubmitted? }
             Fetches GET /api/documents/{documentId}/schema via
             getAuthHeaders(). Field-type mapping matches spec:
                text     → <TextInput>
                checkbox → <Switch> (single) or pressable multi-option
                          group when field.options is populated
                radio    → pressable radio group driven by field.options
                          (defaults to Yes/No when no options)
                combobox → pressable single-select chips
                listbox  → pressable multi-select chips
                signature→ "Signature capture coming soon" placeholder
                          (stores no value; Phase 3)
                button   → skipped entirely
             State: single useState({}) keyed by field_name. No external
             state library. Wrapped in ScrollView + KeyboardAvoidingView.
             Fields grouped by page with a "Page N of M" pill and a
             per-page field count. Header shows total field count. Submit
             button POSTs current state to POST /api/documents/{id}/submissions
             (both header and footer submit for reachability on long forms).
             Handles empty/error/loading states cleanly.
          3. WIRED /app/frontend/app/(tabs)/documents.tsx:
                - Import switched from FillableFormModal to
                  DynamicFormRenderer + (legacy) FillableFormModal from
                  the renamed rollback file. Legacy rendered inside
                  `{false && ...}` guard so the module stays reachable but
                  never mounts.
                - Added a "Fill" button (create-outline icon,
                  testID `fill-doc-<id>`) next to the eye-icon "View"
                  button on every doc card with a file. Tapping it opens
                  the DynamicFormRenderer for that document. The empty
                  state is graceful for PDFs without detectable fields.
                - Legacy tap-on-card behaviour for hardcoded fillable
                  titles (FILLABLE_TITLES) still triggers the same
                  setFormDoc — but now it opens the dynamic renderer
                  instead of the legacy form.

          VERIFICATION LOCALLY:
            • Backend POST /api/documents/{seedDoc}/submissions → 201, row
              in Mongo `submissions` collection with matching document_id.
            • lint clean on both new/renamed frontend files.
            • Backend/frontend both healthy after restart.
          Not yet verified end-to-end via testing_agent (called next).

  - task: "PDF viewer + auth helpers — use Supabase JWT, not stale legacy userToken"
    implemented: true
    working: "NA"
    file: "/app/frontend/src/api/client.ts, /app/frontend/src/components/pdf/PdfViewerModal.tsx, /app/frontend/src/utils/open-file.ts, /app/frontend/app/(tabs)/assistant.tsx, /app/frontend/app/caregiver/[id].tsx, /app/frontend/app/client/[id].tsx"
    stuck_count: 0
    priority: "high"
    needs_retesting: true
    status_history:
      - working: "NA"
        agent: "main"
        comment: |
          ROOT CAUSE #2 (frontend, same user report "PDFs not the same ones loaded"):
          Six frontend callsites bypassed the canonical authHeaders() helper
          and read `AsyncStorage.getItem("userToken")` directly — that's the
          LEGACY JWT key, which is empty when the user is logged in via
          Supabase mode (the default). Consequences in Supabase mode:
            - PdfViewerModal sent no/wrong Authorization header → backend
              returned 401 (HTML/JSON) → modal swallowed the error and the
              iframe rendered the stale previously-loaded blob (literally a
              different document than the one the user tapped). The error
              also masked the unicode-title 500 above.
            - assistant.tsx AI chat sent `Bearer null` → 401 → no streaming.
            - caregiver / client photo upload, openAuthedFile (binder
              downloads) — same pattern.
          FIX: api/client.ts exports two new helpers: `getAuthToken()`
          (returns Supabase access token if a session is live, else legacy
          `userToken`, else null) and `getAuthHeaders()` (full headers map).
          Patched all six callsites:
            • PdfViewerModal.tsx (read + sign paths) — also adds an explicit
              content-type guard so an error response can no longer be
              mistaken for a PDF.
            • utils/open-file.ts (openAuthedFile)
            • app/(tabs)/assistant.tsx
            • app/caregiver/[id].tsx (photo upload)
            • app/client/[id].tsx (photo upload)
          AuthContext.tsx:78 is the only callsite that still reads
          `userToken` directly — that is the legacy-mode bootstrap and is
          correct.

  - task: "Login bug — stale password autofill caused 'Incorrect email or password'"
    implemented: true
    working: true
    file: "/app/frontend/app/(auth)/login.tsx"
    stuck_count: 0
    priority: "high"
    needs_retesting: false
    status_history:
      - working: true
        agent: "testing"
        comment: |
          Iteration 21 verified GREEN end-to-end via Playwright on the preview
          URL (mobile viewport 390x844). All 7 scenarios passed:
            1. Fresh-load bug-repro protection — email auto-filled to
               admin@healthguard.com, password field EMPTY, mode pill says
               "Auth: Supabase · tap to switch".
            2. Supabase login default — typing AdminPassword123! and tapping
               login-submit-button reached the dashboard (stat-clients=1,
               stat-caregivers=12, stat-documents=50).
            3. Legacy login — toggling auth-mode-toggle flipped the pill to
               "Auth: Legacy (MongoDB)"; password field remained empty; typing
               Admin@123 reached the dashboard.
            4. Persisted mode safety (the ORIGINAL BUG) — after Legacy login,
               logout via logout-button, page.reload(), the mode pill still
               said Legacy, email pre-filled, AND the password field was empty
               (NO stale 'AdminPassword123!' carryover). Typing Admin@123
               logged in successfully.
            5. Empty-submit guard — clearing both fields and tapping submit
               showed inline 'Email and password are required'; zero network
               requests fired.
            6. Wrong-password error normalisation — Legacy mode with
               'wrongpassword' surfaced 'Incorrect email or password' (from
               401); Supabase mode with same input also surfaced
               'Incorrect email or password' (normalised from the Supabase
               'Invalid login credentials' / 400 response). Both modes share
               the same UI string.
            7. Regression sanity — Documents tab loaded with 30 delete-doc-*
               cards and 27 view-doc-* eye icons (matches the 38/12 storage_path
               split observed in iteration 20); signout+reload returned to the
               login screen with an empty password.
          The root-cause fix in login.tsx (removing the password autofill at
          mount and only prefilling the email when blank) holds. Marking the
          task working=true, needs_retesting=false.
      - working: "NA"
        agent: "main"
        comment: |
          USER REPORT: "The login feature does not work."
          ROOT CAUSE:
          login.tsx initialised the `password` state from `mode === "supabase"`
          AT MOUNT TIME, but the AuthContext bootstrap effect restores the
          persisted mode from AsyncStorage asynchronously. Sequence:
            1. login.tsx mounts → mode='supabase' (default) → password autofilled
               as 'AdminPassword123!'
            2. AuthContext useEffect resolves storedMode='legacy' → flips mode
            3. login.tsx re-renders showing "Auth: Legacy" pill, but the
               password state is unchanged → still 'AdminPassword123!'
            4. User taps Sign in → POST /api/auth/login with Supabase-style
               password → backend returns 401 "Incorrect email or password".
          The user sees the Legacy pill and a correct-looking obscured field,
          so they (correctly) believe login is broken.
          FIX:
          1. Removed credential auto-fill from `useState` initialisers.
             Email auto-fills only if blank (one-shot useEffect).
             Password ALWAYS starts blank and the user types whichever
             password matches the active mode — no more stale autofill.
          2. Better error messages: distinguishes credential failure from
             network failure and from generic errors (no more silently
             showing "Incorrect email or password" when the real issue is a
             timeout or 500).
          3. Empty-field guard before submit.
          Manual reproduction in Playwright (preview URL) confirmed login
          worked when the form is filled correctly — the bug only manifests
          when the user trusts the (out-of-sync) autofill.

  - task: "Documents UI — gate Open/View button on storage_path (not file_base64)"
    implemented: true
    working: true
    file: "/app/frontend/app/(tabs)/documents.tsx, /app/backend/models.py, /app/backend/server.py, /app/scripts/_backfill_storage_path.py"
    stuck_count: 0
    priority: "medium"
    needs_retesting: false
    status_history:
      - working: true
        agent: "testing"
        comment: |
          Iteration 20 verified GREEN end-to-end.
          BACKEND (pytest 6/6 in test_iteration20_storage_path_gating.py):
            - GET /api/documents returns 50 docs; exact split 38 with non-null
              storage_path / 12 with storage_path=null. The `storage_path` key
              is present on every record (Pydantic Document model exposes it).
            - POST /api/documents with a small base64 PDF returns 200 with
              storage_path='documents/<uuid>.pdf' set on the create response;
              GET /api/documents/{id}/url then returns 200 with url=https://...
              and storage_path matching exactly. DELETE cleans up both stores.
            - Metadata-only doc GET /api/documents/{id}/url returns 404 with
              detail 'No stored file for this document' (intentional contract,
              not flagged as a bug per the review request).
          FRONTEND (Expo web, mobile 390x844, Supabase admin login):
            - Documents tab header reads "50 items".
            - Of 30 cards rendered by FlatList virtualization, 27 carried
              data-testid="view-doc-<id>" and 3 did NOT. The 3 without a view
              button were exactly the seed docs whose storage_path is null
              (TEST_caregiver_other, TEST_caregiver_own, TEST_training_doc) —
              confirming the gate `hasFile = !!(item.storage_path || item.file_base64)`
              wires the eye-icon visibility correctly.
            - Tapping the eye-icon on a doc WITH storage_path opens the
              appropriate viewer (PdfViewerModal for plain PDFs, FillableFormModal
              for the 5 fillable-by-title docs — both are valid "open" actions).
            - Tapping the card body of a doc WITHOUT storage_path does NOT
              open any modal (Pressable.onPress is undefined when hasFile is
              false). Verified with TEST_caregiver_other.
            - Upload flow: tapping + button (testID=add-document-button),
              entering title "phase7_ui_smoke" with category "credential" and
              NO file picked, then tapping Save -> the new doc appears in the
              list with NO view-doc button (hasView=false), matching the
              gating contract. Cleanup via delete-doc-<id> succeeded.
      - working: "NA"
        agent: "main"
        comment: |
          Followup to Phase 6 testing finding about 12 seed docs returning 404 on
          /api/documents/{id}/url. The user explicitly asked to (a) hide the
          Open/View affordance when storage_path is missing, (b) confirm real
          uploads persist a valid storage_path and open correctly.
          Changes:
          1. models.Document gained `storage_path: Optional[str] = None`, so
             /api/documents (list) and /api/documents/{id} (get) now include it.
          2. create_document now uploads to Supabase Storage BEFORE inserting
             into Mongo, then persists storage_path on BOTH Mongo and Postgres
             rows — so the frontend immediately sees the new field.
          3. New backfill script `/app/scripts/_backfill_storage_path.py`
             walked all 50 docs: 38 had file_base64 with no storage_path → all
             uploaded to Storage and patched on both stores. 12 docs are
             metadata-only (no blob) and intentionally remain without
             storage_path.
          4. documents.tsx now computes `hasFile = !!(item.storage_path || item.file_base64)`
             — storage_path is the canonical "viewable" flag; file_base64 is a
             defensive fallback for legacy docs not yet backfilled.
          Backend behavior for /documents/{id}/url is UNCHANGED (still 404 when
          no blob in Storage). Verified via standalone smoke: real upload sets
          storage_path, /url returns signed URL, /stamped serves PDF, DELETE
          removes both stores.

  - task: "Phase 6 — Frontend Supabase JWT cutover with legacy fallback"
    implemented: true
    working: "NA"
    file: "/app/frontend/src/context/AuthContext.tsx, /app/frontend/src/api/client.ts, /app/frontend/app/(auth)/login.tsx"
    stuck_count: 0
    priority: "high"
    needs_retesting: true
    status_history:
      - working: "NA"
        agent: "main"
        comment: |
          AuthContext now defaults to Supabase mode when SUPABASE_CONFIGURED is true
          (always true in this env). api/client.ts already prefers Supabase JWT and
          falls back to legacy token only when no Supabase session.
          Phase 6 hardening:
          1. Cached user (sbUserInfo) in AsyncStorage for snappy cold-start in
             Supabase mode (avoids blank screen while /supabase/me round-trips).
          2. Logout now ALWAYS signs out of Supabase + clears all cached tokens
             regardless of current mode (prevents stale sessions when toggling).
          3. The "Auth mode" toggle pill on the login screen remains visible so
             users can fall back to Legacy (MongoDB) JWT if Supabase has an outage.
          Backend already accepts both Supabase ES256 JWT (via JWKS) and legacy
          HS256 custom JWT in parallel (Phase 2). MS Graph integration unchanged.

metadata:
  created_by: "main_agent"
  version: "1.0"
  test_sequence: 10
  run_ui: false

test_plan:
  current_focus: []
  stuck_tasks: []
  test_all: false
  test_priority: "high_first"

agent_communication:
  - agent: "testing"
    message: |
      Iteration 21 — Login bug verification VERIFIED GREEN.
      Ran 7 Playwright scenarios on the preview URL (mobile 390x844) covering
      fresh-load state, Supabase login, Legacy login, the persisted-mode
      reload safety scenario (the original user-reported bug), empty-submit
      guard, wrong-password normalisation for BOTH modes, regression sanity
      on Documents tab, and clean signout. All 7 PASSED. Backend logs confirm
      a 401 from POST /api/auth/login for the wrong-pwd Legacy case and a 400
      from Supabase for the wrong-pwd Supabase case — both surfaced the same
      normalised UI string "Incorrect email or password". The persisted-mode
      reload test specifically confirmed the password field is EMPTY after
      reload (i.e. the originally buggy stale autofill is gone). Updated
      frontend.tasks[Login bug ...] working=true, needs_retesting=false. No
      retest required.

  - agent: "main"
    message: |
      USER REPORTED LOGIN BUG — FIX APPLIED, NEEDS VERIFICATION.
      Root cause: login.tsx autofilled the password at mount from `mode`,
      but mode was overwritten async by the persisted AsyncStorage value,
      leaving the wrong password in the field. Removed the password autofill
      entirely; email autofills only if empty. Improved error reporting so
      network failures aren't mislabeled as credentials failures.

      PLEASE VERIFY (FRONTEND ONLY — backend is already known good):

      Scenario A — Fresh load, Supabase mode (default), type the correct
      Supabase password manually:
        1. Open /
        2. The email field should already show admin@healthguard.com.
        3. The password field MUST be empty on first load.
        4. Type: AdminPassword123!
        5. Tap Sign in → should land on /(tabs) and render the dashboard.

      Scenario B — Toggle to Legacy mode and sign in:
        1. From the login screen, tap the "Auth: Supabase · tap to switch"
           pill → pill flips to "Auth: Legacy (MongoDB) · tap to switch".
        2. Password field remains empty (no stale autofill).
        3. Type: Admin@123
        4. Tap Sign in → should land on /(tabs) and render dashboard.

      Scenario C — Persistent mode bug (the original report):
        1. From a working signed-in state, sign out (top-right power icon).
        2. After landing on login, toggle to Legacy and type Admin@123 →
           login succeeds. (This sets persisted mode=legacy.)
        3. Sign out again, hard-refresh the page.
        4. CRITICAL: on reload the password field is EMPTY (not pre-filled
           with AdminPassword123!), and the pill correctly says "Legacy".
        5. Type Admin@123 → Sign in → succeeds.
        (Previously this scenario failed because the password autofilled
        with the Supabase password while the mode was legacy.)

      Scenario D — Empty-submit guard:
        1. Clear both fields → tap Sign in → error "Email and password are required".

      Scenario E — Bad password yields the correct error:
        1. Type admin@healthguard.com / wrongpass → Sign in →
           error "Incorrect email or password" (not a generic / network msg).

      Credentials reminder:
        Supabase: admin@healthguard.com / AdminPassword123!
        Legacy:   admin@healthguard.com / Admin@123

  - agent: "main"
    message: |
      UI/DATA FIX: Documents tab now only shows the Open/View button when the
      document has a storage_path (canonical Supabase Storage flag).
      Real uploads via POST /api/documents persist storage_path on BOTH Mongo
      and Postgres immediately. Backfilled 38 pre-existing seed docs that had
      file_base64 but no storage_path. The 12 metadata-only docs (HIPAA
      Privacy Policy etc., no actual PDF) intentionally show no Open button.

      PLEASE VERIFY (frontend only, on Expo web at /):
      1. Log in (Supabase default mode) as admin@healthguard.com / AdminPassword123!
      2. Open the Documents tab.
      3. Confirm:
         a. Seed docs WITH a backfilled PDF (e.g. titles like
            "Client Authorization Form", "Background Check", "TB Test", etc.)
            show a green doc-attach icon AND the eye-icon "view" button
            (testID `view-doc-<id>`).
         b. The 12 metadata-only docs (titles like "02 - HIPAA Privacy
            Policy", "03 - Bloodborne Pathogens & Infection Control",
            "TEST_caregiver_other", etc.) DO NOT show the view button — only
            the title/category row + delete icon. Tapping the card itself
            should be a no-op (Pressable onPress is undefined when !hasFile).
         c. Tapping the view button on a doc WITH storage_path opens
            PdfViewerModal and renders the stamped PDF.
         d. Upload a fresh PDF via the + button: the new doc should appear in
            the list AND show the view button immediately (no refresh needed).
            Tap to open — modal should display the PDF.
      Backend test (optional sanity):
      - GET /api/documents returns storage_path on each item (38 set, 12 null).
      - POST /api/documents with file_base64 returns storage_path in response.
      - GET /api/documents/{id}/url returns signed URL for docs with
        storage_path; returns 404 for the 12 metadata-only docs (this is
        intentional per backend contract).

      Credentials reminder:
        Supabase: admin@healthguard.com / AdminPassword123!
        Legacy:   admin@healthguard.com / Admin@123

  - agent: "main"
    message: |
      Phase 5 Slice J + Phase 6 complete.
      - Slice J: MS Graph /api/ms/* dual-writes to Postgres public.integrations.
      - Phase 6: Frontend now defaults to Supabase JWT. Legacy JWT login is still
        reachable via the toggle pill (testID="auth-mode-toggle") on the login
        screen so users can fall back if Supabase has an outage.
      Backend still accepts both auth modes (dual-mode auth from Phase 2).
      Please run a COMPREHENSIVE backend + frontend regression covering:
        Backend:
          • /api/auth/login + /api/auth/me (legacy + Supabase JWT)
          • /api/supabase/me bridge
          • /api/clients CRUD, /api/caregivers, /api/assignments
          • /api/documents (upload + signed URL via /api/documents/{id}/url)
          • /api/chat/threads + /api/chat/messages
          • /api/policies/acknowledge + /api/training + completions
          • /api/onboarding
          • /api/packets/share + /api/packets/{token}/sign/{doc_id}
          • /api/ms/email-recipients + /api/ms/disconnect (dual-write to PG)
        Frontend:
          • Default login flow uses Supabase mode and lands on /(tabs)
          • Login mode toggle pill switches to Legacy and authenticates with
            admin@healthguard.com / Admin@123
          • After login: tabs render, clients list loads, chat threads load,
            training and policies render, packet share screen reachable.
          • Logout clears both Supabase and Legacy sessions.
      Admin Supabase creds: admin@healthguard.com / AdminPassword123!
      Admin Legacy creds:   admin@healthguard.com / Admin@123
      Caregiver Supabase:   caregiver@healthguard.com / Caregiver123!
      Caregiver Legacy:     caregiver@healthguard.com / Caregiver@123

  - agent: "testing"
    message: |
      Iteration 23 — Phase 1 backend PDF field extraction: **ALL 21 pytest cases PASS** (100%).
      Test file: /app/backend/tests/test_iteration23_pdf_field_extraction.py
      JUnit report: /app/test_reports/pytest/iteration23_results.xml

      Coverage vs. spec:
        • Module imports (extract_acroform_fields / extract_fields_from_text / parse_pdf) — PASS
        • parse_pdf on SKilleRN-Fillable.pdf → 182 fields, all keys present,
          types = {text, checkbox, radio, signature}, positions valid rects,
          every source == "acroform", "Last Name" text field found on page 1 — PASS
        • extract_acroform_fields == parse_pdf (AcroForm path preferred) — PASS
        • Flat-PDF fallback (pymupdf-generated /tmp/flat.pdf) — widgets empty,
          text-heuristic returns Full Name + Date of Birth as text with
          source='text-heuristic', parse_pdf falls back correctly — PASS
        • Upload hook (POST /api/documents with PDF base64) → 200 + Mongo
          field_schemas row with field_count=182 source='acroform' — PASS
        • GET /api/documents/{id}/schema → 200 envelope {document_id,
          field_count=182, source='acroform', fields[...], extracted_at,
          parser_version='1.0'} — every field has field_name/field_type/page/
          position{x0,y0,x1,y1}/options[list]/required[bool]/value/source — PASS
        • GET /api/documents/does-not-exist-123/schema → 404 with detail
          "Document not found" — PASS
        • GET /schema without bearer token → 401 (get_current_user) — PASS
        • Lazy backfill: delete Mongo field_schemas row → re-GET still 200
          with field_count=182 (re-extracted from file_base64) — PASS
        • Non-PDF (image/png) doc → GET /schema returns 200 field_count=0
          source='empty' (no 500 crash) — PASS
        • Regression: /api/documents/{seeded 01-employment}/form-schema
          still 200 has_form=true schema present; /schema endpoint is
          separate and does not shadow /form-schema — PASS
        • Metadata-only POST (no file_base64) → 200, no crash — PASS
        • DELETE /api/documents/{id} → 200 — PASS

      No backend regressions observed. requirements.txt already contains
      pymupdf==1.28.0 and reportlab==5.0.0. Frontend intentionally not touched.
      Phase 1 backend can be marked complete.

  - agent: "testing"
    message: |
      Iteration 24 — Phase 2 (POST /api/documents/{id}/submissions + DynamicFormRenderer)
      end-to-end verification: **BACKEND 8/8 pytest PASS + FRONTEND UI green.**
      Test file: /app/backend/tests/test_iteration24_dynamic_submissions.py
      JUnit: /app/test_reports/pytest/iteration24_results.xml

      Backend coverage:
        • POST /submissions with 3 populated values against seeded doc
          16745d1d-22a7-4912-adbf-acc588192a01 → 201
          {id, document_id, submitted_at, field_count:3} — PASS
        • POST with values={} → 201 field_count=0 — PASS
        • Unknown document id → 404 detail "Document not found" — PASS
        • Missing bearer token → 403 — PASS
        • Optional signature_b64 accepted and stored verbatim in Mongo — PASS
        • Mongo `submissions` row exists with matching id and shape
          {document_id, document_title, submitted_by, submitter_email,
           submitter_role, values, signature_b64, submitted_at} — PASS
        • signature_b64 stored as null when omitted — PASS
        • REGRESSION: GET /api/documents/{seededDoc}/schema still returns
          valid envelope (field_count=27, source=acroform, parser_version=1.0) — PASS

      Frontend coverage (Supabase login admin@healthguard.com):
        • Login → Documents tab → SKilleRN-Fillable.pdf card shows THREE
          icons (view / fill-doc / push / delete) as expected — PASS
        • fill-doc-<id> tap opens Modal with header "SKilleRN-Fillable",
          subtitle "182 fields · 4 pages", Submit button (dyn-form-submit)
          and "PAGE 1 OF 4" pill visible — PASS
        • Modal renders 142 dyn-field TextInputs, 12 dyn-radio chips,
          33 dyn-check chips, and 1 "Signature capture coming soon" placeholder — PASS
        • Fill 3 TextInputs (Date/Last Name/First), toggle 1 checkbox,
          select 1 radio → tap dyn-form-submit → POST /submissions 201 →
          Modal closes; Mongo row confirms values persisted
          (Date=2026-07-03, Last Name=Smith, First=John,
           "Are you legally qualified…"=true, radio="Yes") — PASS
        • REGRESSION: react-native primitives only — no <input>, <form>,
          <div>, <span>, react-router-dom, @mui, tailwindcss, framer-motion
          in DynamicFormRenderer.tsx — PASS (grep clean)
        • REGRESSION: _LegacyEmploymentForm.tsx preserved, exports
          FillableFormModal, docstring warns "Do NOT delete", rendered
          only inside `{false && (...)}` guard in documents.tsx — PASS
        • KeyboardAvoidingView + ScrollView wired at DynamicFormRenderer:434 — PASS
        • eye-icon PdfViewerModal on doc cards still works (regression) — PASS

      Minor issue (non-blocking, main agent):
        1. React key-collision warnings ("Encountered two children with
           the same key") logged repeatedly when the modal is mounted for
           SKilleRN-Fillable. The state key uses `field_name` which is
           already deduped server-side ("YES", "YES_2", …), so the outer
           <View key={f.field_name}> is unique — the duplicate keys are
           inside f.options.map((opt) => <Pressable key={opt}>) when a
           schema field has repeated option strings (e.g. two identical
           "YES" options). Suggest key={`${opt}-${idx}`}. Not a functional
           bug — form still submits and values persist correctly.

      Divergence from spec (non-blocking):
        2. Metadata-only doc case in the test list ("Tap fill-doc-<id>
           on a metadata-only doc, no PDF, e.g. '02 - HIPAA Privacy Policy'")
           is not reachable via UI because the fill-doc button in
           documents.tsx is gated by `hasFile` (line 382). This is by
           design (matches the eye-icon "view" button gating) and any
           PDF that lacks fields still shows the graceful empty state
           "No fillable fields were detected in this PDF" via the
           renderer, verified against uploaded PNG-only test docs in
           iteration 23. Leaving as-is.

      test_result.md updated: Phase 2 frontend task marked working=true
      needs_retesting=false.
