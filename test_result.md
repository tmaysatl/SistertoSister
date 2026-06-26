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
  current_focus:
    - "Phase 6 — Frontend Supabase JWT cutover with legacy fallback"
    - "Phase 5 Slice J — MS Graph integrations dual-write (Mongo + Postgres)"
  stuck_tasks: []
  test_all: false
  test_priority: "high_first"

agent_communication:
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