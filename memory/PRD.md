# HealthGuard Compliance — PRD

## What it does
A React Native Expo mobile app for home health agency owners to keep all
audit-required documents and folders (client, caregiver, onboarding,
training, policies) organized and audit-ready. Caregivers get their own
limited view of assigned clients, onboarding steps, and training to complete.

## Roles
- **Admin (Agency Owner)** — manages everything: clients, caregivers,
  assignments, documents, training, onboarding steps.
- **Caregiver** — views assigned clients, onboarding checklist, training
  library, and the AI compliance assistant.

## Features
- JWT email/password authentication with role selection at signup
- Dashboard with **Audit Readiness %** + overview tiles + pending actions
- Document Vault: upload PDFs/images, organize by category, delete
- Team: clients CRUD, caregiver listing, caregiver↔client assignments
- Onboarding step checklist per caregiver (admin assigns, caregiver checks off)
- Training Library: upload videos/PDFs, mark complete
- AI Compliance Assistant (Claude Sonnet 4.5) — streaming SSE chat

## Tech
- Backend: FastAPI + MongoDB (Motor)
- Frontend: Expo Router (file-based), React Native, expo-image
- AI: emergentintegrations + Claude `claude-sonnet-4-5-20250929` via
  Emergent Universal Key
- Auth: JWT (HS256) + bcrypt via passlib

## Seeded Demo Accounts
- `admin@healthguard.com` / `Admin@123`
- `caregiver@healthguard.com` / `Caregiver@123`
