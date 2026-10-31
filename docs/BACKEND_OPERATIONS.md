# Supabase backend operating guide

Updated 17 October 2026. The selected deployment is a local senior-project demonstration; see [LOCAL_DEMO.md](LOCAL_DEMO.md). Supabase is the **only database and application identity provider**. There is no local PostgreSQL service, local database volume, SQLite fallback or demo-signing-key startup path.

## Run the backend

Prerequisites: Node.js 22.12+ for the frontend build; Docker Desktop for the API/worker containers; Python 3.12 and uv for developer tools. The Supabase project **Senior Porject** (`yxcysaudekjhtsqoqghd`, Singapore) must be active.

```sh
uv sync --project backend --frozen
backend/.venv/bin/python backend/scripts/demo.py start
curl --fail --cacert .runtime/localhost.pem https://localhost:8443/health/ready
```

This computer already has restricted credentials and the Supabase CA certificate in `.runtime`. On another computer, supply `.runtime/supabase-app.env` using `backend/deploy/shared.env.example` and place the official CA at `.runtime/supabase-ca.pem`. The local preparation script creates file-mounted secrets and a localhost certificate. Compose starts `api`, `worker`, `gateway` and `prometheus`; database migrations are explicit Supabase operations, never application startup actions.

The API is available over HTTPS on loopback port 8443; protected monitoring uses 9443. The backend account has no schema-change privileges, cannot grant roles, and cannot rewrite audit history. All containers run without root privileges, with read-only filesystems and no Linux capabilities. No JWT signing private key is mounted. Image bases are pinned by digest; Python dependencies are locked in `backend/uv.lock`.

`backend/deploy/Caddyfile` implements the local TLS gateway with bounded bodies, headers and timeouts. Nothing is published publicly, and no WAF or cloud-hosting claim is made. The configured frontend origin is `https://localhost:8443`; serve the later frontend at that origin or explicitly review an origin change. Do not enable wildcard hosts or CORS. Browser trust requires user approval; automated checks trust only the project certificate.

### API contract and client behavior

```sh
backend/.venv/bin/python -m atm_backend.cli openapi --output docs/openapi.json
```

`docs/openapi.json` describes all routes. `/api/v1/openapi.json` requires authentication. Ingestion returns 202 and a durable transaction ID; processing status is read separately. Every ingestion request needs an idempotency key; reusing a key with different content returns a conflict. Financial values are integer minor units and timestamps must include a UTC offset. Batch ingestion is atomic, capped at 100 items and 128 KiB per request.

Mutable case/call/proposal operations require `expected_version`. A conflict means refresh the object and review the current evidence before retrying. High-impact proposals need a different supervisor with recent MFA; string values such as `"false"` cannot satisfy an approval. Calls support English/Arabic, answer/decline/end/timeout, versioned bank/customer text turns and protected offline-generated introductory speech. The React workspace in `frontend/` uses these endpoints and is served at https://localhost:8443. See `frontend/README.md` for role-specific screens and onboarding.

## Supabase connection and schema

The verified session-pooler endpoint is `aws-0-ap-southeast-1.pooler.supabase.com:5432`, username `atm_app.yxcysaudekjhtsqoqghd`, database `postgres`. The direct IPv6 endpoint is not reachable from this network. **Use session pooling, not transaction pooling** with this adapter.

Connections enforce `sslmode=verify-full`. Supabase's official root certificate is downloaded from the URL used by its dashboard: `https://supabase-downloads.s3-ap-southeast-1.amazonaws.com/prod/ssl/prod-ca-2021.crt`. Its checked SHA-256 fingerprint is `807025AD50D4ED219D2C9C7D299C004F824EB00CF7F65AFEF607D07B72E6CAFA`. Never weaken TLS to work around an expired/mismatched certificate; verify current provider guidance during rotation.

```sh
backend/.venv/bin/python backend/scripts/verify_database.py --env-file .runtime/supabase-app.env
```

Supply the complete application configuration in that file, including the issuer, JWKS URL,
audience and HTTPS browser origin. For this host-side command, the database URL's CA certificate
path must be accessible on the host. The command does not require a second configuration file.

The adapter explicitly sets each session's statement timeout to 10 seconds, lock timeout to 3 seconds, idle transaction timeout to 15 seconds and timezone to UTC. The pooler ignored startup options, so effective settings are verified after connection. Each API/worker process has a five-connection bounded pool with no overflow; account for every process when sizing the Supabase connection budget.

Schemas and identities:

| Scope | Schema / identity | Boundary |
|---|---|---|
| Application data | `atm` / login `atm_app` | API and worker only; no identity or DDL writes |
| Runtime privilege group | `atm_runtime` (NOLOGIN) | Explicit table grants, RLS backend policy |
| Integration tests | `atm_test` / `atm_test_runtime` | Cannot access the `atm` schema |
| Test fixture setup | `atm_test_owner` | Owns only test objects; cannot access `atm` |
| Supabase Auth | Provider-managed `auth` | App reads only session identity/expiry columns for revocation checks |
| Original prototype | Preserved `public` tables | Browser grants and unrestricted authenticated policies removed |

RLS blocks direct browser Data API access. **Team and case authorization is enforced by the API; the application runtime role itself is not isolated to one team.** A compromised backend credential remains a significant trust boundary. Authenticated and anonymous Supabase browser roles have no privileges on application/test tables. No service-role key is placed in application configuration or frontend code.

The region remains Singapore. Use only synthetic project data; Saudi residency, outsourcing approval and institution-specific obligations have not been established.

## Identity setup and access lifecycle

The requested initial supervisor address is recorded privately in `.runtime/supervisor-access-request.json`. It was not present in `auth.users` when checked. No account, invitation delivery, MFA enrollment or successful real-user login is claimed.

Create/invite the account through [Supabase Auth users](https://supabase.com/dashboard/project/yxcysaudekjhtsqoqghd/auth/users), verify the address and enroll TOTP MFA. The operator tool resolves its identity and checks verified email, active/not-banned status and verified TOTP enrollment before granting staff access. Grant application membership through the separately controlled operator identity:

```sh
backend/.venv/bin/python backend/scripts/manage_identity.py grant \
  --env-file /secure/path/operator.env \
  --issuer https://yxcysaudekjhtsqoqghd.supabase.co/auth/v1 \
  --email YOUR_VERIFIED_EMAIL --team project --role supervisor \
  --operator ACCESS_TICKET_ID
```

The operator file contains `ATM_MIGRATION_DATABASE_URL` with a separately authorized owner credential and verified TLS. It must never be loaded into API/worker containers. Use `revoke --subject STORED_AUTH_USER_UUID` to disable membership. Revocation deliberately works even if the provider user was deleted; grant checks cannot block emergency revocation. Every grant/change/revocation invalidates older tokens and records an audit event atomically. The API admin role cannot grant itself privileges. Do not directly insert records into Supabase's managed `auth.users` tables.

Configure asymmetric JWT signing keys (ES256/RS256), the exact trusted issuer/JWKS URL, audience `authenticated`, access-token lifetime at most 900 seconds and MFA. The backend ignores user-editable role metadata, rejects anonymous/service-role tokens and checks provider session liveness on every request through the private `atm.provider_session_active` function. This boolean-only function binds the session to the verified JWT subject using transaction-local context; hosted Supabase does not support custom-role grants on its managed Auth schema. Readiness also exercises this boundary. Fresh approval uses the TOTP authentication time, not token refresh time.

The frontend should use the provider's PKCE login flow, then exchange its bearer token at `POST /api/v1/auth/session` with the configured `Origin`. The backend returns an HttpOnly Secure SameSite=Strict cookie and CSRF token. Include `Origin` and `X-CSRF-Token` for cookie-authenticated changes. Sessions cannot outlive the provider access token. Refresh/reauthentication belongs to the provider; rotate the backend cookie after obtaining a new credential. The backend stores no provider refresh token.

## Supabase-only verification

```sh
backend/.venv/bin/python backend/scripts/check.py
backend/.venv/bin/pip-audit --skip-editable --progress-spinner off
```

The test runner uses `.runtime/test.env` with the dedicated cloud test-role credentials. Test setup checks the actual owner role and proves it lacks access to `atm` **before** clearing `atm_test`. ORM schema translation keeps all test objects in `atm_test`. Fixed test signing keys exist only inside the test process; they cannot be configured in the running application profile. Tests exercise the real Supabase PostgreSQL database while simulating identity-provider tokens. That does not replace the real-user Supabase Auth/MFA acceptance test.

Do not run two integration suites at once: they share the isolated test schema. CI serializes that job. Test data is synthetic and disposable; never copy real records into it. Integration tests need cloud network access and take longer than unit tests.

GitHub Actions runs static checks, configuration tests, dependency audit and an offline speech/container build. The full cloud suite is a manually dispatched, serialized job using a protected `supabase-integration` environment and `ATM_TEST_MIGRATION_URL` / `ATM_TEST_DATABASE_URL` secrets. Configure those with the dedicated test roles only. No secrets are exposed to pull-request code by that job. Hosted CI execution has not been claimed before a run exists.

## Migrations, backup and recovery

`supabase/migrations` is the only schema-migration source. The deployed application schema revision is 0008 (stored in `atm.alembic_version`, retained as a version marker). Local Alembic tooling and local database bootstrap scripts were removed. Future application changes must also update the isolated test schema, then verify both. Generate filenames with `supabase migration new`, review grants and policies, apply explicitly and run security/performance advisors. Do not replay the baseline over existing tables.

Prefer additive migrations and forward repair. Before a shared migration, verify a cloud backup and restoration plan; roll back an application image only while its schema remains compatible. Restore into a separate cloud database/project, verify counts/content/access policies and only then switch application connections. Never use a schema downgrade that re-exposes private records.

The old local demo database was backed up to `.runtime/local-demo-final.dump`; its archive listing was checked. Its containers and database volume were removed. This backup is preserved only to avoid discarding earlier synthetic work and is not a runtime dependency. A previous local restore exercise is historical evidence, **not** proof of Supabase backup/PITR. Cloud backup scheduling, offsite encryption, restore rehearsal and purchased-plan recovery guarantees still need qualification. Proposed shared objectives: RPO ≤15 minutes, RTO ≤4 hours; neither is claimed achieved.

Jobs, business changes and simulated receipts commit together. A pre-commit worker crash rolls back the claim and result so another worker can retry. Retries back off and stop after five attempts. Team admins inspect failed jobs and request explicit retries after correcting the dependency. Real bank effects would require an idempotent provider and reconciliation before replacing the simulated adapter.

## Monitoring, incidents and retention

Monitor `/health/live`, `/health/ready`, team-scoped admin job metrics, oldest ready-job age, failures, HTTP 401/403/429/5xx, database connections and storage use. HTTP access logs contain only request ID, route template, status and elapsed time. Failure logs additionally allow a fixed error category, a validated five-character SQLSTATE, a connection-invalidated flag, and request/job identifiers; they exclude SQL, parameters and driver messages. Keep Uvicorn access logs and SQL echo disabled; they can expose request identifiers or values. Voice errors preserve text interaction.

For an incident: restrict ingress, revoke provider sessions and local principal membership, preserve request IDs/evidence, pause affected workers if action integrity is uncertain, rotate credentials, reconcile work and verify fixes before reopening access. Assign a human incident owner. Regulatory communications are institutional responsibilities, separate from fraud-case handling.

Credential rotation: provision a new restricted cloud login, update the secret manager, deploy and verify, then revoke the old login and terminate its sessions. Use Supabase's supported overlapping signing-key rotation. Test-role credentials must never inherit application privileges.

Audit exports use a fixed `through` cursor: preserve it across pages, advance `after=next_cursor` and stop when `has_more=false`. Reads are audited. Concurrent sequence IDs may commit out of order, so authoritative archival needs a consistent database snapshot or reconciled repeated exports. API pagination alone is not an immutable archive.

Runtime roles cannot update/delete audit history, decisions or call turns. Security logs, business evidence and audio require separately approved retention/legal-hold schedules. Expired browser sessions are removed after one day and old rate buckets are cleared. Business evidence is not silently purged. NCA's security-event retention minimum does not automatically apply to every voice clip. Central retention, alert delivery, WAF and privileged-access review remain shared-deployment controls to implement and verify.

## Safe action cancellation and failure recovery

While an approved action has no execution receipt, the API blocks case closure, reassignment, evidence notes, investigation changes, new calls and further approvals/actions. Wait for the receipt or cancel the outstanding approval before changing its evidence. This includes a failed execution job; a failed job is not a successful bank action.

`GET /api/v1/action-proposals/{id}/execution` returns `AWAITING_APPROVAL`, `PENDING`, `FAILED`, `SUCCEEDED`, `REJECTED`, `EXPIRED` or `CANCELLED`. Failed/pending jobs include attempts and a redacted error code. An inconsistent completed job without a receipt produces an error rather than reporting success.

`POST /api/v1/action-proposals/{id}/cancel` requires `expected_version` and a reason. A proposer may cancel their own pending proposal; cancelling another person's proposal or an already approved action requires a supervisor. Fresh authentication and case scope are enforced. An executed action cannot be cancelled. The reason is retained in the immutable case event and the acting identity in the audit log. Cancellation is not a reversal of a bank effect.

A worker refreshes the approval under the same parent-case lock used by cancellation. If cancellation wins the race, the worker acknowledges the job without an execution; if execution wins, cancellation is rejected. Workers also recheck evidence version and case state before recording an effect. The database rejects changed proposal targets/evidence, invalid state transitions and receipts that do not match an approved action. Existing approved evidence is never rewritten to make a retry succeed: cancel it and create a new proposal for the current evidence.


## Backend application workflows and optional scoring service

See [BACKEND_APPLICATION.md](BACKEND_APPLICATION.md) for transaction context, features, unscored review, ranked queues, customer verification, findings and exports. The deployed `ATM_SCORING_MODE=disabled` remains deliberate. A failed scoring job now opens a manual-review case after five attempts. Once a case exists, the admin retry API refuses to rescore its transaction so that reviewed evidence cannot change.

The implemented remote adapter can be enabled later with `ATM_SCORING_MODE=remote`, `ATM_SCORING_URL` (fixed HTTPS endpoint without credentials/query/fragment), `ATM_SCORING_TOKEN` (secret-manager value), `ATM_SCORING_MODEL_NAME`, `ATM_SCORING_MODEL_DIGEST` (64-character SHA-256) and optional `ATM_SCORING_TIMEOUT_SECONDS` (default 5, maximum 10). Register and independently approve the same non-simulation model in each team's registry first. No network request is sent for an unapproved model. The API and worker must receive the same configuration. Restart the worker after a reviewed deployment configuration change; changing a registry record alone does not deploy a service.

Changing provider configuration is not model qualification: verify model artifact identity, evaluation/calibration, five-feature explanations, measured latency and the service's data-handling boundary before enabling it. Provider errors and invalid responses follow the same bounded retry/manual-review path. Runtime logs must never include the provider token or raw response.

## Distinguishing storage and processing failures

A `storage_unavailable` API response is a failed database operation, not an AI verdict. Correlate its request ID with the failure log: `pool_timeout` means no pooled connection became available; `database_error` may include a SQLSTATE and `connection_invalidated`; `processing_error` in worker logs indicates a non-storage processing failure. Do not infer a specific provider outage from a generic processing error. Never log raw exceptions to diagnose customer transactions.

After an ingestion failure, retry the same unchanged payload with the original idempotency key. Do not generate a new key: a response may be lost after a successful commit. The backend returns the existing transaction for an identical replay and rejects conflicting content. Failed statements roll back the transaction and queued job together. The worker retries failed processing with bounded backoff, and exhausted scoring opens a manual-review case.

The simulation scorer and templated EN/AR calls make no external AI requests. Introductory audio is generated with local `espeak-ng`; voice failure leaves text available. Supabase remains a network dependency even when all AI/voice processing is local.

The connection pool also handles a psycopg edge case where restoring autocommit masks a failed pre-checkout ping. A bounded exception-chain check marks that connection invalid only during pre-ping; SQLAlchemy replaces it before application work starts. This follows the documented `handle_error` disconnect hook: https://docs.sqlalchemy.org/en/20/core/pooling.html#supporting-new-database-error-codes-for-disconnect-scenarios. Errors during a transaction still fail and roll back; they are never automatically replayed.

On 22 October 2026, the live Supabase access-token expiry was changed from 3600 to 900 seconds and verified after reloading the dashboard. Refresh-token replay detection remains enabled with its existing 10-second reuse interval. The backend continues enforcing its own session expiry and MFA checks; paid provider session settings were not enabled. Evidence: `evidence/supabase-token-expiry.jpg`.
