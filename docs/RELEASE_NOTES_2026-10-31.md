# Integrated review version — 31 October 2026

This proposed update brings the E05 supervised scorer and local advisory AI into a role-based transaction-monitoring application, with synthetic customer histories, phone verification, governed simulated actions and audit records. It does not execute real bank payments.

The current snapshot also includes presenter MFA and a bounded 24-hour presentation session. Role changes preserve its original deadline; provider/account revocation and logout remain effective. Runtime configuration and credentials are excluded.

The README now describes installation prerequisites, deployment profiles, every role, scoring versus priority, customer histories, verification, model limitations and tests. Mohammad Aldemaiji's original contribution and team attribution are preserved.

## Validation

- Backend: 188 passed across main/configuration runs; one optional load test skipped.
- Latest frontend retry: 29 passed; two worker-startup errors prevented complete validation.
- Live replay: 100 submitted, 87 cleared, 13 flagged; no scoring jobs pending/failed at observation.
- Live customer-history filtering, JSON display, investigation startup and call acceptance checked. This audit did not finish all role workflows.

## Open review items

1. Supervisor navigation cannot reach the presentation policy/model approval screens.
2. The authenticated HTTPS verification script retains an obsolete unauthenticated assertion.
3. Local AI advice can contradict itself; voice preparation may be slow; real Whisper inference is unverified.
4. Historical synthetic replay can overlap training. It cannot establish real-world fraud accuracy; live bank features, anomaly/fusion scoring and actual financial execution are outside this version.

These are disclosed review limitations, not claims of completed production qualification. See the root README for setup and detailed behavior.
