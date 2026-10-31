# E05 and local-agent integration

Integrated from Mohammad Aldemaiji's `7922499702e896d4043f9b18af29455c121722ae` version. The separate upstream checkout is unchanged. Our pre-integration restore tag is `checkpoint-before-integration-2026-10-26` (`77247df`).

## What runs where

The backend now has an optional native Python profile for E05, Qwen3, Piper and Whisper. The existing lightweight Alpine containers retain the default disabled ML/AI configuration; they do not contain these native ML dependencies or weights. Do not enable the profile in those containers without supplying a compatible image, artifact mounts and a local Ollama service. No database migration is required. Existing Supabase authentication, team scoping, model approval, risk policy, action approval and simulated execution remain in force.

Install on the local host using Python 3.12+:

```sh
uv sync --project backend --extra local-ai
# macOS only, if missing:
brew install libomp
```

The repository now includes a native HTTPS launcher that reuses the existing restricted `.runtime/supabase-app.env` credentials and local certificate, without rewriting them:

```sh
npm --prefix frontend run build
backend/.venv/bin/python backend/scripts/local_ai.py api
# Merged dashboard: https://localhost:8444
```

The launcher pins the bundled E05 model/data, enables the local Qwen3 adapter (4B by default on this Mac; override with `ATM_AGENT_MODEL`), and discovers downloaded voices in `.runtime/local-ai/voices` and Whisper in `.runtime/local-ai/whisper-small`. The UI and API share an HTTPS origin and retain their authentication, CSRF and content-security boundaries. Supabase signup/email redirects need to allow this additional localhost origin if those flows are used; existing account sign-in remains subject to the usual role and MFA requirements.

To switch transaction processing to the native profile, stop the old project's worker first, then run `backend/.venv/bin/python backend/scripts/local_ai.py worker`. Do not run mixed disabled/E05 workers against the same queue. The launcher never grants model approval or activates a policy. The existing containers are not automatically replaced; their monitoring continues to describe the container deployment, not the native profile. Use the original Docker workflow to return to that deployment.

For another deployment, use the settings below with the existing operating guide's restricted Supabase credentials and verified CA file. Keep credentials in ignored runtime configuration, bind native services to loopback and retain HTTPS for the browser.

## E05 scoring

```sh
backend/.venv/bin/python backend/scripts/e05_sample.py
backend/.venv/bin/python backend/scripts/e05_sample.py --transaction TX000733650
```

The first command prints the six `ATM_` settings required by the adapter, including SHA-256 identities for the model bundle and the bundled sample. The second prints an API-compatible synthetic transaction. Submit it through the normal authenticated transaction intake. An administrator must register `fraud_e05` with the printed model digest and genuine evaluation metrics; an independent supervisor approves it through the existing model workflow. Do not invent precision or recall just to register the model. The worker checks approval separately for each team. Without approval or a matching sample, it retries and ultimately opens an unscored manual-review case.

E05 is **supervised fraud detection only**. Its log-scaled risk ranking is not a calibrated probability; the result explicitly records `score_kind=uncalibrated_ranking`, `feature_source=pinned_synthetic_e05`, and the dataset digest. `supervised_probability` is the model output and `anomaly_score` remains null. Our policy thresholds/severity rules still apply; his probability-based band labels are not substituted for approved policy.

The adapter preserves his eight engineered features, one-hot encoding and grouped SHAP explanations. Metadata formerly loaded from pickle is checked in as JSON; runtime inference never unpickles those files. The identity covers the model JSON, column ordering and threshold settings.

This is an **exact synthetic replay adapter**, not a general live feature bridge. Only approved authorizations from 11 August 2026 onward in the pinned dataset are eligible. The external ID, account, amount, currency, mapped channel and timestamp must match. Context overrides are rejected. Other inputs are not zero-filled or silently mapped to unrelated accounts. The live account-window features are still recorded but cannot substitute for E05's full research feature set. A production/live feature bridge needs its own input contract and evaluation.

## Local investigation

Start Ollama locally with cloud features disabled and download `qwen3:8b`. Configure:

```text
ATM_AGENT_ENABLED=true
ATM_AGENT_URL=http://127.0.0.1:11434
ATM_AGENT_MODEL=qwen3:8b
ATM_AGENT_TIMEOUT_SECONDS=90
```

In an active investigation, **Analyse with local AI** saves a finding under **Notes & findings**. The model gets the case's score, feature snapshot and up to 19 same-account historical transactions within 90 days, scoped by team, event time and receipt cutoff. History truncation is disclosed. It receives no database access, action tools, labels, credentials or arbitrary lookup capability.

His recommendation vocabulary maps to our workflow: RELEASE → NO_ACTION advice; VERIFY_WITH_CUSTOMER → CONTACT_CUSTOMER advice; BLOCK_AND_FREEZE → PROPOSE_ACTION advice. None creates a proposal, approves an action, changes an account or resolves the case. A human reviews the finding and uses the existing workflow.

Inference runs outside database transactions. Saving rechecks the actor's current role/active status, assignment, investigation and evidence version. Concurrent evidence changes invalidate the result. Invalid JSON, unknown evidence references, redirects, cloud models and timeouts fail closed. Requests have a total deadline, bounded responses and one local inference slot per API process. Failure leaves the case available for manual review. The browser and local gateway permit the longer bounded AI request without extending the ordinary backend response timeout.

Qwen3 8B can exceed a small laptop's practical memory/latency budget. A real request and a separate 180-second warm-up timed out on the current Mac during verification; mocked provider tests are not proof of successful local inference. Keep this limitation visible rather than treating a missing response as an AI decision.

## Local voice

Download Piper's `en_US-lessac-medium` and `ar_JO-kareem-medium` voices to a local directory, then set `ATM_PIPER_VOICE_DIR` to it. The existing voice job uses Piper for the fixed bank introduction; without this setting it retains eSpeak. Text remains available when speech fails.

Set `ATM_WHISPER_MODEL_DIR` to a fully downloaded faster-whisper model directory. During an active participant-owned call, **Transcribe a short recording** accepts at most 64 KB of encoded audio and 15 seconds of decoded audio. Whisper uses local files only. The returned text is an editable draft: the participant must explicitly send it, and the ordinary call-state/version checks still apply. Audio is not stored by transcription. Uploaded recordings never determine a bank action.

This integration retains our structured verification and approval flow. It does not import the upstream phone agent's automatic release/block decisions or add autonomous open-ended phone conversations.

## Verification

Run focused checks using the existing isolated `atm_test` credentials:

```sh
backend/.venv/bin/python backend/scripts/check.py tests/test_local_ai.py tests/test_agent_integration.py tests/test_scoring_contract.py tests/test_action_integrity.py
npm --prefix frontend test -- --maxWorkers=1
npm --prefix frontend run build
```

The tests cover real E05 inference, artifact/input mismatches, local-only model access, malformed responses, total timeouts, cross-team/account evidence boundaries, stale-case rejection, model approval and transcript ownership/draft behavior. AI and transcription fixtures in workflow tests are explicitly mocked; the supervised E05 inference tests load the actual model.

## Verified on 26–27 October 2026

- 46 model/provider unit tests passed with the actual E05 model; 12 targeted database workflow/approval tests passed across the initial run and corrected-test rerun. Three new tests initially expected HTTP 201 from an existing HTTP 200 endpoint; correcting that assertion yielded three passes. No product behavior was changed to accommodate it.
- 26 frontend tests passed using the thread pool, after fork-worker startup timeouts in this environment. The frontend build and formatting checks passed. The native profile test confirms SPA routing, missing-asset 404s, protected API routes, host checks and security headers.
- Ruff, strict mypy and Bandit checks passed. Piper produced valid English and Arabic WAV audio. Whisper workflow tests use a mock transcription provider; real Whisper inference remains unverified until its weights are installed.
- Real Qwen3 8B inference remains unverified because loading timed out. E05 matching/scoring and advisory failure behavior are verified independently of this hardware limitation.

The Transactions screen offers **Simulate incoming traffic** to ingestion accounts. Each click replays ten of 50 eligible held-out samples through the existing batch endpoint. Stable sample keys prevent duplicate transactions across retries and page reloads. This does not activate the scoring worker or approve the model.

The native local profile also enables a **Simulate incoming traffic** panel above the workspace for analysts, supervisors and administrators. Its dedicated endpoint only accepts an offset into the first 50 eligible, digest-verified dataset samples; it cannot accept arbitrary transaction fields. Other deployments leave this capability disabled by default (`ATM_SAMPLE_TRAFFIC_ENABLED=false`). Ordinary ingestion permissions remain unchanged.
