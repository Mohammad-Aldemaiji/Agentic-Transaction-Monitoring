# Backend application and integration guide

Updated 14 October 2026. This describes implemented Python application behavior. Supabase provides durable storage and identity; it does not replace the backend's business logic.

## What the backend does

The API accepts a synthetic transaction, validates the caller and input, records it idempotently, and queues processing. The worker computes a versioned feature snapshot and calls the configured scoring adapter. Successful scoring applies the independently approved team risk policy and creates a case when review is required. After five unsuccessful processing attempts, it creates an **unscored manual-review case**. No score or fraud prediction is invented for that case.

An analyst can search the queue, inspect account history, add notes and structured findings, conduct an English or Arabic simulated verification call, propose a protective action, and record a final decision. The supervisor can assign cases and independently approve sensitive actions. The worker executes only the approved simulated action. The auditor can inspect protected evidence and download a bounded, checksummed audit export. Administrators manage processing failures and model/policy handoffs.

## Reference used

`Senior Project V1.1 copy.zip`, supplied by the user, was inspected as historical reference. Its transaction schemas, feature engine, scoring orchestration, dashboard statistics and account-history routes informed this implementation. Its embedded agent instructions were not adopted. The new backend does not import its environment, credentials, dependencies, memory repositories, Kafka/Redis stack or stub scoring behavior.

The SPAP proposal was located at `Desktop/Current Sem/Senior Project/SPAP-Form - Detection of Suspicious Financial Transactions Using ML.docx`; its adjacent extracted Markdown was reviewed against the earlier requirements record.

## Modules

| Module | Application responsibility |
|---|---|
| `contracts.py` | Strict request fields, bounded strings, money/timestamps, workflow commands |
| `security.py`, `sessions.py` | Supabase token verification, live role/session checks, MFA, browser session and CSRF |
| `services.py` | Case access, assignment, findings, calls, customer responses, approval, cancellation and decision rules |
| `features.py` | Point-in-time 1-hour, 24-hour and 7-day account features |
| `scoring.py`, `adapters.py` | Typed provider boundary, fixed HTTPS destination, bounded responses and model identity |
| `worker.py` | Processing, scoring policy, retries, manual-review escalation, expiry and simulated execution |
| `queries.py` | Authorized queue filtering/ranking, cursor pagination and dashboard aggregates |
| `workflows.py` | Application read models, customer verification, findings, training cancellation, audit export |
| `management.py` | Risk policies, model approval, training snapshots, drift reports and job recovery |
| `models.py`, `db.py` | Persistent state, transaction/session boundaries and restricted Supabase connections |

## Transaction contract

`POST /api/v1/transactions`, ingestion role, with an `Idempotency-Key` header:

```json
{
  "external_id": "synthetic-tx-001",
  "account_id": "synthetic-origin-001",
  "amount_minor": 125000,
  "currency": "SAR",
  "occurred_at": "2026-10-01T08:00:00Z",
  "channel": "TRANSFER",
  "context": {
    "destination_account_id": "synthetic-destination-002",
    "merchant_category": "6012",
    "device_id": "synthetic-device-001",
    "location_country": "SA",
    "location_city": "Riyadh"
  }
}
```

Amounts use the selected currency's minor units, never floating-point money. Originating account is `account_id`. Context is optional for compatibility; missing context remains explicitly unknown. `card_id` is also supported for CARD/ATM transactions and is required before proposing `BLOCK_CARD`. A card block targets that card, not the account. Labels, client-computed features, scores, reviewer identities and approval states are not ingestion inputs.

The response is `202` after durable acceptance. Omitting optional context or sending only unknown/null context preserves the original retry digest; adding actual context is a content change and cannot reuse an existing retry key. `GET /transactions/{id}/processing` distinguishes pending, scored and failed processing and exposes the related review case when one exists. `GET /transactions` provides bounded status/account filtering. Reviewers see only transactions belonging to cases they may access; ingestion identities see their team's ingestion records.

## Features and scoring

The `account-windows-v1` snapshot includes counts and same-currency amount totals for 1h/24h/7d, a 7-day amount baseline and deviation, time since the previous observation, UTC hour/weekday, and whether a supplied destination/device/country was unseen in the seven-day window. It also records cold-start and late-event flags, event time and receipt cutoff.

The current transaction is excluded. History must belong to the same team/account, fall inside the event-time window and have been received no later than the current transaction. Later arrivals and future events cannot change a saved snapshot. Once saved, feature snapshots, transaction inputs and scoring results are protected against modification. Feature reads use indexed PostgreSQL history and bounded time windows; there is no Redis or in-memory feature store. Performance against the proposal's load target remains to be measured.

`ATM_SCORING_MODE` supports:

- `disabled`: current deployed default. Failed processing becomes an unscored review case after bounded retries.
- `simulation`: deterministic test adapter; prohibited in shared application configuration.
- `remote`: implemented HTTPS service adapter. Requires operator-configured URL, secret, model name and digest. The same non-simulation model must be independently approved in the team's model registry before any request is sent.

Remote requests send the transaction and computed snapshot, plus the configured model identity. They do not send case notes, customer transcripts, identity records or training labels. The endpoint cannot be selected by an API caller. Redirects and compressed replies are rejected; response bodies are limited to 64 KiB. An outer cancellation deadline bounds the entire exchange, including a reply that trickles in slowly; connection/read timeouts also apply. A persistent event loop/client reuses HTTPS connections inside the synchronous worker. Provider errors are reduced to safe operational error codes.

The machine-readable response contract is [scoring-response.schema.json](scoring-response.schema.json). Remote responses must include the matching transaction ID, `account-windows-v1`, configured model version/digest, supervised probability, anomaly score, fused risk score, a strict review boolean, and exactly five distinct ranked feature contributions with values, directions and magnitudes. Probabilities/scores use 0–1 inside the provider contract. The analyst queue displays 0–100. These checks validate the contract, not model accuracy, calibration or explanation quality.

A missing approved risk policy requires review. A provider's mandatory-review result cannot be suppressed by a threshold. Approved policy versions now support `medium_threshold`, `high_threshold`, `critical_threshold` (0–1, strictly increasing) and optional `daily_review_capacity` (1–100,000). The default bands are 0.35/0.65/0.85. Scoring stores severity, band thresholds and policy version with immutable transaction evidence; changing a policy does not reinterpret past results. Older results without stored severity retain `display-bands-v1`. An unavailable score is UNSCORED. These configurable bands are operational settings, not evidence of ML calibration.

An admin proposes settings through `POST /api/v1/admin/risk-policy`; a different supervisor activates them with recent MFA. Database revision 0008 enforces immutable policy details and DRAFT → ACTIVE → RETIRED transitions. Change an approved policy by creating a new version. Supervisors and auditors receive `review_capacity` in `GET /dashboard`: UTC day, target, opened/completed counts, remaining target and an overflow indicator. Analysts do not receive team-wide workload counts. The target never suppresses incoming cases; actual calibration against reviewed data remains ML work.

## Analyst workflow

1. `GET /api/v1/cases` (or `/alerts`) supports state, assignment, account, severity, minimum risk and created-time filters; order can be ID, oldest, newest or risk. Pass the returned `next_cursor` as `after` with the same filters/order. Equal-risk rows use a UUID tie-breaker. Assignment changes can remove access between pages; refresh the queue if its cursor becomes inaccessible.
2. `GET /cases/{id}/summary` returns coherent case/transaction state, current investigation, active call, final decision, evidence version, and pending/unfinished action counts. `GET /dashboard` aggregates only the caller's authorized cases.
3. `PUT /cases/{id}/assignment` assigns an active team analyst. `GET /team/members?role=analyst` is supervisor-only; `role=participant` supplies permitted synthetic-call participants to reviewers. Directory results contain application IDs and roles, not provider subjects or email addresses.
4. `POST /cases/{id}/investigations` starts, cancels or resumes investigation. `POST /cases/{id}/findings` records a human-authored category, summary, recommendation and up to 20 evidence transaction IDs. Referenced evidence must pass the case's account/team/time boundary. Findings are append-only case events and increment the evidence version. They cannot execute or approve actions.
5. `POST /cases/{id}/calls` creates an assigned EN/AR simulation; `GET /cases/{id}/calls` lists prior calls. Participants can answer/decline their call. Existing text turns and protected offline introduction audio remain available.
6. `POST /calls/{id}/verification` accepts AUTHORIZED, NOT_AUTHORIZED or UNSURE from that call's participant, appends a customer response and corresponding bank reply, ends the call and returns the case to analyst review. It does not classify the transaction, approve an action or resolve the case. Free text is never interpreted as an authorization instruction.
7. An analyst proposes an allowlisted action bound to the transaction/account/card. Sensitive approval requires an independent supervisor and fresh MFA. Approved unfinished actions block evidence changes and case closure. Cancellation is versioned and audited. Execution writes one simulated receipt; retries cannot duplicate it.
8. The analyst records CONFIRMED_FRAUD, LEGITIMATE or INCONCLUSIVE with a reason. The final decision is immutable and retains reviewer/time attribution. The case and investigation complete together.

Every command uses the returned current version. On `409 stale_version`, refresh the affected case/call/proposal and review the change before resubmitting. Do not blindly retry approvals or decisions.

## Operations and governed exports

- A scoring job that exhausts retries creates one manual-review case and retains its failed status. It cannot be retried through the admin API after that case exists, because rescoring would change evidence under an existing review. New transactions use the recovered provider normally.
- Queue metrics and failed-job recovery remain admin-scoped. Voice failure retains text interaction. Call timeouts and proposal expiry are handled by the worker.
- `GET /audit/export` is auditor-only and requires fresh authentication. It captures at most 10,000 committed records in one query, refuses silent truncation and returns a SHA-256 checksum of canonical JSON items. This is a download integrity check, not a digital signature or an externally retained immutable archive. Sequence cursors do not prove commit order across concurrent transactions; use overlapping exports and de-duplicate IDs for an archival consumer.
- Training snapshots include reviewed labels, label-availability timestamps, transaction context and captured feature snapshots. `GET /training-jobs` lists handoffs; supervisors can cancel a READY handoff with a reason. Snapshot data/hash cannot change; cancellation prevents later completion. A previously downloaded dataset cannot be recalled by cancellation.
- Model registration/review and drift-report storage are implemented. A model is used only when explicitly configured for the remote adapter and independently approved. Model training, statistical drift computation and model-quality evaluation remain the separate ML phase.

## Deployment boundaries

The application and worker run in hardened containers on this computer. Supabase is the only database and deployed identity provider. There is no local database fallback. Local HTTPS and private monitoring are implemented; no public application hosting is requested. Real account/MFA enrollment, managed-database patching, Supabase restore qualification, external audit retention, actual-model performance and independent security assessment remain qualification boundaries. No NCA/SAMA certification or banking production-readiness claim follows from this implementation.
