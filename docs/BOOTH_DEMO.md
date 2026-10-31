# Booth demonstration

Open **https://localhost:8555** and sign in with your approved account and authenticator code. Then select **Bank feed**, **Analyst**, **Supervisor**, or **Administrator** in the role bar. Authentication is real; one presenter still controls four synthetic identities, so role switching does not demonstrate independent human approval.

The protected workspace remains at https://localhost:8444. Booth requests use a separate cookie, database, schema and Docker volume. Never expose the booth server beyond localhost.

## What is running

- 100 synthetic customer accounts with 8,217 eligible payments drawn from the bundled dataset, including ordinary activity and fraud-labelled histories.
- A bank-feed replay with stable transaction keys, chronological order within each customer, and 100-payment batches. The first run submits 100 payments; restarting preserves saved work and does not submit another initial batch.
- The existing trained E05 model, actual scoring jobs, review cases, evidence and histories. The booth permits exact historical dataset replays from earlier periods, which may overlap training. This tests workflow behavior, not out-of-sample accuracy.
- A background worker for scoring, Piper introductions and simulated actions. It is part of the booth process and stops when that process stops.
- Qwen3 4B advisory analysis when the local Ollama service is running and the model is available. It has no action-execution tools.
- Scripted customer confirmation, denial or unavailability inside a verification call. No actual customer is contacted. Responses remain explicitly marked as simulated in the audit.
- The usual case versions, approval rules, duplicate protection and durable state. Financial actions are simulated.

The launcher initializes the demo's fixed staff identities, then registers and approves the E05 model and a demo risk policy through the application's normal routes with separate scripted admin/supervisor identities. Stored model metrics are the measured enriched-sample results, not production qualification.

## Walk through a scenario

1. Select **Bank feed**, inspect the customer list, and click **Simulate incoming traffic · 100 payments**. Each click advances through the cohort; exhausted payments are not duplicated.
2. Select **Analyst**, refresh the queue, and open an alert. Start an investigation and inspect the transaction, score and account history. Optionally run local AI analysis.
3. Start a verification call, selecting the booth participant as the contact simulator. Accept the ringing phone, hear the locally generated instructions, then press **1** to confirm or **2** to deny. **Unavailable** records an unanswered call. Confirmation/denial records a reply and ends the call.
4. Propose an appropriate simulated action. For a general fraud-denial scenario, **Freeze account** works across channels. Card blocking requires a card identifier and transfer holding requires a transfer; the demo retains these checks.
5. Select **Supervisor**, find the same case, review the proposal and approve or reject it. The analyst cannot approve a high-impact action. Wait for the worker to execute approved actions.
6. Return to **Analyst** and record the final decision. The verification run already left one resolved example to inspect.
7. **Administrator** provides model, policy and processing screens. Returning to the same role renews its scripted session when an approval requires recent authentication.

## Start again

From the repository root, with Docker Desktop and the existing local Ollama service running:

```sh
npm --prefix frontend run build
backend/.venv/bin/python backend/scripts/booth_demo.py serve
```

The launcher uses the existing `postgres:17.7` image (`--pull=never`). The dedicated `atm-booth-db` container publishes PostgreSQL only on 127.0.0.1:55439. It uses the `atm-booth-data` volume. Runtime credentials remain in the ignored `.runtime/booth` directory. The app uses the existing localhost TLS certificate. Stop the server with Ctrl-C; do not remove the volume to stop it. No automatic reset or deletion is performed.

## Verified

`backend/scripts/verify_booth.py --ai` completed a real HTTPS scenario on 28 October 2026: real E05 score → real local Qwen analysis (HTTP 200) → scripted customer denial → analyst approval rejected → separate supervisor approval → duplicate approval rejected → simulated execution → resolved case. No failed jobs appeared in the latest processing page. The verification deliberately creates synthetic records and does not reset existing work.

```sh
backend/.venv/bin/python backend/scripts/verify_booth.py --ai
```

Detailed evidence is saved in `.runtime/booth/verification.json`. The suite also checks that booth mutation routes are absent from normal profiles, non-local database/origin settings are rejected, and booth cookies cannot replace the shared workspace session.

## Score display and payment history

The display now uses the direct E05 XGBoost output multiplied by 100, rather than the logarithmic ranking. This is not calibrated certainty. The existing trained alert threshold is unchanged; priority uses the configured severity bands. Old isolated records retain their previous ranking in metadata and receive an audit entry when upgraded. Model weights are unchanged.

Transactions shows all payments, including cleared payments, with customer-history and priority filters. Bank JSON excludes training labels. Local AI findings appear immediately in the case with the full structured response. Risk settings and scoring-model controls remain in Administrator.

The verified prepared state contains 1,225 payments: 1,131 cleared, 94 flagged, zero pending or failed. Scores span 1,142 low, 1 medium, 17 high and 65 critical; each selected customer has 12–14 received payments. These are synthetic historical replays, including training-period examples, not an independent accuracy evaluation.

## Failure handling and evidence freshness

Local AI receives the latest three verification calls with bounded transcripts (up to six recent turns per call, 1,000 text characters overall). Truncation is explicit, and customer statements remain untrusted evidence. A completed call does not imply confirmation. AI cannot authorize a bank action.

The case displays the newest saved AI assessment even beyond the first 50 findings, and warns when later case updates make it historical. Phone audio downloads can be retried; failed voice generation leaves the transcript and response controls available. Server-side call timeout refreshes the parent case. Payment-monitor totals count recorded policy priorities rather than applying fixed display thresholds.

The HTTPS verification script runs local AI after customer denial and saves its result to `.runtime/booth/analysis-after-verification.json`. Regression tests cover transcript limits and case isolation, latest-finding retrieval, changed-policy totals, audio retry, browser audio gestures, voice failure and timeout refresh.


## Presenter sign-in

The HTTPS presentation now requires an existing approved supervisor or administrator account and its authenticator code. After sign-in, the presenter can switch among the four synthetic roles without another code per switch. The role cookies retain the verified presenter claims and expire 24 hours after sign-in, including idle time. Switching roles preserves this deadline. The initial provider token must be valid and MFA-verified; later opaque-session requests recheck identity and provider-session revocation without requiring that short-lived token to remain unexpired. Every protected request rechecks account and provider-session validity through read-only shared identity queries. Transaction data remains isolated. Signing out, expiry, or revocation requires sign-in again. Older unauthenticated role cookies are rejected.

The HTTPS verification script now requires an authenticated presentation cookie and CSRF value supplied privately via `ATM_PRESENTATION_SESSION` and `ATM_PRESENTATION_CSRF`. There is no network-accessible authentication bypass. The bootstrap exception runs only inside the preparation process before the listener starts.
