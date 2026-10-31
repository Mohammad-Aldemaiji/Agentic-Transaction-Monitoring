import json

import pytest
from sqlalchemy.exc import OperationalError, TimeoutError

from atm_backend.db import failure_details


class DriverFailure(Exception):
    sqlstate = "08006"


@pytest.mark.parametrize("sqlstate", ["08006", "secret-password", "ééééé", None])
def test_database_diagnostics_never_include_sensitive_values(sqlstate):
    driver = DriverFailure("credential-and-account-secret")
    driver.sqlstate = sqlstate
    error = OperationalError(
        "SQL-with-private-data",
        {"password": "private-password"},
        driver,
        connection_invalidated=True,
    )
    details = failure_details(error)
    assert details["connection_invalidated"] is True
    assert details.get("sqlstate") == ("08006" if sqlstate == "08006" else None)
    assert "secret" not in json.dumps(details)
    assert "private" not in json.dumps(details)
    assert "password" not in json.dumps(details)


def test_pool_timeout_and_unknown_failure_use_fixed_categories():
    assert failure_details(TimeoutError("private")) == {"category": "pool_timeout"}
    assert failure_details(ValueError("private")) == {"category": "processing_error"}
