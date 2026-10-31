"""Generate the API contract using the configured Supabase deployment settings."""

import argparse
import json
from pathlib import Path

from dotenv import dotenv_values

from atm_backend.main import create_app
from atm_backend.settings import Settings

ROLES = ("analyst", "supervisor", "auditor", "admin", "ingestion", "participant")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("operation", choices=["openapi"])
    parser.add_argument("--env-file", default=".runtime/supabase-host.env")
    parser.add_argument("--output", default="docs/openapi.json")
    args = parser.parse_args()
    values = {
        k.removeprefix("ATM_").lower(): v for k, v in dotenv_values(args.env_file).items() if v
    }
    for field in ("jwt_algorithms", "allowed_hosts"):
        if field in values:
            values[field] = json.loads(values[field])
    settings = Settings.model_validate(values)
    application = create_app(settings)
    Path(args.output).write_text(json.dumps(application.openapi(), indent=2) + "\n")
    application.state.engine.dispose()
    print(f"Wrote API specification to {args.output}")


if __name__ == "__main__":
    main()
