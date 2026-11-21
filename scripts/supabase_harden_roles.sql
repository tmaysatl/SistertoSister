-- Audit Prep Hub: close two Supabase privilege-escalation paths.
-- Run once in the Supabase SQL editor (project "Audit Compliance") AFTER it is restored.
-- Safe to re-run. Additive: replaces one function, adds one trigger, changes no data.

-- 1) Signup trigger: never trust a role the *client* sends.
--    Anyone with the public anon key can call Supabase signUp with any user_metadata,
--    so user_metadata.role must NOT decide the role. raw_app_meta_data can only be set
--    by the backend (service role), so it is the trusted source; default is 'caregiver'.
create or replace function public.handle_new_user()
returns trigger
language plpgsql
security definer set search_path = public
as $$
declare
  v_role text := coalesce(new.raw_app_meta_data->>'role', 'caregiver');
begin
  if v_role not in ('admin', 'caregiver', 'client') then
    v_role := 'caregiver';
  end if;
  insert into public.profiles (id, email, name, role)
  values (
    new.id,
    new.email,
    coalesce(new.raw_user_meta_data->>'name', split_part(new.email, '@', 1)),
    v_role
  )
  on conflict (id) do nothing;
  return new;
end;
$$;

-- 2) Block self-promotion: the profiles_self_update policy lets a signed-in user update
--    their own row, which included the "role" column (PATCH /rest/v1/profiles).
--    Role may now only change from the backend (direct DB connection / service role).
create or replace function public.prevent_role_change_by_users()
returns trigger
language plpgsql
security definer set search_path = public
as $$
begin
  if new.role is distinct from old.role
     and auth.uid() is not null
     and coalesce(auth.role(), '') = 'authenticated' then
    raise exception 'profiles.role can only be changed by the backend';
  end if;
  return new;
end;
$$;

drop trigger if exists profiles_prevent_role_change on public.profiles;
create trigger profiles_prevent_role_change
  before update on public.profiles
  for each row execute function public.prevent_role_change_by_users();

-- Optional, in the Supabase dashboard (Authentication > Sign In / Providers):
--  * Turn OFF "Allow new users to sign up" if caregivers should only be created by the
--    backend/admins. (The app's self-registration in Supabase mode then stops working.)
--  * Turn ON leaked-password protection.
