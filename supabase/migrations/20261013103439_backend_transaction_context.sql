-- Backend transaction context and reproducible feature snapshots.
ALTER TABLE atm.transactions
  ADD COLUMN context jsonb NOT NULL DEFAULT '{}'::jsonb CHECK (jsonb_typeof(context) = 'object'),
  ADD COLUMN features jsonb CHECK (features IS NULL OR jsonb_typeof(features) = 'object');
ALTER TABLE atm.training_requests ADD COLUMN cancellation_reason varchar(2000);

CREATE FUNCTION atm.protect_transaction_input() RETURNS trigger
LANGUAGE plpgsql SET search_path = '' AS $$
BEGIN
  IF ROW(NEW.id, NEW.team, NEW.external_id, NEW.idempotency_key, NEW.payload_hash,
         NEW.account_id, NEW.amount_minor, NEW.currency, NEW.occurred_at, NEW.received_at,
         NEW.channel, NEW.context)
     IS DISTINCT FROM
     ROW(OLD.id, OLD.team, OLD.external_id, OLD.idempotency_key, OLD.payload_hash,
         OLD.account_id, OLD.amount_minor, OLD.currency, OLD.occurred_at, OLD.received_at,
         OLD.channel, OLD.context) THEN
    RAISE EXCEPTION 'Transaction input is immutable' USING ERRCODE = '23514';
  END IF;
  IF OLD.features IS NOT NULL AND NEW.features IS DISTINCT FROM OLD.features THEN
    RAISE EXCEPTION 'Feature snapshot is immutable' USING ERRCODE = '23514';
  END IF;
  IF OLD.result IS NOT NULL AND NEW.result IS DISTINCT FROM OLD.result THEN
    RAISE EXCEPTION 'Scoring evidence is immutable' USING ERRCODE = '23514';
  END IF;
  RETURN NEW;
END;
$$;
REVOKE ALL ON FUNCTION atm.protect_transaction_input() FROM PUBLIC;
CREATE TRIGGER protect_transaction_input BEFORE UPDATE ON atm.transactions
FOR EACH ROW EXECUTE FUNCTION atm.protect_transaction_input();

CREATE FUNCTION atm.protect_training_snapshot() RETURNS trigger
LANGUAGE plpgsql SET search_path = '' AS $$
BEGIN
  IF ROW(NEW.id, NEW.team, NEW.requested_by, NEW.snapshot, NEW.snapshot_hash, NEW.created_at)
     IS DISTINCT FROM
     ROW(OLD.id, OLD.team, OLD.requested_by, OLD.snapshot, OLD.snapshot_hash, OLD.created_at)
     OR OLD.state <> 'READY' OR NEW.state NOT IN ('COMPLETED','CANCELLED') THEN
    RAISE EXCEPTION 'Invalid training transition or changed snapshot' USING ERRCODE = '23514';
  END IF;
  IF (NEW.state = 'COMPLETED' AND (NEW.model_id IS NULL OR NEW.cancellation_reason IS NOT NULL))
     OR (NEW.state = 'CANCELLED' AND (NEW.model_id IS NOT NULL OR length(trim(NEW.cancellation_reason)) < 5 OR NEW.cancellation_reason IS NULL)) THEN
    RAISE EXCEPTION 'Training outcome requires its evidence' USING ERRCODE = '23514';
  END IF;
  RETURN NEW;
END;
$$;
REVOKE ALL ON FUNCTION atm.protect_training_snapshot() FROM PUBLIC;
CREATE TRIGGER protect_training_snapshot BEFORE UPDATE ON atm.training_requests
FOR EACH ROW EXECUTE FUNCTION atm.protect_training_snapshot();
UPDATE atm.alembic_version SET version_num = '0007';

GRANT atm_test_owner TO postgres;
SET ROLE atm_test_owner;
ALTER TABLE atm_test.transactions
  ADD COLUMN context jsonb NOT NULL DEFAULT '{}'::jsonb CHECK (jsonb_typeof(context) = 'object'),
  ADD COLUMN features jsonb CHECK (features IS NULL OR jsonb_typeof(features) = 'object');
ALTER TABLE atm_test.training_requests ADD COLUMN cancellation_reason varchar(2000);

CREATE FUNCTION atm_test.protect_transaction_input() RETURNS trigger
LANGUAGE plpgsql SET search_path = '' AS $$
BEGIN
  IF ROW(NEW.id, NEW.team, NEW.external_id, NEW.idempotency_key, NEW.payload_hash,
         NEW.account_id, NEW.amount_minor, NEW.currency, NEW.occurred_at, NEW.received_at,
         NEW.channel, NEW.context)
     IS DISTINCT FROM
     ROW(OLD.id, OLD.team, OLD.external_id, OLD.idempotency_key, OLD.payload_hash,
         OLD.account_id, OLD.amount_minor, OLD.currency, OLD.occurred_at, OLD.received_at,
         OLD.channel, OLD.context) THEN
    RAISE EXCEPTION 'Transaction input is immutable' USING ERRCODE = '23514';
  END IF;
  IF OLD.features IS NOT NULL AND NEW.features IS DISTINCT FROM OLD.features THEN
    RAISE EXCEPTION 'Feature snapshot is immutable' USING ERRCODE = '23514';
  END IF;
  IF OLD.result IS NOT NULL AND NEW.result IS DISTINCT FROM OLD.result THEN
    RAISE EXCEPTION 'Scoring evidence is immutable' USING ERRCODE = '23514';
  END IF;
  RETURN NEW;
END;
$$;
REVOKE ALL ON FUNCTION atm_test.protect_transaction_input() FROM PUBLIC;
CREATE TRIGGER protect_transaction_input BEFORE UPDATE ON atm_test.transactions
FOR EACH ROW EXECUTE FUNCTION atm_test.protect_transaction_input();

CREATE FUNCTION atm_test.protect_training_snapshot() RETURNS trigger
LANGUAGE plpgsql SET search_path = '' AS $$
BEGIN
  IF ROW(NEW.id, NEW.team, NEW.requested_by, NEW.snapshot, NEW.snapshot_hash, NEW.created_at)
     IS DISTINCT FROM
     ROW(OLD.id, OLD.team, OLD.requested_by, OLD.snapshot, OLD.snapshot_hash, OLD.created_at)
     OR OLD.state <> 'READY' OR NEW.state NOT IN ('COMPLETED','CANCELLED') THEN
    RAISE EXCEPTION 'Invalid training transition or changed snapshot' USING ERRCODE = '23514';
  END IF;
  IF (NEW.state = 'COMPLETED' AND (NEW.model_id IS NULL OR NEW.cancellation_reason IS NOT NULL))
     OR (NEW.state = 'CANCELLED' AND (NEW.model_id IS NOT NULL OR length(trim(NEW.cancellation_reason)) < 5 OR NEW.cancellation_reason IS NULL)) THEN
    RAISE EXCEPTION 'Training outcome requires its evidence' USING ERRCODE = '23514';
  END IF;
  RETURN NEW;
END;
$$;
REVOKE ALL ON FUNCTION atm_test.protect_training_snapshot() FROM PUBLIC;
CREATE TRIGGER protect_training_snapshot BEFORE UPDATE ON atm_test.training_requests
FOR EACH ROW EXECUTE FUNCTION atm_test.protect_training_snapshot();
UPDATE atm_test.alembic_version SET version_num = '0007';

RESET ROLE;
REVOKE atm_test_owner FROM postgres;
