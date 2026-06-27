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