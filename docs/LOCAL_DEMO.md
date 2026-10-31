# Local senior-project demonstration

This deployment is for a demonstration on one computer, with synthetic transactions and simulated bank actions. Nothing is published to an external application host. The existing **Senior Porject** Supabase service remains the database and identity provider, as previously requested, so an internet connection is still required. There is no local database fallback.

## Start and stop

Start Docker Desktop, then from the project directory:

```sh
backend/.venv/bin/python backend/scripts/demo.py start
```

This prepares the private configuration, builds the pinned services, and waits for their health checks. Initial security builds download official source/dependencies and take longer; later starts reuse build layers.

- API: **https://localhost:8443**. `/health/ready` confirms database/schema access. Business endpoints still require real Supabase identity, role membership and MFA.
- System health: **https://localhost:9443**. The username and generated password are in the private `.runtime/monitor-access.json` file. Do not put them in slides, source control or shared screenshots.
- Status: `backend/.venv/bin/python backend/scripts/demo.py status`.
- Stop: `backend/.venv/bin/python backend/scripts/demo.py stop`. This preserves Supabase and monitoring history.

Only the gateway publishes ports, bound to **127.0.0.1**. Other computers on the network cannot access them. The earlier plain-HTTP port 8001 is removed. Internal API, worker exporters and Prometheus have no published ports. Do not change these bindings to `0.0.0.0` for a classroom demonstration.

## HTTPS and private configuration

The certificate is a 90-day, self-signed **server certificate**, limited to localhost/loopback names. It is not a general certificate authority. Automated checks explicitly trust `.runtime/localhost.pem` and verify the hostname; they never disable certificate validation. Browser use additionally needs a user-approved local trust entry. The setup script does not change the operating system's trust store. If trust is not installed, do not bypass the browser warning: use the verified command-line check until the trust step is approved.

```sh
curl --cacert .runtime/localhost.pem https://localhost:8443/health/ready
```

`.runtime` is owner-only and ignored by Git. Credentials and the TLS key are owner-readable files, mounted only into the services that require them. Database/scoring secrets are absent from container environment settings. The original private `.runtime/supabase-app.env` remains the operator's configuration source; rerunning preparation copies its credentials into mounted files without displaying them. This is local secret handling, not an external vault or encryption-at-rest claim. Keep the computer locked and use its disk encryption controls.

The certificate setup refuses to reuse a certificate with fewer than seven days remaining. Renew before expiry by moving the old certificate/key to a private backup, rerunning preparation and replacing the approved localhost trust entry. Never commit private keys or trust a certificate received from another person as this project's certificate.

## Monitoring

The password-protected page displays API availability, worker activity, recent database access, queued and failed jobs, response time, request activity and alerts. It refreshes every 15 seconds. Unavailable or stale samples are displayed as unknown, not healthy. Response times measure time to response headers; they include application database work but are not ML inference measurements.

Prometheus keeps aggregate operational metrics locally for up to seven days, with a 256 MB retention target. Metrics contain matched route templates and status codes, not tokens, account IDs, case IDs, customer names or transcripts. Container logs rotate at three files of 5 MB per service. This is an operations history, not an immutable business audit archive; business evidence remains in Supabase.

Six alert rules cover missing processes, a stalled worker, stale queue statistics, failed jobs, delayed ready jobs and server errors. Alerts appear on this local page. They do not send email/SMS or provide an on-call service. Disabled ML scoring intentionally exhausts retries and opens unscored review cases, so failed scoring jobs can produce a real warning during the demonstration.

Verify the controls and the alert lifecycle:

```sh
backend/.venv/bin/python backend/scripts/demo.py verify
```

This briefly stops the synthetic worker, waits for the availability alert to fire, restarts it even if verification fails, and verifies recovery. Do this before the presentation rather than in the middle of a case walkthrough. It does not stop Supabase or alter application records.

## Performance and security evidence

The [local acceptance report](evidence/local-acceptance.json) records 64 passed gateway/access/isolation checks and bounded live-endpoint load measurements. The [container audit](evidence/container-audit.json) records final image identities, raw findings and package-reachability assessment. The [batch report](evidence/batch-performance.json) additionally records the optimized 100-item transaction intake with duplicate/atomicity checks. The [business-workload report](evidence/local-performance.json) measures real HTTPS transaction acceptance and durable worker processing against the isolated `atm_test` schema, using synthetic identities and a simulated scorer. It does not prove real-user MFA, real-model latency, large-dataset performance or sustained 100 TPS.

To repeat the bounded business load test, with no other integration test running:

```sh
ATM_RUN_LOAD=1 backend/.venv/bin/python backend/scripts/check.py tests/test_local_performance.py --junitxml=test-results-load.xml
```

The fixture validates the isolated test owner and its lack of application-schema access before clearing test data. Never point it at a different database role. The full regression also supports `ATM_RUN_LOAD=1`; without it, the load test is skipped deliberately.

See [LOCAL_SECURITY_REVIEW.md](LOCAL_SECURITY_REVIEW.md) for the implementation review, scanner coverage, remaining findings and database patch decision. An automated/self-review is not an independent penetration test or NCA/SAMA certification.

## Before presenting

1. Start the services and verify that all four show healthy.
2. Open system health and confirm the API, worker and database access are healthy.
3. Use only synthetic examples. Explain that phone calls and protective bank actions are simulated and genuine ML is not yet enabled.
4. Complete real Supabase user enrollment, MFA and operator role provisioning before attempting a real-user case walkthrough. Test identities are isolated test fixtures, not a sign-in bypass for the demonstration API.
5. Keep the machine online, prevent sleep during the presentation and have the test report available if the venue's network fails. Do not describe an offline report as a live system.
