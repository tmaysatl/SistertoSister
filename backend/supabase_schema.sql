-- ================================================================
-- HealthGuard / PHCP — Supabase Postgres Schema (Phase 1)
-- Generated for migration from MongoDB
-- ================================================================

-- --- Extensions ---------------------------------------------------
create extension if not exists "pgcrypto";
create extension if not exists "uuid-ossp";

-- ================================================================
-- 1. PROFILES (linked to auth.users)
-- ================================================================
create table if not exists public.profiles (
  id            uuid primary key references auth.users(id) on delete cascade,
  email         text unique not null,
  name          text not null default '',
  role          text not null default 'caregiver' check (role in ('admin','caregiver')),
  photo_base64  text,
  created_at    timestamptz not null default now(),
  updated_at    timestamptz not null default now()
);
create index if not exists profiles_role_idx on public.profiles(role);

-- Trigger: auto-create profile on new auth user
create or replace function public.handle_new_user()
returns trigger
language plpgsql
security definer set search_path = public
as $$
begin
  insert into public.profiles (id, email, name, role)
  values (
    new.id,
    new.email,
    coalesce(new.raw_user_meta_data->>'name', split_part(new.email,'@',1)),
    coalesce(new.raw_user_meta_data->>'role', 'caregiver')
  )
  on conflict (id) do nothing;
  return new;
end;
$$;

drop trigger if exists on_auth_user_created on auth.users;
create trigger on_auth_user_created
  after insert on auth.users
  for each row execute function public.handle_new_user();

-- ================================================================
-- 2. CLIENTS
-- ================================================================
create table if not exists public.clients (
  id            uuid primary key default gen_random_uuid(),
  name          text not null,
  address       text default '',
  phone         text default '',
  notes         text default '',
  photo_base64  text,
  created_at    timestamptz not null default now()
);
create index if not exists clients_name_idx on public.clients(name);

-- ================================================================
-- 3. DOCUMENTS  (file_base64 -> storage_path on Supabase Storage)
-- ================================================================
create table if not exists public.documents (
  id            uuid primary key default gen_random_uuid(),
  title         text not null,
  category      text not null check (category in (
                  'client','caregiver',
                  'client_onboarding','caregiver_onboarding',
                  'credential','training','policy'
                )),
  owner_id      uuid,
  owner_type    text default 'agency' check (owner_type in ('client','caregiver','agency')),
  storage_path  text,                       -- Supabase Storage path; null until uploaded
  mime_type     text default 'application/pdf',
  notes         text default '',
  uploaded_by   uuid references public.profiles(id) on delete set null,
  uploaded_at   timestamptz not null default now(),
  expires_at    timestamptz,
  seq           integer,
  is_template   boolean not null default false,
  meta          jsonb not null default '{}'::jsonb   -- form schemas, signature info, etc.
);
create index if not exists documents_category_idx on public.documents(category);
create index if not exists documents_owner_idx on public.documents(owner_id, owner_type);
create index if not exists documents_template_idx on public.documents(is_template);

-- ================================================================
-- 4. ASSIGNMENTS  (caregiver <-> client)
-- ================================================================
create table if not exists public.assignments (
  id            uuid primary key default gen_random_uuid(),
  caregiver_id  uuid not null references public.profiles(id) on delete cascade,
  client_id     uuid not null references public.clients(id) on delete cascade,
  schedule      text default '',
  notes         text default '',
  created_at    timestamptz not null default now(),
  unique (caregiver_id, client_id)
);
create index if not exists assignments_caregiver_idx on public.assignments(caregiver_id);
create index if not exists assignments_client_idx on public.assignments(client_id);

-- ================================================================
-- 5. SHIFTS  (scheduling + manual clock in/out)
-- ================================================================
create table if not exists public.shifts (
  id              uuid primary key default gen_random_uuid(),
  caregiver_id    uuid not null references public.profiles(id) on delete cascade,
  client_id       uuid not null references public.clients(id) on delete cascade,
  kind            text not null default 'one_off' check (kind in ('recurring','one_off')),
  date            date,
  weekdays        text[],
  recurring_until date,
  parent_shift_id uuid references public.shifts(id) on delete cascade,
  start_time      text not null,
  end_time        text not null,
  notes           text default '',
  service_type    text default '',
  status          text not null default 'scheduled' check (status in ('scheduled','in_progress','completed','cancelled')),
  clocked_in_at   timestamptz,
  clocked_out_at  timestamptz,
  clock_location  jsonb,
  created_by      uuid references public.profiles(id) on delete set null,
  created_at      timestamptz not null default now(),
  updated_at      timestamptz default now()
);
create index if not exists shifts_caregiver_idx on public.shifts(caregiver_id);
create index if not exists shifts_client_idx on public.shifts(client_id);
create index if not exists shifts_date_idx on public.shifts(date);
create index if not exists shifts_status_idx on public.shifts(status);

-- ================================================================
-- 6. CHAT MESSAGES (channel-style, group rooms)
-- ================================================================
create table if not exists public.chat_messages (
  id           uuid primary key default gen_random_uuid(),
  channel      text not null default 'general',
  sender_id    uuid not null references public.profiles(id) on delete cascade,
  sender_name  text not null,
  message      text not null,
  created_at   timestamptz not null default now()
);
create index if not exists chat_messages_channel_idx on public.chat_messages(channel, created_at desc);

-- ================================================================
-- 7. CHAT DMS (direct messages between two users)
-- ================================================================
create table if not exists public.chat_dms (
  id           uuid primary key default gen_random_uuid(),
  sender_id    uuid not null references public.profiles(id) on delete cascade,
  recipient_id uuid not null references public.profiles(id) on delete cascade,
  sender_name  text not null,
  message      text not null,
  read         boolean not null default false,
  created_at   timestamptz not null default now()
);
create index if not exists chat_dms_pair_idx on public.chat_dms(sender_id, recipient_id, created_at desc);
create index if not exists chat_dms_recipient_idx on public.chat_dms(recipient_id, read);

-- ================================================================
-- 8. CLIENT TASKS
-- ================================================================
create table if not exists public.client_tasks (
  id            uuid primary key default gen_random_uuid(),
  client_id     uuid not null references public.clients(id) on delete cascade,
  title         text not null,
  description   text default '',
  seq           integer,
  completed     boolean not null default false,
  completed_at  timestamptz,
  created_at    timestamptz not null default now()
);
create index if not exists client_tasks_client_idx on public.client_tasks(client_id);

-- ================================================================
-- 9. DOCUMENT VIEWS  (audit log of who opened what)
-- ================================================================
create table if not exists public.document_views (
  id           uuid primary key default gen_random_uuid(),
  document_id  uuid not null references public.documents(id) on delete cascade,
  viewer_id    uuid references public.profiles(id) on delete set null,
  viewer_name  text,
  viewed_at    timestamptz not null default now(),
  context      jsonb not null default '{}'::jsonb
);
create index if not exists document_views_doc_idx on public.document_views(document_id, viewed_at desc);

-- ================================================================
-- 10. INTEGRATIONS  (MS Graph tokens etc.)
-- ================================================================
create table if not exists public.integrations (
  id            uuid primary key default gen_random_uuid(),
  provider      text not null unique,         -- 'microsoft_graph', etc.
  user_id       uuid references public.profiles(id) on delete cascade,
  config        jsonb not null default '{}'::jsonb,
  tokens        jsonb not null default '{}'::jsonb,   -- store access_token, refresh_token, expires_at
  connected_at  timestamptz default now(),
  updated_at    timestamptz default now()
);
create index if not exists integrations_provider_idx on public.integrations(provider);

-- ================================================================
-- 11. ONBOARDING PROGRESS
-- ================================================================
create table if not exists public.onboarding_progress (
  id            uuid primary key default gen_random_uuid(),
  caregiver_id  uuid not null references public.profiles(id) on delete cascade,
  title         text not null,
  description   text default '',
  completed     boolean not null default false,
  completed_at  timestamptz,
  created_at    timestamptz not null default now()
);
create index if not exists onboarding_caregiver_idx on public.onboarding_progress(caregiver_id);

-- ================================================================
-- 12. PACKET SHARES (public sharing tokens)
-- ================================================================
create table if not exists public.packet_shares (
  id            uuid primary key default gen_random_uuid(),
  token         text not null unique,
  caregiver_id  uuid references public.profiles(id) on delete cascade,
  client_id     uuid references public.clients(id) on delete cascade,
  doc_ids       uuid[] not null default '{}',
  title         text not null,
  created_by    uuid references public.profiles(id) on delete set null,
  expires_at    timestamptz,
  created_at    timestamptz not null default now(),
  view_count    integer not null default 0
);
create index if not exists packet_shares_token_idx on public.packet_shares(token);

-- ================================================================
-- 13. POLICY ACKNOWLEDGMENTS
-- ================================================================
create table if not exists public.policy_acknowledgments (
  id            uuid primary key default gen_random_uuid(),
  policy_id     text not null,                          -- slug or doc id
  policy_title  text not null,
  user_id       uuid not null references public.profiles(id) on delete cascade,
  acknowledged_at timestamptz not null default now(),
  meta          jsonb not null default '{}'::jsonb,
  unique (policy_id, user_id)
);
create index if not exists policy_acks_user_idx on public.policy_acknowledgments(user_id);

-- ================================================================
-- 14. TRAINING MODULES
-- ================================================================
create table if not exists public.training_modules (
  id            uuid primary key default gen_random_uuid(),
  title         text not null,
  description   text default '',
  storage_path  text,                  -- video/material in storage
  mime_type     text default 'video/mp4',
  required      boolean not null default true,
  created_at    timestamptz not null default now()
);

-- ================================================================
-- 15. TRAINING COMPLETIONS
-- ================================================================
create table if not exists public.training_completions (
  id            uuid primary key default gen_random_uuid(),
  training_id   uuid not null references public.training_modules(id) on delete cascade,
  caregiver_id  uuid not null references public.profiles(id) on delete cascade,
  completed_at  timestamptz not null default now(),
  unique (training_id, caregiver_id)
);
create index if not exists training_completions_caregiver_idx on public.training_completions(caregiver_id);

-- ================================================================
-- ROW LEVEL SECURITY
-- ================================================================
-- Enable RLS on every public.* table
alter table public.profiles                enable row level security;
alter table public.clients                 enable row level security;
alter table public.documents               enable row level security;
alter table public.assignments             enable row level security;
alter table public.shifts                  enable row level security;
alter table public.chat_messages           enable row level security;
alter table public.chat_dms                enable row level security;
alter table public.client_tasks            enable row level security;
alter table public.document_views          enable row level security;
alter table public.integrations            enable row level security;
alter table public.onboarding_progress     enable row level security;
alter table public.packet_shares           enable row level security;
alter table public.policy_acknowledgments  enable row level security;
alter table public.training_modules        enable row level security;
alter table public.training_completions    enable row level security;

-- Helper: is current user an admin? (based on profile)
create or replace function public.is_admin()
returns boolean
language sql stable security definer set search_path = public
as $$
  select exists (
    select 1 from public.profiles
    where id = auth.uid() and role = 'admin'
  );
$$;

-- --- Profiles policies ---
drop policy if exists "profiles_self_select" on public.profiles;
create policy "profiles_self_select" on public.profiles
  for select to authenticated
  using (id = auth.uid() or public.is_admin());

drop policy if exists "profiles_self_update" on public.profiles;
create policy "profiles_self_update" on public.profiles
  for update to authenticated
  using (id = auth.uid() or public.is_admin())
  with check (id = auth.uid() or public.is_admin());

drop policy if exists "profiles_admin_all" on public.profiles;
create policy "profiles_admin_all" on public.profiles
  for all to authenticated
  using (public.is_admin())
  with check (public.is_admin());

-- --- Clients policies ---
drop policy if exists "clients_admin_all" on public.clients;
create policy "clients_admin_all" on public.clients
  for all to authenticated
  using (public.is_admin())
  with check (public.is_admin());

drop policy if exists "clients_caregiver_assigned_read" on public.clients;
create policy "clients_caregiver_assigned_read" on public.clients
  for select to authenticated
  using (exists (
    select 1 from public.assignments a
    where a.client_id = clients.id and a.caregiver_id = auth.uid()
  ));

-- --- Documents policies ---
drop policy if exists "documents_admin_all" on public.documents;
create policy "documents_admin_all" on public.documents
  for all to authenticated
  using (public.is_admin())
  with check (public.is_admin());

drop policy if exists "documents_caregiver_own_read" on public.documents;
create policy "documents_caregiver_own_read" on public.documents
  for select to authenticated
  using (
    -- caregiver can see their own caregiver docs
    (owner_type = 'caregiver' and owner_id = auth.uid())
    -- or docs for clients they're assigned to
    or (owner_type = 'client' and exists (
      select 1 from public.assignments a
      where a.client_id = documents.owner_id and a.caregiver_id = auth.uid()
    ))
    -- or any policy / training / template doc
    or category in ('policy','training','client_onboarding','caregiver_onboarding')
    or is_template = true
  );

-- --- Assignments policies ---
drop policy if exists "assignments_admin_all" on public.assignments;
create policy "assignments_admin_all" on public.assignments
  for all to authenticated
  using (public.is_admin())
  with check (public.is_admin());

drop policy if exists "assignments_caregiver_own_read" on public.assignments;
create policy "assignments_caregiver_own_read" on public.assignments
  for select to authenticated
  using (caregiver_id = auth.uid());

-- --- Shifts policies ---
drop policy if exists "shifts_admin_all" on public.shifts;
create policy "shifts_admin_all" on public.shifts
  for all to authenticated
  using (public.is_admin())
  with check (public.is_admin());

drop policy if exists "shifts_caregiver_own_rw" on public.shifts;
create policy "shifts_caregiver_own_rw" on public.shifts
  for all to authenticated
  using (caregiver_id = auth.uid())
  with check (caregiver_id = auth.uid());

-- --- Chat policies ---
drop policy if exists "chat_messages_auth_read" on public.chat_messages;
create policy "chat_messages_auth_read" on public.chat_messages
  for select to authenticated using (true);

drop policy if exists "chat_messages_auth_insert" on public.chat_messages;
create policy "chat_messages_auth_insert" on public.chat_messages
  for insert to authenticated with check (sender_id = auth.uid());

drop policy if exists "chat_dms_participant" on public.chat_dms;
create policy "chat_dms_participant" on public.chat_dms
  for all to authenticated
  using (sender_id = auth.uid() or recipient_id = auth.uid())
  with check (sender_id = auth.uid());

-- --- Client tasks (admins only manage; caregivers read for assigned clients) ---
drop policy if exists "client_tasks_admin_all" on public.client_tasks;
create policy "client_tasks_admin_all" on public.client_tasks
  for all to authenticated
  using (public.is_admin())
  with check (public.is_admin());

drop policy if exists "client_tasks_caregiver_assigned_rw" on public.client_tasks;
create policy "client_tasks_caregiver_assigned_rw" on public.client_tasks
  for all to authenticated
  using (exists (
    select 1 from public.assignments a
    where a.client_id = client_tasks.client_id and a.caregiver_id = auth.uid()
  ))
  with check (exists (
    select 1 from public.assignments a
    where a.client_id = client_tasks.client_id and a.caregiver_id = auth.uid()
  ));

-- --- Document views: admins read all; users insert their own ---
drop policy if exists "document_views_admin_read" on public.document_views;
create policy "document_views_admin_read" on public.document_views
  for select to authenticated using (public.is_admin() or viewer_id = auth.uid());

drop policy if exists "document_views_self_insert" on public.document_views;
create policy "document_views_self_insert" on public.document_views
  for insert to authenticated with check (viewer_id = auth.uid() or viewer_id is null);

-- --- Integrations (admins only) ---
drop policy if exists "integrations_admin_all" on public.integrations;
create policy "integrations_admin_all" on public.integrations
  for all to authenticated
  using (public.is_admin())
  with check (public.is_admin());

-- --- Onboarding progress ---
drop policy if exists "onboarding_admin_all" on public.onboarding_progress;
create policy "onboarding_admin_all" on public.onboarding_progress
  for all to authenticated
  using (public.is_admin())
  with check (public.is_admin());

drop policy if exists "onboarding_self_rw" on public.onboarding_progress;
create policy "onboarding_self_rw" on public.onboarding_progress
  for all to authenticated
  using (caregiver_id = auth.uid())
  with check (caregiver_id = auth.uid());

-- --- Packet shares (admins manage; anyone with token reads via service role) ---
drop policy if exists "packet_shares_admin_all" on public.packet_shares;
create policy "packet_shares_admin_all" on public.packet_shares
  for all to authenticated
  using (public.is_admin())
  with check (public.is_admin());

-- --- Policy acknowledgments ---
drop policy if exists "policy_acks_admin_read" on public.policy_acknowledgments;
create policy "policy_acks_admin_read" on public.policy_acknowledgments
  for select to authenticated using (public.is_admin() or user_id = auth.uid());

drop policy if exists "policy_acks_self_insert" on public.policy_acknowledgments;
create policy "policy_acks_self_insert" on public.policy_acknowledgments
  for insert to authenticated with check (user_id = auth.uid());

-- --- Training ---
drop policy if exists "training_modules_auth_read" on public.training_modules;
create policy "training_modules_auth_read" on public.training_modules
  for select to authenticated using (true);

drop policy if exists "training_modules_admin_write" on public.training_modules;
create policy "training_modules_admin_write" on public.training_modules
  for all to authenticated
  using (public.is_admin())
  with check (public.is_admin());

drop policy if exists "training_completions_self_rw" on public.training_completions;
create policy "training_completions_self_rw" on public.training_completions
  for all to authenticated
  using (caregiver_id = auth.uid() or public.is_admin())
  with check (caregiver_id = auth.uid() or public.is_admin());

-- ================================================================
-- DONE
-- ================================================================
select 'Schema applied successfully' as status;
