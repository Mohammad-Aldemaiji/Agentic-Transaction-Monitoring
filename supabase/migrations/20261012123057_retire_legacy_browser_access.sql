-- Retire the prototype's unrestricted browser access. Preserve every table and row.
-- All user-facing access now goes through the scoped backend and private atm schema.
DO $$
DECLARE p record; relation_name text;
BEGIN
  FOR p IN
    SELECT tablename, policyname FROM pg_policies
    WHERE schemaname = 'public'
      AND tablename IN ('accounts','transactions','model_versions','scores','alerts',
        'decisions','threshold_sets','risk_thresholds','scoring_retry_queue')
      AND ('authenticated' = ANY(roles) OR 'anon' = ANY(roles))
  LOOP
    EXECUTE format('DROP POLICY %I ON public.%I', p.policyname, p.tablename);
  END LOOP;
  FOREACH relation_name IN ARRAY ARRAY[
    'accounts','transactions','model_versions','scores','alerts','decisions',
    'threshold_sets','risk_thresholds','scoring_retry_queue',
    'alert_details','current_alert_decisions'
  ] LOOP
    IF to_regclass(format('public.%I', relation_name)) IS NOT NULL THEN
      EXECUTE format('REVOKE ALL ON public.%I FROM anon, authenticated', relation_name);
    END IF;
  END LOOP;
END $$;
