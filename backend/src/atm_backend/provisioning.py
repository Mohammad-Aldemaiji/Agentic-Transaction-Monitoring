"""Operator-only checks against provider-managed identity records.

These reads require the separately controlled operator credential. They are never
available to the API runtime and do not create users or send invitations.
"""

from uuid import UUID

from sqlalchemy import text
from sqlalchemy.orm import Session

from atm_backend.models import now


class IdentityNotReady(ValueError):
    pass


def verified_subject(
    session: Session, *, subject: str | None, email: str | None, require_mfa: bool
) -> str:
    if bool(subject) == bool(email):
        raise IdentityNotReady("Choose a provider UUID or email")
    user_id = UUID(subject) if subject else None
    rows = (
        session.execute(
            text(
                "SELECT u.id, u.email_confirmed_at, u.banned_until, u.deleted_at, "
                "EXISTS (SELECT 1 FROM auth.mfa_factors f WHERE f.user_id=u.id "
                "AND f.factor_type='totp' AND f.status='verified') AS mfa_ready "
                "FROM auth.users u WHERE "
                "(CAST(:subject AS uuid) IS NOT NULL AND u.id=CAST(:subject AS uuid)) OR "
                "(CAST(:email AS text) IS NOT NULL AND lower(u.email)=lower(CAST(:email AS text)))"
            ),
            {"subject": str(user_id) if user_id else None, "email": email},
        )
        .mappings()
        .all()
    )
    if len(rows) != 1:
        raise IdentityNotReady("Exactly one existing Supabase account is required")
    row = rows[0]
    if (
        row["email_confirmed_at"] is None
        or row["deleted_at"] is not None
        or (row["banned_until"] is not None and row["banned_until"] > now())
    ):
        raise IdentityNotReady("The Supabase account must be verified, active and not banned")
    if require_mfa and not row["mfa_ready"]:
        raise IdentityNotReady("Enroll and verify TOTP MFA before granting this role")
    return str(row["id"])
