-- Hosted Supabase owns auth schema ACLs; custom-role USAGE grants are unsupported.
-- This private, backend-only predicate exposes no Auth rows or credentials.
-- https://supabase.com/kb/troubleshooting/permission-errors-when-granting-custom-roles-access-to-the-auth-schema
BEGIN;
CREATE OR REPLACE FUNCTION atm.provider_session_active(session_id uuid)
RETURNS boolean
LANGUAGE sql
STABLE
SECURITY DEFINER
SET search_path = ''
AS $function$
  SELECT EXISTS (
    SELECT 1 FROM auth.sessions s
    WHERE s.id = session_id
      AND s.user_id = (SELECT auth.uid())
      AND (s.not_after IS NULL OR s.not_after > now())
  );
$function$;
ALTER FUNCTION atm.provider_session_active(uuid) OWNER TO postgres;
REVOKE ALL ON FUNCTION atm.provider_session_active(uuid) FROM PUBLIC, anon, authenticated, service_role;
GRANT EXECUTE ON FUNCTION atm.provider_session_active(uuid) TO atm_runtime;
COMMENT ON FUNCTION atm.provider_session_active(uuid) IS
  'Backend-only session liveness check. Caller sets transaction-local subject from a verified JWT; auth.uid binds ownership. Returns boolean only.';
COMMIT;
