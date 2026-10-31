from datetime import timedelta
from unittest.mock import Mock
from uuid import uuid4

import pytest

from atm_backend.models import now
from atm_backend.provisioning import IdentityNotReady, verified_subject


def operator_result(rows):
    session = Mock()
    session.execute.return_value.mappings.return_value.all.return_value = rows
    return session


def account(**changes):
    return {
        "id": uuid4(),
        "email_confirmed_at": now(),
        "banned_until": None,
        "deleted_at": None,
        "mfa_ready": True,
        **changes,
    }


@pytest.mark.parametrize(
    "rows",
    [
        [],
        [account(), account()],
        [account(email_confirmed_at=None)],
        [account(banned_until=now() + timedelta(hours=1))],
        [account(deleted_at=now())],
        [account(mfa_ready=False)],
    ],
)
def test_operator_denies_missing_ambiguous_inactive_or_unverified_accounts(rows):
    with pytest.raises(IdentityNotReady):
        verified_subject(
            operator_result(rows), subject=None, email="synthetic@example.test", require_mfa=True
        )


def test_operator_resolves_verified_uuid_and_parameterizes_email():
    row = account()
    session = operator_result([row])
    address = "quoted'address@example.test"
    assert verified_subject(session, subject=None, email=address, require_mfa=True) == str(
        row["id"]
    )
    statement, params = session.execute.call_args.args
    assert address not in str(statement) and params["email"] == address


def test_revoked_mfa_is_not_accepted_but_ingestion_has_separate_policy():
    row = account(mfa_ready=False)
    assert verified_subject(
        operator_result([row]), subject=str(row["id"]), email=None, require_mfa=False
    ) == str(row["id"])
