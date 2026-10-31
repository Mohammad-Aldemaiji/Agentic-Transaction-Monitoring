"""Read-only verification of a runtime database connection; never prints its URL."""

import argparse
import json

from dotenv import dotenv_values
from sqlalchemy import text

from atm_backend.db import database
from atm_backend.settings import Settings

parser = argparse.ArgumentParser(description=__doc__)
parser.add_argument("--env-file", required=True)
args = parser.parse_args()
values = dotenv_values(args.env_file)
configuration = {
    k.removeprefix("ATM_").lower(): v for k, v in values.items() if k.startswith("ATM_") and v
}
configuration["database_url"] = values["ATM_DATABASE_URL"]
for field in ("jwt_algorithms", "allowed_hosts"):
    if field in configuration:
        configuration[field] = json.loads(configuration[field])
settings = Settings.model_validate(configuration)
engine, _ = database(settings)
try:
    with engine.connect() as connection:
        row = (
            connection.execute(
                text("""
            SELECT current_user AS identity,
              atm.provider_session_active(NULL::uuid) AS unknown_session_active,
              current_setting('server_version') AS server_version,
              current_setting('statement_timeout') AS statement_timeout,
              has_table_privilege(current_user,'atm.transactions','INSERT') AS can_ingest,
              has_table_privilege(current_user,'atm.principals','UPDATE') AS can_change_roles,
              has_table_privilege(current_user,'atm.audit_events','UPDATE') AS can_change_audit,
              has_table_privilege(current_user,'atm.audit_events','DELETE') AS can_delete_audit,
              has_schema_privilege(current_user,'atm','CREATE') AS can_change_schema
        """)
            )
            .mappings()
            .one()
        )
        assert row["can_ingest"]
        assert row["unknown_session_active"] is False
        assert row["statement_timeout"] == "10s"
        assert not any(
            row[k]
            for k in (
                "can_change_roles",
                "can_change_audit",
                "can_delete_audit",
                "can_change_schema",
            )
        )
        assert connection.execute(text("SELECT count(*) FROM atm.principals")).scalar() is not None
        print(json.dumps(dict(row), indent=2))
        print("Runtime permission verification passed.")
except Exception as exc:
    print("Verification failed:", type(exc).__name__)
    raise SystemExit(1) from None
finally:
    engine.dispose()
