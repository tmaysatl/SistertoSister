# 🏥 Home Health Agency App — Replication Playbook

> Master checklist to spin up this compliance-management app for any new home health agency.
> Source-of-truth app: **Sister to Sister, PHCP** (June 2026 build).

---

## 1️⃣ Accounts & Credentials Required (PER NEW AGENCY)

### A. Mandatory — cannot launch without these

| # | Account / Service | Purpose | Cost | Who creates it |
|---|---|---|---|---|
| 1 | **Apple Developer** | iOS App Store publishing | $99 / year | Agency owner |
| 2 | **Google Play Console** | Android Play Store publishing | $25 one-time | Agency owner |
| 3 | **Firebase project** (Google Cloud) | Push notifications (FCM) | Free tier | Agency owner |
| 4 | **Domain + privacy-policy hosting** | App Store/Play Store requirement | ~$12 / yr | Agency owner |
| 5 | **Emergent account** (this platform) | Builds, hosts backend + DB | Subscription | Already in place |
| 6 | **Agency Microsoft 365 admin email** | Audit-binder export to Outlook / OneDrive | Varies ($0–$15/user/mo) | Agency owner |

### B. Recommended (better UX, not strictly required to launch)

| # | Account / Service | Purpose | Cost |
|---|---|---|---|
| 7 | Microsoft 365 Business Basic | Adds OneDrive storage (no email attachments) | $7.20 / user / mo |
| 8 | Custom domain email (e.g. info@yourhealth.com) | Trust signal in shareable packets | Included w/ M365 |
| 9 | Logo design (vector PNG, transparent) | Branding inside PDFs + watermarks | Variable |

### C. Per-agency credentials inventory — fill in for each deployment

```
AGENCY NAME: ______________________
BRAND COLORS: primary #____ secondary #____ tertiary #____
LOGO URL: ______________________
PRIVACY POLICY URL: ______________________
SUPPORT EMAIL: ______________________

— Apple Developer —
Team ID: ______________________
App Bundle ID: com.________.________
Apple ID email: ______________________
App-specific password: ______________________

— Google Play Console —
Developer account email: ______________________
Service account JSON: (attach file)
Package name: com.________.________

— Firebase (push) —
Project ID: ______________________
google-services.json: (attach file)
APNs auth key (.p8): (attach file)
APNs Key ID: ______________________

— Microsoft 365 / Azure —
Tenant ID: ______________________
Client ID (app registration): ______________________
Client secret VALUE (not Secret ID): ______________________
Redirect URI: https://<prod-domain>/api/ms/callback
Admin email that connects: ______________________

— App admin seed account —
Admin email: ______________________
Admin password: ______________________ (rotate after first login)
```

---

## 2️⃣ Systems & Integrations Used in This App

| Layer | Tech | Why we use it |
|---|---|---|
| **Frontend** | React Native (Expo Router, SDK 53) | Cross-platform iOS + Android + Web from one codebase |
| **Backend** | FastAPI (Python 3.11) | Async APIs, auto-OpenAPI docs |
| **Database** | MongoDB (Motor async driver) | Document store fits flexible compliance docs |
| **Auth** | JWT (jose) + bcrypt | Email/password, admin & caregiver roles |
| **AI assistant** | Claude Sonnet 4.5 via Emergent LLM Key | In-app compliance Q&A |
| **PDF watermarking + e-sign** | reportlab + pypdf + react-native-signature-canvas | Audit-trail stamps and in-modal signatures |
| **Push notifications** | Emergent Push (FCM + APNs under the hood) | Chat + shift change alerts |
| **Microsoft Graph (MSAL)** | OAuth2 + offline_access refresh tokens | Monthly Audit Binder → OneDrive or Outlook attachment |
| **Scheduler** | APScheduler (cron) | Runs the monthly export job |
| **File hosting** | Base64 in Mongo for now (simple) | Replace with S3/Azure Blob at scale |

---

## 3️⃣ Per-Agency Tweak Checklist (What To Change for Each New Client)

These are the *only* files / values that need editing per agency. The rest of the codebase stays identical.

### Frontend
- [ ] `/app/frontend/src/theme.ts` → `BRAND_NAME`, color tokens
- [ ] `/app/frontend/app.json` → `name`, `slug`, `ios.bundleIdentifier`, `android.package`
- [ ] `/app/frontend/assets/` → logo, app icon, splash screen
- [ ] `/app/frontend/google-services.json` → new agency's Firebase project file
- [ ] Login screen demo creds line in `/app/frontend/app/(auth)/login.tsx` — remove or update

### Backend
- [ ] `/app/backend/.env` → swap every `MS_*` value, agency-specific JWT secret, MongoDB DB name, support email
- [ ] `/app/backend/core/pdf_utils.py` → `LOGO_URL` (and the watermark text "SISTER TO SISTER, PHCP")
- [ ] `/app/backend/server.py` → seed-admin name/email (function `seed_admin`)
- [ ] `/app/backend/models.py` → if agency uses different onboarding-doc titles, update the seed-templates list

### Microsoft Azure
- [ ] New app registration (each tenant needs its own)
- [ ] Add redirect URI `https://<prod-domain>/api/ms/callback`
- [ ] Add API permissions: `Files.ReadWrite Mail.ReadWrite Mail.Send User.Read offline_access` → grant admin consent

### App Store / Play Store listings
- [ ] New app icons (1024×1024 iOS, 512×512 Android adaptive)
- [ ] 5–8 screenshots per device class
- [ ] Description, keywords, category = "Medical" or "Business"
- [ ] Privacy policy URL (must mention SSNs, health data, caregiver credentials)
- [ ] Content rating: Everyone / Medical

**Estimated tweak time per new agency: 2–3 hours** of config + ~1 hour of asset prep + the standard 3–7 day launch window.

---

## 4️⃣ Build Steps — How We Got to This Point

A short narrative of how this current build was assembled, in order:

1. **Foundation** — auth (JWT, bcrypt), users/clients/caregivers CRUD, MongoDB schema, FastAPI + Expo Router skeleton.
2. **Custom branding** — Sister to Sister PHCP theme, logo, color tokens, custom audit-binder watermarking.
3. **Document vault** — 13 client + 14 caregiver onboarding PDFs seeded as templates; categories: client_onboarding / caregiver_onboarding / credentials / training / policy.
4. **Watermark + e-sign** — reportlab stamps every viewed PDF with viewer name + timestamp + diagonal brand watermark; in-modal signature canvas (`react-native-signature-canvas`) embeds the signature back into the PDF on submit.
5. **Public packet share** — admin generates `/packet/[token]` link; surveyors view + sign without logging in.
6. **One-tap Audit Binder** — `/api/reports/audit-binder` concatenates all docs + audit trail into one ~28MB PDF.
7. **In-app chat** — admin ↔ caregiver direct messages with unread counts.
8. **Push notifications** — Emergent Push wired with `google-services.json`; works on real device builds (Expo Go limits).
9. **Caregiver/client drill-down pages** — per-user document lists, photos, status badges.
10. **Mutual assignment UI** — chip pickers on both profiles + idempotent backend.
11. **Document push** — admin pushes any doc (PDF, photo, training) to specific caregivers/clients.
12. **Schedule tab** — week + day view; one-off and recurring (weekday picker) shifts; clock-in/out preserved; admin edit/cancel triggers push notification.
13. **Microsoft 365 export** — OAuth (MSAL); monthly cron via APScheduler; auto-fallback to Outlook attachment if tenant lacks SharePoint license.
14. **Refactor** — split monolithic `server.py` into `core/` + `models.py` + `routers/` for maintainability.

---

## 5️⃣ Pre-Launch Checklist (Reusable for Every Agency)

```
□ Brand assets dropped into /app/frontend/assets/
□ theme.ts updated (name + colors)
□ app.json updated (name, slug, bundle IDs)
□ google-services.json placed for new Firebase project
□ Backend .env updated with new Mongo DB name + MS_* vars
□ Audit-binder watermark text updated in pdf_utils.py
□ Seed-admin email/name updated in server.py
□ Apple Developer account active, certs generated
□ Google Play Console account active, keystore generated/stored
□ Azure app registered, consented, redirect URI added
□ Privacy policy URL live and linked
□ App icons + screenshots ready (1024² iOS, 512² Android)
□ Test admin login + caregiver login end-to-end
□ Test audit binder export → OneDrive or Outlook
□ Test push notification on a real device build
□ Click Emergent "Publish" button
□ Wait for review: 1–3 days iOS, 1–3 days Android
□ Approved → live on stores 🎉
```

---

## 6️⃣ Quick Cost Summary (Per Agency)

| Item | One-time | Recurring |
|---|---|---|
| Apple Developer | — | $99/yr |
| Google Play Console | $25 | — |
| Domain (privacy policy) | — | $12/yr |
| Microsoft 365 Business Basic (optional for OneDrive) | — | $7.20/user/mo |
| Logo / icons (if outsourced) | $50–500 | — |
| Emergent subscription | — | as-is |
| **Total typical first year** | **~$75** | **~$110/yr** (without M365 upgrade) |

---

## 7️⃣ Known Limitations / "Mocked" pieces to track

- **Push key** (`EMERGENT_PUSH_KEY`) is a placeholder until the Emergent Publish flow swaps in the real one.
- **EVV** (Electronic Visit Verification) is a manual clock-in/out stub — replace with a true EVV vendor (HHAeXchange, Sandata, etc.) when needed.
- **Files** stored as base64 in MongoDB — fine for an MVP, but at >100 agencies or >50GB you'll want S3/Azure Blob.
- **server.py** still has some inline routes (auth/clients/documents/chat/packets/audit-binder/training/onboarding/assistant) — modular refactor partially done.

---

*Generated June 2026 · Last updated when MS-Graph email-attachment fallback was verified working.*
