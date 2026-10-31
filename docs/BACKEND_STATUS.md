# Backend implementation evidence

Updated 25 October 2026. This is an implementation/evidence register, not a certification or a claim that the full project is finished. The proposal remains the requirements baseline. Backend/database work is the present phase; the frontend and genuine AI/ML phase remain distinct.

## Current verification status — 25 October

An earlier 128-test run finished with **112 passed, 15 failed and 1 intentionally skipped**. Do not use the older passing runs below to describe this run as successful. Failures included lost database connections, storage-unavailable responses and expired call transitions. The targeted rerun passed all **38 workflow/session checks** in 605.66 seconds, including every previously failing test (`backend/test-results-workflow-recheck.xml`). A rerun alone does not establish that the original connectivity cause is fixed. A subsequent 135-test run was interrupted after a connection-check failure. Its trace exposed a psycopg operational error masked by an autocommit-restoration exception. The backend now classifies that error chain as a disconnect only during pre-checkout validation, allowing the pool to replace the connection without replaying any in-flight transaction. The exact regression and execution-status scenario both passed (41.39 seconds; `backend/test-results-ping-recovery.xml`). The subsequent full regression completed with **135 passed and 1 intentionally skipped** in 1498.46 seconds (`backend/test-results-backend-recovery-full.xml`). The skipped test is the opt-in load test. This is the latest completed full-suite result, before the 24 October fixes below.

Added allowlisted database diagnostics to API and worker logs (error category, SQLSTATE, connection-invalidated flag and request/job identifier). SQL text, parameters, credentials and driver messages remain excluded. Five diagnostic-redaction unit checks passed. New recovery coverage injects a failed write and terminates only the test request’s own database connection, then verifies rollback and idempotent recovery; **both checks passed** against Supabase in 84.05 seconds (`backend/test-results-storage-recovery.xml`).

The previous rebuilt local API and worker passed 62 local acceptance checks without repeating the separate worker-stop alert exercise. The connection-recovery fix and patched certificate bundle are now in the running API and worker; all 22 source modules match the workspace. The source scan and Python dependency audit have no findings. The API image scan is clean after refreshing Alpine packages; gateway/monitoring Go security rebuilds remain in progress.

These workflow suites use `SimulationScorer`, which makes no AI API calls. Call replies are deterministic EN/AR templates; introductory audio uses local `espeak-ng`. These are workflow simulation features, not a trained conversational model.

## Backend behavior fixes — 24 October

- Fixed the review finding that browser-session rotation reset the per-user request allowance. Cookie and bearer requests now share the issuer/subject bucket; expired or unknown cookies remain rejected before creating rate-limit storage.
- Fixed the operator verification command to read its settings from the supplied configuration file. Removed the hidden dependency on a second checkout-specific file and updated the operating guide.
- Added regressions for mixed cookie/bearer limits, cookie rotation, separate users, and configuration loading outside the checkout. Added a complete declined-call/rejected-action scenario checking no execution, audit records, and reviewed case resolution.
- The 24 October broad targeted run stopped without a final result; it is not counted as a pass. The focused acceptance run on 25 October passed **all 23 tests** in 900.20 seconds (`backend/test-results-backend-acceptance.xml`), including English/Arabic end-to-end workflows, declined and unanswered calls, unavailable-scoring manual review, action cancellation/concurrency safeguards, and both review-finding regressions. Ruff lint/format and strict mypy checks passed. The existing Starlette test-client deprecation warning remains. The rebuilt local API and worker passed **62 local acceptance checks**, and all **22 source modules** in both services match the workspace (`docs/evidence/runtime-source-verification.json`). These checks exercise real backend handlers and the isolated Supabase test schema with synthetic identities, not real-user Supabase sign-in or a trained ML model.

## Delivered

| Area | Implemented behavior | Evidence |
|---|---|---|
| API foundation | FastAPI, strict Pydantic contracts, bounded inputs, redacted errors, protected OpenAPI | Ruff, strict mypy, validation/property tests |
| Authentication/session boundary | Supabase JWT verification, provider session lookup, database-authoritative roles, MFA age, opaque browser sessions, CSRF | Token/session/role tests; actual cloud user/MFA test pending |
| Transaction ingestion | Integer money, UTC timestamps, single/atomic batch ingestion, payload-bound retry keys | Real PostgreSQL duplicate/concurrency/rollback tests |
| Cases and investigations | Assignment, scoped historical evidence, notes, start/cancel/resume, final reviewed decisions | Cross-team/case denials, restart and evidence-boundary tests |
| Retained simulated phone feature | EN/AR, participant binding, bank/customer turns, answer/decline/hangup/timeout, protected introductory audio | Both language API scenarios; offline speech generation verified without network |
| Approval and execution | Strict boolean, current evidence, expiry, recent MFA, independent supervisor, bound targets, durable simulated receipts | Coercion/replay/independence/crash/atomic-audit tests |
| Queue and recovery | PostgreSQL claim locks, transactional acknowledgement, bounded retry, failed-job review/retry | Multi-worker and failure/replay tests |
| Policy and ML handoff | Independent risk-policy activation, policy applied/recorded in scoring, model review, hashed reviewed-label export, drift-result storage | Policy/snapshot/model/drift workflow tests; no actual training/scoring claim |
| Database protection | Private schema, least privilege, RLS, immutable evidence, foreign-key indexes | Supabase advisors and live runtime privilege checks |
| Operations | Containers, health/queue metrics, redacted logs, locked dependencies, operator tools, CI, runbook | Images built; running API ready against Supabase; dependency audit |

Supabase **Senior Porject** is active. The application uses its private `atm` schema; isolated tests use `atm_test`. The local database container, volume, bootstrap/migration scripts and demo-token CLI were removed at the user's request. A private backup of earlier synthetic demo data was preserved.

The live session-pooler connection passes full certificate/hostname verification. Effective statement timeout is 10 seconds. The application login can ingest but cannot modify principals, change schema, or update/delete audit records. Supabase `anon` and `authenticated` roles cannot access application tables. Unrestricted legacy authenticated policies were removed without deleting the original tables/data.

## Verification record

- Before the Supabase-only switch: 66 tests passed against PostgreSQL, lint/format checks and strict type checks passed. English and Arabic workflows completed through the running API and worker. A local restore compared all 20 application tables successfully. These are historical results, not cloud backup evidence.
- Supabase-only integration suite: **all 66 checks passed across two runs**. The main run passed 65 and lost a database connection during the final fixture setup (19m23s). After enabling stale-connection validation/recycling and batching fixture inserts, that test and the runtime-timeout check both passed (21.59s). Evidence: `backend/test-results.xml` and `backend/test-results-retry.xml`. This is not a claim of one uninterrupted 66-test cloud run. Static lint/format and strict type checks also passed. A Starlette test-client deprecation warning remains; it does not affect runtime behavior.
- Supabase Auth JWKS endpoint responded successfully and publishes an ES256 key. Real-user sign-in/MFA remains unverified.
- Runtime Supabase permission/TLS/timeout verification passed; API `/health/ready` returns `ready` with Supabase as its only database.
- Dependency audit repeated on 14 October: no known vulnerabilities in audited dependencies; the editable project itself is not covered by that dependency scanner.
- Supabase security advisor: no findings. Performance advisor: only informational unused-index notices; no missing foreign-key indexes remain in application or test schemas. This checks specific patterns and does not establish application compliance or platform patch currency.
- GitHub Actions workflow is configured; no hosted run is claimed until executed.

## Backend integrity increment — 13 October

- Added cancellation with a required reason, current version, case scope and recent authentication. Cancelling an approved action requires a supervisor; execution receipts cannot be cancelled or overwritten.
- Prevented closing/reassigning a case or changing evidence while an approved action remains unfinished, including a failed execution job. This closes the gap between approval and execution.
- Made workers refresh approval under the case lock and recheck evidence before execution. Cancellation and execution races have one valid outcome; cancelled work cannot later execute on retry.
- Added database protections for immutable proposal details, valid transitions and receipts bound to approved actions. Migration revision **0006** is applied to both Supabase schemas.
- Added explicit execution status for waiting approval, pending work, failure and cancellation.
- Tightened the separate operator tool: staff grants require an existing, verified, active Supabase user with verified TOTP. Email lookup is parameterized; revocation remains possible after provider deletion. The requested supervisor account still does not exist, so no account grant or invitation is claimed.
- Verified **20 targeted checks across two runs**: 11 cloud integration/regression tests (including cancellation races, stale worker state, tamper rejection and both call languages) in 5m26s, followed by the cloud execution-status regression and eight provisioning unit tests in 32s. Evidence: `backend/test-results-actions.xml` and `backend/test-results-provisioning-status.xml`. Lint, formatting and strict type checks passed. This increment does not claim a rerun of the entire suite.
- Supabase security advisor after the migration: no findings. Rebuilt and restarted both API and worker successfully after recovering the Docker engine. The updated API returned `{"status":"ready"}` against Supabase revision 0006, and both services were running without startup errors. Only API and worker run on this computer; the database remains exclusively on Supabase.

## Backend application increment — 14 October

- Implemented richer synthetic transaction inputs, immutable 1h/24h/7d feature snapshots, currency-safe aggregates and explicit cold-start/late-event handling.
- Failed scoring now creates an unscored manual-review case, so the investigation, call, approval and decision workflow remains usable while ML is unavailable.
- Added filtered/risk-ranked case queues, scoped transaction search/status, dashboard aggregates, coherent case summaries and team selection endpoints.
- Added structured investigation findings with validated evidence references, participant-bound EN/AR verification responses, and case call history.
- Added the bounded HTTPS ML adapter with model approval/identity checks and five-contribution response validation. It is implemented but remains disabled in the deployed configuration until a genuine service is supplied.
- Added checksummed bounded audit downloads, training handoff listing/cancellation, feature-aware label exports and immutable dataset snapshots. Card blocking now requires and binds the transaction's synthetic card ID.
- Migration revision **0007** is applied to application and isolated-test schemas. The security advisor returned no findings after migration on 14 October.
- Historical `Senior Project V1.1 copy.zip` informed requirements; its implementation, credentials and embedded agent instructions were not imported. See [BACKEND_APPLICATION.md](BACKEND_APPLICATION.md) for behavior and API usage.

## Final verification — 17 October

- Recovered the completed 14 October test reports and compared their distinct test identities with the current collection: **116 checks, all with a passing latest result**. This combines the full regression and targeted follow-up runs, not one uninterrupted 116-test run.
- The full cloud regression ran 113 checks: 111 passed and two test assertions failed. One expected the wrong exception class for PostgreSQL's deliberate permission/trigger denial. The other crossed a real minute boundary while checking a durable rate limit. The assertions were corrected to verify the actual SQLSTATEs and control only the limiter clock; application protections were not relaxed.
- All six follow-up cloud checks passed in 141 seconds, including those corrections, legacy ingestion retry compatibility, independently approved remote-model enforcement, concurrent duplicate ingestion, and the complete unscored review/call/action/decision workflow. Evidence: `backend/test-results-backend-followup.xml`.
- All 24 final scoring contract checks passed, including compressed-response rejection and the total deadline for slow responses. These use a mock HTTP transport; they do not establish the availability or quality of a real model service. Evidence: `backend/test-results-provider-contract.xml`. The full-run evidence is `backend/test-results-backend-full.xml`.
- Lint, formatting, strict type checks for all 20 source modules, and patch whitespace checks passed again on 17 October. The current suite collects 116 tests. The known Starlette test-client deprecation warning remains.
- Restarted Docker Desktop and confirmed both existing API and worker containers are running. The API returned `{"status":"ready"}` against Supabase revision **0007** on 17 October. Only the API and worker run locally; Supabase remains the sole database and deployed identity provider.
- Compared SHA-256 hashes of all 20 application source modules inside each running container against the workspace; both match the final tested source.
- Genuine scoring remains disabled. Real-user login/MFA, external hosting and the qualification gates below are not represented as passed by these checks.

## Local demonstration and batch increment — 20 October

- Added local HTTPS, private file-mounted secrets, password-protected system health, private aggregate metrics and six alert rules. The application remains on this computer; Supabase remains the only database and identity provider. No application hosting was published.
- The 17 October full regression passed **122 tests** in 22m32s. A subsequent six-test local-operations run also passed, including a new configuration-error credential-redaction check. The 20 October targeted security/configuration/provider run passed **46 tests**.
- Replaced repeated per-item batch locks/queries/inserts with shared ordered locking and bulk persistence. **Five real-Supabase regressions passed**: maximum 100-item batches, order-preserving retries, overlapping batches/single requests, conflicting batch atomicity and legacy single-request concurrency. A 100-item batch used seven database statements, accepted in **5.77 seconds**, and produced exactly 100 transactions/jobs/audit entries after retry. This is a bounded acceptance measurement, not 100 TPS or end-to-end scoring latency.
- Supabase remains active on database **17.6.1.166** and schema **0008**. Security advisors returned no findings after migration on 20 October; performance advisors returned only informational unused-index notices. Indexes supporting foreign keys and infrequent workflows were retained instead of removing them based on a small demo workload. [Advisor guidance](https://supabase.com/docs/guides/database/database-linter?lint=0005_unused_index).
- The requested supervisor is still not verified in Supabase Auth. Real sign-in, MFA and independent-person acceptance remain pending.
- Applied governed review-capacity and configurable-severity settings in revision **0008** to both schemas. Approved settings are immutable; changing them requires a new independently approved version. Recorded severity stays attached to the original transaction, and exceeding the daily target never suppresses cases. The migration helper cancelled automatically twice; after confirmed user authorization, the same SQL plus migration history was applied atomically through the SQL connection and verified.
- Final local acceptance passed **64 checks**, including worker outage/recovery, TLS, unauthorized access, protected monitoring assets, write denial and container isolation. Health endpoint load had zero errors; those numbers are not transaction/scoring throughput. Both running API and worker matched all 22 workspace source modules.
- Final API image scan reported no findings. Gateway/monitoring scans reported no critical/high/medium/low findings; remaining OpenPGP module notices were reviewed against build-dependency manifests and the affected packages are absent. See `evidence/container-audit.json`. This is not an independent penetration-test claim.
- Added connection recycling and TCP liveness settings to recover stale Supabase connections. Server query/lock/idle transaction deadlines remain enforced; dropped-network detection also depends on the operating system's supported TCP options.
- Requirement-by-requirement scope is recorded in [REQUIREMENTS_TRACEABILITY.md](REQUIREMENTS_TRACEABILITY.md); local controls, scan coverage and remaining platform/access limitations are in [LOCAL_SECURITY_REVIEW.md](LOCAL_SECURITY_REVIEW.md).

## Security control mapping

This maps implementation evidence to selected control areas. It is not a complete ASVS requirement-by-requirement assessment. Status refers to this configuration, not an organizational certification. Detailed source references and scope caveats are in `BACKEND_PLAN.md`.

| Area | NCA ECC 2-2024 | SAMA CSF | Status and evidence / gap |
|---|---|---|---|
| Secure design/development | 1-5, 1-6 | 3.2.1, 3.3.6 | Implemented: architecture/threat model, typed boundaries, reviewable migrations, tests. Independent review remains. |
| Identity and scoped permissions | 2-2 | 3.3.5 | Implemented/tested authorization logic. Real account enrollment, MFA, access-review process and key rotation remain. |
| Application/ingress protection | 2-15 | 3.3.6 | Verified strict validation, access denials, versions, CSRF. Local TLS/access/abuse checks implemented; real frontend checks remain. Public WAF is outside this local demo scope. |
| Encryption/data handling | 2-7, 2-8 | 3.3.9 | Verified DB TLS/hostname and restricted credentials. Backup encryption, residency and retention approvals remain. |
| Security logging | 2-12 | 3.3.14 | Redacted HTTP logs and DB-protected business audit implemented. Central log archive, 12-month security-event retention and alert-delivery exercise remain. |
| Recovery | 2-9, 3-1 | Separate business-continuity requirements apply | Worker recovery tested. Supabase backup/PITR and cloud restore/RPO/RTO are unqualified. |
| Vulnerability handling | 2-10, 2-11 | 3.3.17 | Dependency audit/test evidence available. Image/source/dependency scans and platform compatibility review performed; managed upgrade and independent penetration test remain. |
| Incident response | 2-13 | 3.3.15 | Runbook written. Named organizational owners, tabletop exercise and notification process remain. |
| Suppliers/hosting | 4-1, 4-2 | 3.4.1–3.4.3 | Supabase data flow documented; offline voice avoids a speech cloud. Singapore location/contract/applicability approvals remain. |

## Explicit release gates

1. **Initial identity:** the requested supervisor email does not yet exist in Supabase Auth. Create/verify it, enroll MFA, provision its UUID and exercise actual login, revocation and independent approvals with a second authorized reviewer.
2. **Database patching:** the managed project reports PostgreSQL 17.6. Current upstream 17.11 includes security fixes. Confirm Supabase's supported patched release/backports and upgrade the project through its supported controls before a shared security-qualified release. An advisor's empty result does not resolve this. [PostgreSQL 17.11 release notes](https://www.postgresql.org/docs/17/release-17-11.html).
3. **Local presentation:** local HTTPS, restricted file secrets and private monitoring replace the earlier public-hosting gate at the user’s request. Browser certificate trust still needs explicit permission; do not publish this application as part of the senior-project demo.
4. **Recovery/retention:** demonstrate Supabase cloud restore, approved encrypted/offsite retention and separately controlled audit archival; establish evidence for RPO/RTO.
5. **AI/ML phase:** implement and evaluate genuine supervised/anomaly models, calibrated fusion/explanations, training and drift computation. Versioned backend account features and the approved-service connection are implemented. Present routes store and govern handoff data; they do not perform these computations. Jev remains an optional future provider. The deployed scorer remains disabled; exhausted attempts create explicitly unscored manual-review cases, never normal verdicts.
6. **Frontend phase:** connect the existing phone experience and analyst dashboard to the scoped API and real login/MFA flow. No browser-only security rules should be relied on.
7. **Qualification:** run actual-model load/latency tests on the specified hardware and complete applicable ASVS Level 1/2 checks plus independent security testing. No 100 TPS, 200 ms, full ASVS or Saudi compliance claim has been made.

The system is a functioning backend/database implementation with explicit AI and deployment boundaries. Calling it “bank production-ready” or “NCA/SAMA compliant” at this point would be inaccurate.

## Certificate security update — 22 October

The latest image scan found GHSA-6q48-6c7w-9gw2 in certifi 2026.7.22. The lockfile and project constraint now require certifi 2026.10.19 or later, and the local test environment is synchronized to 2026.10.19. Rebuild and final scan verification are pending. Advisory: https://github.com/advisories/GHSA-6q48-6c7w-9gw2. Previous clean scans were point-in-time results, not evidence that the newly disclosed finding was absent.
