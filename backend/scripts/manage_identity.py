"""Operator-only identity provisioning/revocation with an atomic audit record.

Requires the separate migration identity. The API/runtime cannot run this operation.
A subject must be copied from a verified identity provider, never from user metadata.
"""

import argparse
from urllib.parse import parse_qs, urlparse

from dotenv import dotenv_values
from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session

from atm_backend.cli import ROLES
from atm_backend.models import Audit, Principal, now
from atm_backend.provisioning import verified_subject

parser = argparse.ArgumentParser(description=__doc__)
parser.add_argument("operation", choices=["grant", "revoke"])
parser.add_argument("--env-file", required=True)
parser.add_argument("--issuer", required=True)
identity = parser.add_mutually_exclusive_group(required=True)
identity.add_argument("--subject", help="Verified provider UUID")
identity.add_argument("--email", help="Resolve an existing verified Supabase account")
parser.add_argument("--team", required=True)
parser.add_argument("--role", choices=ROLES, required=True)
parser.add_argument("--operator", required=True, help="Named operator or approved access ticket")
args = parser.parse_args()
url = dotenv_values(args.env_file)["ATM_MIGRATION_DATABASE_URL"]
if not url:
    raise SystemExit("A separate migration database credential is required")
parsed = urlparse(url)
if parse_qs(parsed.query).get("sslmode") != ["verify-full"]:
    raise SystemExit("Remote identity administration requires verified TLS")
if (
    not args.issuer.startswith("https://")
    or (args.subject is not None and not 1 <= len(args.subject) <= 200)
    or (args.email is not None and not 3 <= len(args.email) <= 254)
    or not 1 <= len(args.team) <= 64
    or not 1 <= len(args.operator) <= 100
):
    raise SystemExit("Invalid identity or operator parameters")
engine = create_engine(url, hide_parameters=True)
with Session(engine) as s, s.begin():
    if args.operation == "grant":
        try:
            args.subject = verified_subject(
                s, subject=args.subject, email=args.email, require_mfa=args.role != "ingestion"
            )
        except ValueError as error:
            raise SystemExit(str(error)) from None
    elif args.email:
        raise SystemExit(
            "Revoke by stored subject UUID; revocation must work after provider deletion"
        )
    row = s.scalar(
        select(Principal)
        .where(Principal.issuer == args.issuer, Principal.subject == args.subject)
        .with_for_update()
    )
    if row is None:
        if args.operation == "revoke":
            raise SystemExit("Identity does not exist")
        row = Principal(issuer=args.issuer, subject=args.subject, team=args.team, role=args.role)
        s.add(row)
    # Existing credentials are invalidated on every grant/change as well as revocation.
    row.team, row.role, row.active, row.valid_after = (
        args.team,
        args.role,
        args.operation == "grant",
        now(),
    )
    s.flush()
    s.add(
        Audit(
            team=args.team,
            actor=args.operator,
            kind="identity." + args.operation,
            object_id=str(row.id),
            request_id="operator",
        )
    )
    identity_id = row.id
print(f"Identity {identity_id}: {args.operation}; obtain a newly issued credential before use.")
engine.dispose()
