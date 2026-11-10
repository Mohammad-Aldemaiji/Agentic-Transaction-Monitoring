-- Apply as schema owner before deploying guided investigations.
BEGIN;
ALTER TABLE atm.proposals ALTER COLUMN expires_at DROP NOT NULL;
ALTER TABLE atm_test.proposals ALTER COLUMN expires_at DROP NOT NULL;
COMMIT;
