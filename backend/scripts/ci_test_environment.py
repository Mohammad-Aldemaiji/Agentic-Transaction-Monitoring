"""Write isolated Supabase test credentials from protected CI secrets without logging them."""

import os
from pathlib import Path

from sqlalchemy.engine import make_url

os.umask(0o077)
values = []
for name, role in [
    ("ATM_TEST_MIGRATION_URL", "atm_test_owner"),
    ("ATM_TEST_DATABASE_URL", "atm_test_runtime"),
]:
    value = os.environ.get(name)
    if not value:
        raise SystemExit(f"Missing protected CI secret: {name}")
    url = make_url(value)
    if not (url.username or "").startswith(role + "."):
        raise SystemExit("Only isolated test-role credentials are accepted")
    url = url.update_query_dict(
        {"sslmode": "verify-full", "sslrootcert": str(Path(".runtime/supabase-ca.pem").resolve())}
    )
    values.append(name + "=" + url.render_as_string(hide_password=False))
Path(".runtime/test.env").write_text("\n".join(values) + "\n")
