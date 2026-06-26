-- ================================================================
-- Phase 3 Schema Fixups: align column names with the MongoDB
-- production shape so the Phase 4 cutover is a clean swap.
-- ================================================================

-- --- chat_messages: AI assistant chat, not group chat ---
drop table if exists public.chat_messages cascade;
create table public.chat_messages (
  id          uuid primary key default gen_random_uuid(),
  session_id  text not null,
  user_id     uuid not null references public.profiles(id) on delete cascade,
  role        text not null check (role in ('user','assistant','system')),
  content     text not null,
  created_at  timestamptz not null default now()
);
create index if not exists chat_messages_session_idx on public.chat_messages(session_id, created_at);
create index if not exists chat_messages_user_idx on public.chat_messages(user_id, created_at desc);

alter table public.chat_messages enable row level security;
drop policy if exists "chat_messages_own_rw" on public.chat_messages;
create policy "chat_messages_own_rw" on public.chat_messages
  for all to authenticated
  using (user_id = auth.uid() or public.is_admin())
  with check (user_id = auth.uid() or public.is_admin());

-- --- chat_dms: rename columns to from_id/to_id/text ---
drop table if exists public.chat_dms cascade;
create table public.chat_dms (
  id          uuid primary key default gen_random_uuid(),
  from_id     uuid not null references public.profiles(id) on delete cascade,
  from_name   text not null,
  to_id       uuid not null references public.profiles(id) on delete cascade,
  to_name     text not null,
  text        text not null,
  read        boolean not null default false,
  created_at  timestamptz not null default now()
);
create index if not exists chat_dms_pair_idx on public.chat_dms(from_id, to_id, created_at desc);
create index if not exists chat_dms_inbox_idx on public.chat_dms(to_id, read);

alter table public.chat_dms enable row level security;
drop policy if exists "chat_dms_participant" on public.chat_dms;
create policy "chat_dms_participant" on public.chat_dms
  for all to authenticated
  using (from_id = auth.uid() or to_id = auth.uid() or public.is_admin())
  with check (from_id = auth.uid() or public.is_admin());

-- --- packet_shares: match Mongo shape ---
drop table if exists public.packet_shares cascade;
create table public.packet_shares (
  id              uuid primary key default gen_random_uuid(),
  token           text not null unique,
  recipient_name  text not null,
  recipient_role  text not null,
  category        text,
  recipient_email text,
  recipient_phone text,
  created_by      uuid references public.profiles(id) on delete set null,
  created_at      timestamptz not null default now(),
  viewed_at       timestamptz,
  completed_at    timestamptz,
  signed_ids      uuid[] not null default '{}'
);
create index if not exists packet_shares_token_idx on public.packet_shares(token);
create index if not exists packet_shares_creator_idx on public.packet_shares(created_by);

alter table public.packet_shares enable row level security;
drop policy if exists "packet_shares_admin_all" on public.packet_shares;
create policy "packet_shares_admin_all" on public.packet_shares
  for all to authenticated
  using (public.is_admin())
  with check (public.is_admin());

-- --- policy_acknowledgments: rename to policy_acks; add user_name; relax policy_id ---
drop table if exists public.policy_acknowledgments cascade;
create table public.policy_acks (
  id              uuid primary key default gen_random_uuid(),
  user_id         uuid not null references public.profiles(id) on delete cascade,
  user_name       text,
  policy_id       text not null,
  policy_title    text not null,
  acknowledged_at timestamptz not null default now(),
  unique (policy_id, user_id)
);
create index if not exists policy_acks_user_idx on public.policy_acks(user_id);

alter table public.policy_acks enable row level security;
drop policy if exists "policy_acks_self_read" on public.policy_acks;
create policy "policy_acks_self_read" on public.policy_acks
  for select to authenticated using (public.is_admin() or user_id = auth.uid());
drop policy if exists "policy_acks_self_insert" on public.policy_acks;
create policy "policy_acks_self_insert" on public.policy_acks
  for insert to authenticated with check (user_id = auth.uid());

-- --- document_views: viewer_id may be null; allow viewer_name freeform ---
alter table public.document_views drop constraint if exists document_views_viewer_id_fkey;
alter table public.document_views add constraint document_views_viewer_id_fkey
  foreign key (viewer_id) references public.profiles(id) on delete set null;

-- --- onboarding: rename onboarding_progress -> onboarding (match Mongo) ---
drop table if exists public.onboarding cascade;
create table public.onboarding (
  id            uuid primary key default gen_random_uuid(),
  caregiver_id  uuid not null references public.profiles(id) on delete cascade,
  title         text not null,
  description   text default '',
  completed     boolean not null default false,
  completed_at  timestamptz,
  created_at    timestamptz not null default now()
);
create index if not exists onboarding_caregiver_idx on public.onboarding(caregiver_id);

alter table public.onboarding enable row level security;
drop policy if exists "onboarding_admin_all" on public.onboarding;
create policy "onboarding_admin_all" on public.onboarding
  for all to authenticated
  using (public.is_admin())
  with check (public.is_admin());
drop policy if exists "onboarding_self_rw" on public.onboarding;
create policy "onboarding_self_rw" on public.onboarding
  for all to authenticated
  using (caregiver_id = auth.uid())
  with check (caregiver_id = auth.uid());

drop table if exists public.onboarding_progress cascade;

-- --- training_modules -> training (match Mongo) ---
drop table if exists public.training cascade;
create table public.training (
  id            uuid primary key default gen_random_uuid(),
  title         text not null,
  description   text default '',
  storage_path  text,
  mime_type     text default 'video/mp4',
  required      boolean not null default true,
  created_at    timestamptz not null default now()
);

alter table public.training enable row level security;
drop policy if exists "training_auth_read" on public.training;
create policy "training_auth_read" on public.training
  for select to authenticated using (true);
drop policy if exists "training_admin_write" on public.training;
create policy "training_admin_write" on public.training
  for all to authenticated
  using (public.is_admin())
  with check (public.is_admin());

drop table if exists public.training_modules cascade;

-- training_completions FK still points to old training_modules; fix
alter table public.training_completions drop constraint if exists training_completions_training_id_fkey;
alter table public.training_completions add constraint training_completions_training_id_fkey
  foreign key (training_id) references public.training(id) on delete cascade;

select 'Schema fixups applied' as status;
