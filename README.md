# Rasid — Agentic Transaction Monitoring

An integrated senior-project workspace for synthetic bank transactions, supervised fraud scoring, analyst investigations, local AI advice, customer-verification simulations and independently approved simulated bank actions.

CS/IS/SE 499, Prince Sultan University, Semester 261. Supervisor: Dr. Tariq.
Originated by Mohammad Aldemaiji; this version integrates his E05 model and local-agent approach with the team's monitored review workflow. See [attribution](ATTRIBUTION.md).

**Review version: 31 October 2026.** This is a local research/presentation application, not a production banking deployment. No money is withdrawn, no real account is frozen, and no actual customer is called. Runtime credentials, local databases and downloaded AI/voice assets are not included.

## What this version adds

- React/TypeScript dashboard with Bank feed, Analyst, Supervisor and Administrator views.
- FastAPI backend, durable PostgreSQL jobs, audit records, version checks, scoped evidence and duplicate protection.
- Actual inference using the bundled supervised E05 XGBoost model, with explanations and recorded model/policy identities.
- Synthetic customer histories, transaction JSON, configurable priorities and a review queue.
- Local Qwen advisory analysis with structured findings, bounded history and verification transcripts; stale findings are identified.
- Phone-style simulated verification: ringing, accept, confirm/deny keypad, unavailable/timeout outcomes and optional local speech.
- Human proposals, independent supervisor approvals, simulated execution receipts and final case decisions.
- Model/policy governance, reviewed-data exports and processing/recovery screens.
- Real presenter sign-in with an authenticator code, followed by synthetic role switching. The presentation session lasts up to 24 hours, including idle time; signing out or provider/account revocation ends access earlier. Switching roles does not extend the deadline.

## Roles

| Role | Purpose |
|---|---|
| Bank feed | Replay synthetic transactions and inspect customer histories and processing results. |
| Analyst | Investigate flagged cases, review evidence and AI advice, perform simulated verification, propose actions and record final decisions. |
| Supervisor | Oversee cases and approve/reject eligible actions independently of the proposing analyst. |
| Administrator | Manage policy/model proposals, reviewed datasets and operational processing. Administrator access does not imply supervisor approval authority. |

In the presentation profile one authenticated presenter controls separate synthetic identities. This makes navigation convenient but does **not** prove independent approval by different people. Shared-workspace users retain their actual assigned roles.

## Transaction workflow

```text
Bank feed → durable intake → approved supervised model → recorded risk policy
  ├─ no review required: retained in the transaction monitor
  └─ flagged or scoring unavailable after retries: manual-review case
       → investigation + optional local AI + verification evidence
       → proposed action → independent approval when required
       → simulated execution → analyst's final decision
```

Analysts review flagged cases, not every received payment. A customer confirmation or an AI recommendation cannot authorize a bank action. This application monitors and simulates responses; a real bank's authorization, settlement and account balances are outside its integration.

## Scores, priorities and evidence

The displayed score is the supervised model output multiplied by 100. It is **not a calibrated probability of fraud**. Review eligibility also considers the model's review flag and the approved policy threshold; missing policy/scoring information fails into review rather than silently clearing transactions. Therefore a low-priority payment can still require review.

Severity cutoffs classify the score (defaults: medium 0.35, high 0.65, critical 0.85). The review threshold is separate. Policy changes require approval; a daily review target is a capacity setting, not proof of model accuracy. Existing records retain their recorded priorities.

E05 integration is an exact, digest-checked synthetic replay adapter. It preserves the research feature contract and rejects mismatched inputs; it is not yet a general live-bank feature bridge. The presentation allows historical examples that may overlap training. Neither a convincing demonstration nor varied scores establish out-of-sample accuracy. Anomaly detection, calibrated fusion and automatic retraining remain future work.

The original research describes 746,698 transactions; this checkout supplies only the 35,828-row sample. The presentation selects 100 synthetic customers and 8,217 eligible payments, replayed in batches of 100 with per-customer chronological ordering. Training labels are excluded from displayed bank JSON and AI evidence. See [scoring study](docs/E05_SCORING_STUDY.md) for sample evaluation and limitations.

## Run locally or from VS Code

Open the repository folder in VS Code and use its integrated terminal. Prerequisites: Python 3.12+, uv, a compatible Node.js/npm installation, Docker Desktop, and an operator-configured Supabase project for real sign-in. Optional local AI requires Ollama and downloaded models. A fresh clone needs configuration before it can run.

```sh
uv sync --project backend --extra local-ai
npm --prefix frontend ci
# macOS only, if the XGBoost OpenMP runtime is missing:
brew install libomp
```

Follow the [backend operating guide](docs/BACKEND_OPERATIONS.md) to apply the reviewed migrations and configure restricted database access and approved users. Keep actual values in ignored `.runtime/supabase-app.env`; use [shared.env.example](backend/deploy/shared.env.example) as a template, replacing its project-specific identifiers. Supply the verified database CA at `.runtime/supabase-ca.pem`. Never use a service-role secret in the browser.

Configure `frontend/.env.local` from [frontend/.env.example](frontend/.env.example) using your Supabase URL and publishable key. The frontend configuration is embedded at build time. Prepare your localhost certificate/key and private runtime files with the [local HTTPS guide](docs/LOCAL_DEMO.md); certificate trust requires an explicit local setup step. Keep the servers bound to localhost.

For the isolated presentation workspace, after configuration and certificate setup:

```sh
docker pull postgres:17.7
npm --prefix frontend run build
backend/.venv/bin/python backend/scripts/booth_demo.py serve
```

Open **https://localhost:8555/** and sign in with an approved supervisor/administrator account and its authenticator code. The launcher starts its worker and initializes the synthetic identities, model and policy. PostgreSQL uses a dedicated `atm-booth-db` container and persistent `atm-booth-data` volume on loopback port 55439. Restarting retains records. Ctrl-C stops the application; do not delete the volume merely to stop it. See the [presentation walkthrough](docs/BOOTH_DEMO.md).

For optional local AI, in another terminal:

```sh
ollama pull qwen3:4b
ollama serve
```

Do not start a second Ollama server if the desktop service is already running. The application uses the local endpoint on port 11434. AI remains advisory and manual investigation remains available if inference fails. Piper voice and Whisper weights require separate downloads/configuration; see [local AI and voice setup](docs/LOCAL_AI_INTEGRATION.md). Model assets are not bundled with this PR except the supervised E05 artifacts.

### Other launch profiles

| Profile | Launch | Notes |
|---|---|---|
| Native shared workspace, 8444 | `backend/.venv/bin/python backend/scripts/local_ai.py api` and, separately, `backend/.venv/bin/python backend/scripts/local_ai.py worker` | Uses configured shared database and actual user roles. Model and policy approval remain required. Stop other workers targeting that queue first. |
| Original Docker workspace, 8443 | `backend/.venv/bin/python backend/scripts/demo.py start` | See local HTTPS guide. Lightweight images do not include the native AI dependencies; scoring defaults to disabled until configured. |

## Validation and known limitations

Latest backend verification: **188 passed, 1 optional load test skipped**, across the main and configuration runs on 30 October. The latest frontend retry completed **29 tests**, with two worker-startup timeout errors; this is not a complete green frontend run. Earlier verification is recorded in the linked guides and is dated evidence, not a guarantee for every current scenario.

A live replay submitted 100 additional payments: 87 cleared and 13 flagged, with no pending/failed scoring jobs at observation. Investigation startup, customer-history filtering, transaction JSON and simulated call acceptance were exercised. The latest audit did not complete every role's entire workflow.

Known issues for review:

- Presentation navigation exposes risk/model screens only to Administrator, while approval controls require Supervisor. This UI handoff needs correction before claiming complete governance coverage.
- `verify_booth.py` accepts authenticated session inputs but retains an incompatible initial unauthenticated `/me` assertion. Its older successful run predates presenter authentication; the current script needs updating.
- Local Qwen can return contradictory reasoning and can time out on limited hardware. Human review is required. Real Whisper inference remains unverified.
- Speech preparation may be slow; transcripts and response controls remain available when audio fails.
- Synthetic history replay is not independent fraud-detection validation. Real bank execution, live feature ingestion, anomaly/fusion models and production qualification are not implemented.

Run checks against an explicitly configured isolated test database, never production:

```sh
backend/.venv/bin/python backend/scripts/check.py
npm --prefix frontend test -- --maxWorkers=1
npm --prefix frontend run build
```

The backend check script requires the isolated test configuration described in the operating guide. Targets such as recall ≥0.85 at ≤2% false positives, p95 scoring ≤200 ms and 100 transactions/second remain research targets, not release guarantees.

## Repository guide

- `backend/`: API, workers, authentication, integration adapters and tests.
- `frontend/`: dashboard, phone simulation and frontend tests.
- `supabase/migrations/`: reviewed schema and security changes.
- `models/fraud_e05/`: supervised model and feature metadata; runtime inference uses JSON metadata rather than unpickling.
- `data/sample/`: bundled synthetic sample.
- `notebooks/`, `generator/`, `src/`: research and original project components.
- `docs/`: [API contract](docs/openapi.json), [application behavior](docs/BACKEND_APPLICATION.md), setup and dated verification evidence.

Older documents describe earlier milestones and deployment profiles; use this README for the current version's status. See [PR release notes](docs/RELEASE_NOTES_2026-10-31.md) for the review summary.

## Team

| Name | Role |
|---|---|
| Mohammad Aldemaiji | Team lead, machine learning engineer |
| Yousef Alyousef | Data engineer |
| Abdulrahman Shikmakanik | Anomaly detection and evaluation |
| Salman Almadi | Backend and deployment engineer |
| Mohammed Nasser | Frontend engineer and explainability |

## Data and attribution

Use synthetic/public data only. Do not commit `.runtime`, actual environment files, certificates/private keys, authentication tokens, database dumps or downloaded private records. Third-party components and AI-assisted development are documented in [ATTRIBUTION.md](ATTRIBUTION.md). Preserve the original authors' credit when redistributing this work.
