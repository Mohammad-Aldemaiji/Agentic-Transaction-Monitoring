# Local demonstration security review

20 October 2026. Scope: the synthetic-data FastAPI backend, Supabase application/test schemas, durable worker, local HTTPS gateway and local monitoring. This is an implementation review and automated assessment by the implementing agent, **not an independent penetration test**, formal ASVS assessment or NCA/SAMA certification.

## Verified controls

- Fixed Supabase JWT issuer/audience and asymmetric algorithms; provider session checks and database-authoritative active roles; recent TOTP for approvals. No roles from editable user metadata.
- Opaque browser sessions, HttpOnly/Secure/SameSite cookies, CSRF checks and bounded token/session lifetime. Protected business/OpenAPI routes; team/case/participant scope checks.
- Strict input limits, integer monetary values, parameterized queries, bounded provider calls and offline voice text passed through standard input without a shell.
- Atomic business state, jobs and audit writes; append-only business audit; immutable transaction input, feature/scoring evidence and training snapshots; dual approval and target-bound simulated receipts.
- Dedicated restricted runtime login, private application schema, RLS as defense in depth, no browser-role application table access, isolated test role unable to access the application schema. Verified database CA and hostname.
- Only loopback HTTPS ports are published. Non-root, read-only containers drop capabilities and cannot gain privileges. Database/key material lives in owner-restricted files and is mounted only where needed; it is absent from container environment settings and source control.
- Final local acceptance passed **64 checks**, including actual outage/recovery, protected static assets, denied writes, certificate/hostname validation and loopback-only ports. Private aggregate metrics exclude identities and raw URL identifiers. Password-protected local monitoring has no external analytics/scripts. Worker outage alert firing and recovery were exercised.

## Evidence and scanner boundaries

The 17 October full backend run passed 122 tests, with an additional configuration-redaction check subsequently passing. Five 20 October batch checks verify maximum-size batches, overlapping retries, atomic conflict handling and exactly-once database effects. Test authentication is isolated; this is not proof that the user's real account/MFA has been enrolled.

Bandit reported zero findings, with three documented narrow suppressions for fixed executable subprocess use and a private-container heartbeat path. The Python dependency audit reported no known vulnerabilities in scanned dependencies; it excludes the editable project itself. Original container scans revealed vulnerable vendor/base components, so the API moved to updated Alpine and the Go services were rebuilt with patched toolchains/dependencies. The [final image audit](evidence/container-audit.json) records image IDs and all findings. The API image has zero reported findings. Gateway and monitoring have no critical/high/medium/low findings; their remaining module-level OpenPGP notices are assessed below. Raw image reports remain private under `.runtime/scan`.

A module-level Go finding, [GO-2026-5932](https://pkg.go.dev/vuln/GO-2026-5932), concerns the unmaintained `golang.org/x/crypto/openpgp` packages. A module's presence alone does not demonstrate that these packages are compiled/reachable. The final [gateway](evidence/gateway-build-dependencies.txt) and [monitoring](evidence/prometheus-build-dependencies.txt) build-dependency manifests include no affected OpenPGP package. These module-level notices are recorded as not applicable to the compiled packages, rather than silently removed. No known fixed version is listed by the Go advisory.

## Managed database patch decision

The **Senior Porject** project was healthy on 20 October and still reports managed version **17.6.1.166**. Supabase security advisors returned no findings. That advisor result does not resolve platform patch currency.

The [Supabase patch announcement](https://supabase.com/changelog/postgres-15-19-17-11-breaking-changes) describes the 17.11 security release. Compatibility checks found no application/test ltree or affected float GiST indexes, custom non-extension selectivity estimators, or application stored-function legacy PGP cipher calls. Installed managed extensions were inventoried. This supports upgrade planning, not a claim that the provider's upgrade has occurred or every managed component was independently audited.

Use the platform's supported [Postgres upgrade procedure](https://supabase.com/docs/guides/platform/upgrading) after dashboard sign-in, confirm the offered release, preserve a verified backup, allow the announced downtime, then verify database TLS, restricted role login, schema readiness and regression checks. The available connector has no exposed upgrade operation; the dashboard was signed out. A pause/restore is not used as an unverified upgrade shortcut. Custom-role credentials require special care if a restore is involved.

## Remaining acceptance boundaries

- **Identity:** the requested supervisor account is not yet verified. Enroll real users and TOTP, provision roles with the restricted operator procedure, and exercise independent approval using a second authorized person. Do not bypass authentication with test tokens.
- **Browser certificate trust:** HTTPS works with an explicitly trusted certificate file. The attempted persistent macOS Keychain trust change was rejected by automatic approval review because it changes a security setting without specific permission. It has not been retried or bypassed; explicit permission remains necessary for that change.
- **Policy increment:** revision 0008 is applied to both schemas, including immutable policy evidence and validated review capacity/severity. The migration helper cancelled automatically; after the user confirmed the retry and clarified that cancellation was automatic, the reviewed SQL and migration record were applied together through the authorized SQL connection. Both revision markers and a clean security-advisor result were verified.
- **Performance:** proposal targets of 100 TPS and p95 200 ms are unachieved. Baseline and bulk acceptance measurements are retained; neither substitutes for actual-model scoring performance.
- **Independent assessment:** a different qualified assessor must provide independent security testing. This report cannot truthfully satisfy that independence requirement by relabelling the implementer's checks.
- **Recovery/compliance:** live Supabase restore, retention/offsite audit archive, organizational access reviews, incident exercises, regulatory applicability and Singapore residency approval remain unqualified. Local operations retention is seven days and does not satisfy regulatory retention merely by existing.

For a senior-project presentation, describe demonstrated behavior and these boundaries accurately. Do not use real financial/customer data or describe this build as a certified banking system.
