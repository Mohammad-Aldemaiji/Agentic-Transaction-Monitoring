-- Action lifecycle integrity for application and isolated test schemas.
ALTER TABLE atm.proposals DROP CONSTRAINT proposals_state_check;
ALTER TABLE atm.proposals ADD CONSTRAINT proposals_state_check
  CHECK (state IN ('PENDING','APPROVED','REJECTED','EXPIRED','CANCELLED'));

CREATE FUNCTION atm.protect_proposal() RETURNS trigger
LANGUAGE plpgsql SET search_path = '' AS $$
BEGIN
  IF ROW(NEW.id, NEW.case_id, NEW.proposer_id, NEW.action, NEW.target, NEW.reason,
         NEW.evidence_version, NEW.created_at, NEW.expires_at)
     IS DISTINCT FROM
     ROW(OLD.id, OLD.case_id, OLD.proposer_id, OLD.action, OLD.target, OLD.reason,
         OLD.evidence_version, OLD.created_at, OLD.expires_at) THEN
    RAISE EXCEPTION 'Proposal evidence and action are immutable';
  END IF;
  IF NEW.version != OLD.version + 1 OR NOT (
    (OLD.state = 'PENDING' AND NEW.state IN ('APPROVED','REJECTED','EXPIRED','CANCELLED')) OR
    (OLD.state = 'APPROVED' AND NEW.state = 'CANCELLED')
  ) THEN
    RAISE EXCEPTION 'Invalid proposal transition';
  END IF;
  IF (OLD.state <> 'PENDING' AND NEW.approver_id IS DISTINCT FROM OLD.approver_id) OR
     (NEW.state IN ('APPROVED','REJECTED') AND NEW.approver_id IS NULL) THEN
    RAISE EXCEPTION 'Invalid approval identity';
  END IF;
  IF NEW.state = 'CANCELLED' AND EXISTS (
    SELECT 1 FROM atm.executions WHERE proposal_id = OLD.id
  ) THEN
    RAISE EXCEPTION 'Executed actions cannot be cancelled';
  END IF;
  RETURN NEW;
END $$;
REVOKE ALL ON FUNCTION atm.protect_proposal() FROM PUBLIC;
CREATE TRIGGER protect_proposal BEFORE UPDATE ON atm.proposals
FOR EACH ROW EXECUTE FUNCTION atm.protect_proposal();

CREATE FUNCTION atm.validate_execution() RETURNS trigger
LANGUAGE plpgsql SET search_path = '' AS $$
DECLARE proposal atm.proposals%ROWTYPE; parent atm.cases%ROWTYPE;
BEGIN
  SELECT * INTO proposal FROM atm.proposals WHERE id = NEW.proposal_id;
  IF NOT FOUND THEN RAISE EXCEPTION 'Proposal required'; END IF;
  SELECT * INTO parent FROM atm.cases WHERE id = proposal.case_id FOR UPDATE;
  SELECT * INTO proposal FROM atm.proposals WHERE id = NEW.proposal_id FOR SHARE;
  IF proposal.state <> 'APPROVED' OR parent.state = 'RESOLVED'
     OR proposal.evidence_version <> parent.version
     OR (NEW.receipt->>'action') IS DISTINCT FROM proposal.action
     OR (NEW.receipt->>'target') IS DISTINCT FROM proposal.target
     OR (NEW.receipt->>'idempotency_key') IS DISTINCT FROM proposal.id::text
     OR (NEW.receipt->'simulation') IS DISTINCT FROM 'true'::jsonb
     OR (NEW.receipt->>'status') IS DISTINCT FROM 'SUCCEEDED' THEN
    RAISE EXCEPTION 'Execution must match approved evidence and action';
  END IF;
  RETURN NEW;
END $$;
REVOKE ALL ON FUNCTION atm.validate_execution() FROM PUBLIC;
CREATE TRIGGER validate_execution BEFORE INSERT ON atm.executions
FOR EACH ROW EXECUTE FUNCTION atm.validate_execution();
UPDATE atm.alembic_version SET version_num = '0006';

GRANT atm_test_owner TO postgres;
SET LOCAL ROLE atm_test_owner;
ALTER TABLE atm_test.proposals DROP CONSTRAINT proposals_state_check;
ALTER TABLE atm_test.proposals ADD CONSTRAINT proposals_state_check
  CHECK (state IN ('PENDING','APPROVED','REJECTED','EXPIRED','CANCELLED'));

CREATE FUNCTION atm_test.protect_proposal() RETURNS trigger
LANGUAGE plpgsql SET search_path = '' AS $$
BEGIN
  IF ROW(NEW.id, NEW.case_id, NEW.proposer_id, NEW.action, NEW.target, NEW.reason,
         NEW.evidence_version, NEW.created_at, NEW.expires_at)
     IS DISTINCT FROM
     ROW(OLD.id, OLD.case_id, OLD.proposer_id, OLD.action, OLD.target, OLD.reason,
         OLD.evidence_version, OLD.created_at, OLD.expires_at) THEN
    RAISE EXCEPTION 'Proposal evidence and action are immutable';
  END IF;
  IF NEW.version != OLD.version + 1 OR NOT (
    (OLD.state = 'PENDING' AND NEW.state IN ('APPROVED','REJECTED','EXPIRED','CANCELLED')) OR
    (OLD.state = 'APPROVED' AND NEW.state = 'CANCELLED')
  ) THEN
    RAISE EXCEPTION 'Invalid proposal transition';
  END IF;
  IF (OLD.state <> 'PENDING' AND NEW.approver_id IS DISTINCT FROM OLD.approver_id) OR
     (NEW.state IN ('APPROVED','REJECTED') AND NEW.approver_id IS NULL) THEN
    RAISE EXCEPTION 'Invalid approval identity';
  END IF;
  IF NEW.state = 'CANCELLED' AND EXISTS (
    SELECT 1 FROM atm_test.executions WHERE proposal_id = OLD.id
  ) THEN
    RAISE EXCEPTION 'Executed actions cannot be cancelled';
  END IF;
  RETURN NEW;
END $$;
REVOKE ALL ON FUNCTION atm_test.protect_proposal() FROM PUBLIC;
CREATE TRIGGER protect_proposal BEFORE UPDATE ON atm_test.proposals
FOR EACH ROW EXECUTE FUNCTION atm_test.protect_proposal();

CREATE FUNCTION atm_test.validate_execution() RETURNS trigger
LANGUAGE plpgsql SET search_path = '' AS $$
DECLARE proposal atm_test.proposals%ROWTYPE; parent atm_test.cases%ROWTYPE;
BEGIN
  SELECT * INTO proposal FROM atm_test.proposals WHERE id = NEW.proposal_id;
  IF NOT FOUND THEN RAISE EXCEPTION 'Proposal required'; END IF;
  SELECT * INTO parent FROM atm_test.cases WHERE id = proposal.case_id FOR UPDATE;
  SELECT * INTO proposal FROM atm_test.proposals WHERE id = NEW.proposal_id FOR SHARE;
  IF proposal.state <> 'APPROVED' OR parent.state = 'RESOLVED'
     OR proposal.evidence_version <> parent.version
     OR (NEW.receipt->>'action') IS DISTINCT FROM proposal.action
     OR (NEW.receipt->>'target') IS DISTINCT FROM proposal.target
     OR (NEW.receipt->>'idempotency_key') IS DISTINCT FROM proposal.id::text
     OR (NEW.receipt->'simulation') IS DISTINCT FROM 'true'::jsonb
     OR (NEW.receipt->>'status') IS DISTINCT FROM 'SUCCEEDED' THEN
    RAISE EXCEPTION 'Execution must match approved evidence and action';
  END IF;
  RETURN NEW;
END $$;
REVOKE ALL ON FUNCTION atm_test.validate_execution() FROM PUBLIC;
CREATE TRIGGER validate_execution BEFORE INSERT ON atm_test.executions
FOR EACH ROW EXECUTE FUNCTION atm_test.validate_execution();
UPDATE atm_test.alembic_version SET version_num = '0006';
RESET ROLE;
REVOKE atm_test_owner FROM postgres;
