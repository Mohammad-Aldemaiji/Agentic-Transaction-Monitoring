# Agentic Transaction Monitoring

ML-based detection of suspicious financial transactions with an AI agent that investigates alerts and acts on them after analyst approval.

> CS/IS/SE 499 Senior Project, Prince Sultan University, Semester 261
> Supervisor: Dr. Tariq

**Status:** In development. Performance figures below are project targets, not achieved results, until marked otherwise.

---

## Overview

Banks still rely heavily on static rules (fixed thresholds, blacklists, simple velocity checks). These are easy to evade by splitting a transfer into smaller ones, produce many false alerts, and only catch patterns someone has already written down.

This project replaces fixed rules with a system that learns suspicious behaviour from data. It combines:

1. **A supervised classifier** trained on known fraud and money-laundering cases.
2. **An unsupervised anomaly detector** that flags behaviour no label ever described.
3. **A fused, calibrated risk score** with a severity band, tuned to a realistic daily alert-review capacity.
4. **Per-alert explanations**: the top five contributing features are attached to every alert.
5. **An AI agent** that analyses each alert, recommends an action, and executes it only after a bank employee approves it.
6. **An analyst dashboard** where decisions are recorded and fed back into model retraining.

Everything runs on public and synthetic data. No real customer data is used at any stage.

## How it works

```
Client transaction
      |
      v
Backend + Database --> ML Model --> risk score + top reasons
      |
      +-- Normal ------> approved
      |
      +-- Suspicious --> hold transaction, create alert
                               |
                               v
                         AI Agent: explanation + recommended action
                               |
                               v
                         Dashboard --> Bank Employee approves
                               |
                               v
                         Agent executes approved action
                         (verify with client / release / block + freeze)
                               |
                               v
                         Employee closes case --> decision becomes a training label
```

The human approval step is deliberate. The agent recommends, a person decides, and only then does the agent act.

### System layers

| Layer | What it does |
|---|---|
| Ingestion | Receives transactions on a message stream and validates them against a fixed schema. |
| Feature service | Maintains rolling per-account aggregates (1 hour, 24 hours, 7 days) in an in-memory store. |
| Scoring | Runs the classifier and anomaly detector in parallel on the same feature vector. |
| Decision | Fuses both scores into a 0-100 risk score and assigns a severity band. |
| Agent and dashboard | Presents alerts with reasons and recommendations; stores analyst decisions for retraining. |

## Targets

| Goal | Target |
|---|---|
| Supervised detection | Recall of 0.85 or higher at a false-positive rate of 2% or lower, on a held-out test period |
| Anomaly layer | Detect laundering typologies withheld from the training labels |
| Scoring latency | 95th percentile of 200 ms or less |
| Throughput | At least 100 transactions per second on a four-core machine |
| Explanations | Top five contributing features on every alert |
| Reproducibility | Every result reproducible from a fixed seed, versioned config and recorded dataset version |

## Dataset

The project uses a synthetic Saudi retail-bank transaction dataset built by our own seeded generator, so it rebuilds identically.

- 2,400 accounts (individuals, SMEs, corporates) over 9 months, 1 January to 30 September 2026
- 746,698 transaction attempts, about 10% declined
- Saudi-specific rails and thresholds: mada, sarie (SAR 20,000 registered-beneficiary cap, SAR 2,500 alias cap), SADAD, SWIFT, exchange-house remittance, WPS payroll
- Saudi calendar effects: Friday-Saturday weekend, Ramadan, Eids, Hajj season, salary cycle
- **Fraud labels:** card testing, card-not-present bursts, account takeover, authorised-push-payment scams, ATM skimming, first-party bust-out
- **AML labels:** cash structuring, sarie smurfing, mule pass-through rings, salary-mule rings, cash-intensive SME layering, remittance smurfing, gold conversion
- **Withheld typologies** (trade-based over-invoicing, shell-company rapid turnover) are kept out of the labels and used only to test the unsupervised layer

See `DATA_DICTIONARY.md` for every column.

### Things to know before using it

- **Prevalence is inflated on purpose** so each typology has enough cases to measure. Use `weight_fraud` and `weight_aml` to map metrics back to realistic rates, and report weighted metrics.
- **Labels are noisy on purpose**, like real analyst dispositions and chargebacks.
- **Labels arrive late** (`label_available_at`). Building features from label information before that date is leakage.
- **Split on time, and group by `case_id`.** A laundering ring spans several accounts and weeks, so a random row split gives meaningless scores.
- **Fairness:** `residency_status`, `remittance_corridor` and `counterparty_country` are proxies for national origin. We either exclude them or report flag rates by residency alongside accuracy.
- Synthetic data understates real-world noise. Results are validated against independent public datasets as well (IBM Transactions for Anti-Money Laundering, ULB credit-card fraud).

## Repository structure

```
agentic-transaction-monitoring/
├── data/            # DVC-tracked, not in Git
├── generator/       # seeded dataset generator
├── notebooks/       # exploration and experiments
├── src/
│   ├── features/    # history store; live feature pipeline to come
│   ├── models/      # fraud model E05 (anomaly detector, calibration, fusion to come)
│   ├── serving/     # FastAPI service: scoring, cases, workflow, database
│   ├── explain/     # (reasons currently live in src/models/fraud_model.py)
│   └── agent/       # local LLM agent: investigation, verification call, voice
├── dashboard/       # React + TypeScript analyst dashboard
├── models/          # saved model artifacts (tracked with DVC / MLflow)
├── tests/
├── docker-compose.yml
├── ATTRIBUTION.md   # external code, data and AI-assisted work
└── README.md
```

## Tech stack

| Area | Tools |
|---|---|
| Language | Python, pandas, NumPy |
| Machine learning | scikit-learn, XGBoost, LightGBM, PyTorch, imbalanced-learn |
| Explainability | SHAP |
| Streaming and caching | Kafka or Redis Streams; Redis feature store |
| Service | FastAPI, Uvicorn |
| Storage | PostgreSQL (alerts, decisions); Parquet (datasets) |
| Experiment tracking | MLflow (models), DVC (datasets) |
| Frontend | React, TypeScript |
| Testing | pytest, Locust |
| Infrastructure | Docker, Docker Compose, GitHub Actions |
| Agent (all local) | Ollama with Qwen3 8B for the agent, Piper for its voice, faster-whisper for speech recognition |

## Getting started

> Filled in as the components are built. The goal (NFR6) is a single command on a clean machine.

```bash
# Rebuild the dataset
cd generator
python3 main.py --customers 2400 --rate-scale 0.28 --seed 20260921 --out ../data/

# Start the full system
docker compose up
```

## Running the system

Everything runs on your own machine: the model, the agent (Ollama), its voice (Piper) and
speech recognition (faster-whisper). No transaction data, prompt, voice or recording is sent
to an external service, and the agent refuses to start if it is pointed at a remote or cloud model.

**Once:**

```bash
# 1. Ollama: install from https://ollama.com/download, then
ollama pull qwen3:8b

# 2. Python packages (Python 3.12+; xgboost 3.4.1, which the model was trained with, needs it)
pip install -r requirements.txt

# 3. Voices for the verification call (optional; without them the phone shows text only)
python -m piper.download_voices en_US-lessac-medium ar_JO-kareem-medium --download-dir src/agent/voices

# 4. Dashboard (Node 20+)
cd dashboard && npm install && npm run build && cd ..
```

Put the full `transactions.csv` in `data/` for the real history; without it the system runs on
`data/sample/transactions_mini.csv`.

**Every time:**

```bash
python -m src.serving
```

Open http://localhost:8000 for the analyst dashboard and http://localhost:8000/phone in a second
window for the customer's phone. For dashboard development with hot reload, run `npm run dev`
in `dashboard/` and open http://localhost:5173.

### The flow (the sequence diagram)

1. **Live feed** replays test-period transactions (ones the model never trained on) into
   `POST /api/transactions`, or sends single examples of each fraud type.
2. The **fraud model (E05)** scores each one in about 50 ms and attaches its top five reasons
   (SHAP values from XGBoost). Below the alert line it is approved; above it, it is held and a case opens.
3. The **agent** looks up the transaction, the account's 90-day history and the device, then
   recommends one action: release, call the customer, or block and freeze. It cannot act.
4. The **analyst** approves that action or picks another. Every decision records their name.
5. If the action is a call, the agent rings the **customer's phone**, asks about the payment and
   reports back. Code, not the model, decides the outcome: a clear "no" blocks, a clear "yes" with no
   sign of a scam releases, anything else goes back to the analyst.
6. The analyst **closes the case** as fraud or legitimate. That decision is stored as a training
   label (`/api/labels.csv`) for the next model version.

### Code map

| Path | What it is |
|---|---|
| `src/models/fraud_model.py` | E05 feature preparation (identical to the notebook), scoring, severity bands, reasons |
| `src/features/history.py` | The transaction history the model and agent read from (stand-in for core banking) |
| `src/agent/llm.py` | Local Ollama client, the "local only" checks, the tool-calling loop |
| `src/agent/investigator.py` | The agent: analyse an alert, verify by phone, and the outcome rules |
| `src/agent/voice.py` | Piper text-to-speech and faster-whisper speech-to-text |
| `src/agent/cli.py` | Run the agent on one alert in the terminal (`python -m src.agent.cli pick`) |
| `src/serving/service.py` | The flow above: ingest, case states, workers, actions, labels, replay |
| `src/serving/main.py` | The HTTP API (interactive docs at http://localhost:8000/docs) |
| `src/serving/db.py` | Tables: transactions, cases, audit trail, accounts, messages, labels |
| `dashboard/` | React + TypeScript dashboard and the customer phone page |

Settings are environment variables: `AGENT_MODEL` (default `qwen3:8b`), `AGENT_THINK=1` to let
the model reason before each step (better, slower), `WHISPER_MODEL` (`small`; `medium` is better
for Arabic), `WHISPER_DEVICE=cuda`, and `DATABASE_URL` for PostgreSQL instead of the default
SQLite file in `data/`.

## Roadmap

| Week | Milestone |
|---|---|
| 1 | M1: requirements and design signed off, scope frozen |
| 2 | M2: data pipeline complete |
| 3 | M3: supervised detection targets met |
| 4 | M4: both detection layers working together |
| 5 | M5: live stream scoring |
| 6 | Dashboard and explanations |
| 7 | M6: integration, retraining, performance targets met |
| 8 | M7: final report, demo and defence |

## Out of scope

Live core-banking integration, real customer data, automated regulator filings, sanctions and watchlist screening, cryptocurrency tracing, and a native mobile app.

## Team

| Name | Role |
|---|---|
| Mohammad Aldemaiji | Team lead, machine learning engineer |
| Yousef Alyousef | Data engineer |
| Abdulrahman Shikmakanik | Anomaly detection and evaluation |
| Salman Almadi | Backend and deployment engineer |
| Mohammed Nasser | Frontend engineer and explainability |

## Data and privacy

All data is synthetic or public. No real customer, account, card or institution appears in any file. All external code, datasets, third-party components and AI-assisted work are recorded in `ATTRIBUTION.md`.
