-- Approved operational capacity and immutable risk policy evidence.
ALTER TABLE atm.risk_policies
  ADD COLUMN daily_review_capacity integer CHECK (daily_review_capacity BETWEEN 1 AND 100000),
  ADD COLUMN medium_threshold double precision NOT NULL DEFAULT 0.35,
  ADD COLUMN high_threshold double precision NOT NULL DEFAULT 0.65,
  ADD COLUMN critical_threshold double precision NOT NULL DEFAULT 0.85,
  ADD CONSTRAINT ordered_severity_bands CHECK (
    0 <= medium_threshold AND medium_threshold < high_threshold AND
    high_threshold < critical_threshold AND critical_threshold <= 1
  ),
  ADD CONSTRAINT policy_state CHECK (state IN ('DRAFT', 'ACTIVE', 'RETIRED')),
  ADD CONSTRAINT policy_independent_approval CHECK (
    (state = 'DRAFT' AND approved_by IS NULL) OR
    (state IN ('ACTIVE', 'RETIRED') AND approved_by IS NOT NULL AND approved_by <> created_by)
  );

CREATE FUNCTION atm.protect_risk_policy() RETURNS trigger
LANGUAGE plpgsql SET search_path = '' AS $$
BEGIN
  IF ROW(NEW.id, NEW.team, NEW.version, NEW.threshold, NEW.reason, NEW.created_by,
         NEW.created_at, NEW.daily_review_capacity, NEW.medium_threshold,
         NEW.high_threshold, NEW.critical_threshold)
     IS DISTINCT FROM
     ROW(OLD.id, OLD.team, OLD.version, OLD.threshold, OLD.reason, OLD.created_by,
         OLD.created_at, OLD.daily_review_capacity, OLD.medium_threshold,
         OLD.high_threshold, OLD.critical_threshold) THEN
    RAISE EXCEPTION 'Risk policy evidence is immutable; propose a new version' USING ERRCODE = '23514';
  END IF;
  IF NOT ((OLD.state = 'DRAFT' AND NEW.state = 'ACTIVE') OR
          (OLD.state = 'ACTIVE' AND NEW.state = 'RETIRED' AND NEW.approved_by = OLD.approved_by)) THEN
    RAISE EXCEPTION 'Invalid policy transition' USING ERRCODE = '23514';
  END IF;
  RETURN NEW;
END;
$$;
REVOKE ALL ON FUNCTION atm.protect_risk_policy() FROM PUBLIC;
CREATE TRIGGER protect_risk_policy BEFORE UPDATE ON atm.risk_policies
FOR EACH ROW EXECUTE FUNCTION atm.protect_risk_policy();
UPDATE atm.alembic_version SET version_num = '0008';

GRANT atm_test_owner TO postgres;
SET ROLE atm_test_owner;
ALTER TABLE atm_test.risk_policies
  ADD COLUMN daily_review_capacity integer CHECK (daily_review_capacity BETWEEN 1 AND 100000),
  ADD COLUMN medium_threshold double precision NOT NULL DEFAULT 0.35,
  ADD COLUMN high_threshold double precision NOT NULL DEFAULT 0.65,
  ADD COLUMN critical_threshold double precision NOT NULL DEFAULT 0.85,
  ADD CONSTRAINT ordered_severity_bands CHECK (
    0 <= medium_threshold AND medium_threshold < high_threshold AND
    high_threshold < critical_threshold AND critical_threshold <= 1
  ),
  ADD CONSTRAINT policy_state CHECK (state IN ('DRAFT', 'ACTIVE', 'RETIRED')),
  ADD CONSTRAINT policy_independent_approval CHECK (
    (state = 'DRAFT' AND approved_by IS NULL) OR
    (state IN ('ACTIVE', 'RETIRED') AND approved_by IS NOT NULL AND approved_by <> created_by)
  );

CREATE FUNCTION atm_test.protect_risk_policy() RETURNS trigger
LANGUAGE plpgsql SET search_path = '' AS $$
BEGIN
  IF ROW(NEW.id, NEW.team, NEW.version, NEW.threshold, NEW.reason, NEW.created_by,
         NEW.created_at, NEW.daily_review_capacity, NEW.medium_threshold,
         NEW.high_threshold, NEW.critical_threshold)
     IS DISTINCT FROM
     ROW(OLD.id, OLD.team, OLD.version, OLD.threshold, OLD.reason, OLD.created_by,
         OLD.created_at, OLD.daily_review_capacity, OLD.medium_threshold,
         OLD.high_threshold, OLD.critical_threshold) THEN
    RAISE EXCEPTION 'Risk policy evidence is immutable; propose a new version' USING ERRCODE = '23514';
  END IF;
  IF NOT ((OLD.state = 'DRAFT' AND NEW.state = 'ACTIVE') OR
          (OLD.state = 'ACTIVE' AND NEW.state = 'RETIRED' AND NEW.approved_by = OLD.approved_by)) THEN
    RAISE EXCEPTION 'Invalid policy transition' USING ERRCODE = '23514';
  END IF;
  RETURN NEW;
END;
$$;
REVOKE ALL ON FUNCTION atm_test.protect_risk_policy() FROM PUBLIC;
CREATE TRIGGER protect_risk_policy BEFORE UPDATE ON atm_test.risk_policies
FOR EACH ROW EXECUTE FUNCTION atm_test.protect_risk_policy();
UPDATE atm_test.alembic_version SET version_num = '0008';

RESET ROLE;
REVOKE atm_test_owner FROM postgres;
