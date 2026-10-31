# Rasid frontend

React + TypeScript + Vite, served by the existing local HTTPS gateway at **https://localhost:8443**. All financial records and writes go through the FastAPI backend; Supabase JS is used only for authentication.

## Run

Node.js 22.12+ is required. On a new checkout, copy `.env.example` to `.env.local` and supply the project's Supabase URL and **publishable** key. Never put a service-role key, database credential, or JWT signing key in a `VITE_` variable.

From the repository root:

```sh
backend/.venv/bin/python backend/scripts/demo.py start
```

This installs missing frontend dependencies, builds the frontend, and starts the local services. Browser trust of the local certificate must be handled by the user. No public hosting is configured.

## Accounts and roles

Use **Create an account** on the sign-in screen, verify the confirmation email, sign in, and enroll an authenticator. Supabase's email provider, signup policy and allowed redirect URLs must support this flow; configure `https://localhost:8443` as an allowed confirmation redirect. If the provider redirects elsewhere after verification, return to the workspace and sign in. Account creation never grants workspace access.

A trusted operator uses the existing identity-provisioning process in `docs/BACKEND_OPERATIONS.md` after email and MFA verification. Each account has one role. Use separate browser profiles for the analyst, independent supervisor, participant, and ingestion identities in a multi-person demonstration; tabs in one browser profile share the same session cookie.

- Analyst / supervisor: queue, evidence, findings, calls, action proposals, decisions; supervisors assign cases and independently approve actions.
- Participant: assigned simulated calls, English/Arabic replies and structured transaction verification.
- Ingestion: single/batch synthetic transaction intake and processing status.
- Auditor: scoped read-only case access, audit snapshot/export, policy/model history.
- Admin: processing recovery, policy proposals, model registration and dataset/result handoffs.

Provider tokens stay in memory. The backend uses an HttpOnly cookie; only the CSRF value is retained in tab-scoped session storage. Refresh preserves the current tab's session. A new tab or expired session may require sign-in. Sensitive approvals require recent MFA; use **Re-authenticate** when prompted. Mutations are never automatically retried. Unchanged transaction retries preserve their idempotency key.

## Verification

```sh
npm --prefix frontend test
npm --prefix frontend run build
npm --prefix frontend run format:check
```

The 25 targeted UI tests use mocked responses; they do not prove real-user sign-in or replace the backend's Supabase integration tests. The separate `tests/preview.html` is a development-only visual fixture with writes and external fetches blocked. It is not included in the production build. Actual ML training/inference and real-bank actions are not performed by the frontend.
