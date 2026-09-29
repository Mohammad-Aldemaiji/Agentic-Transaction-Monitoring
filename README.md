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
│   ├── features/    # feature pipeline and feature store
│   ├── models/      # classifier, anomaly detector, calibration, fusion
│   ├── serving/     # FastAPI scoring service
│   ├── explain/     # per-alert reason codes (SHAP)
│   └── agent/       # alert analysis and action execution
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

## Getting started

> Filled in as the components are built. The goal (NFR6) is a single command on a clean machine.

```bash
# Rebuild the dataset
cd generator
python3 main.py --customers 2400 --rate-scale 0.28 --seed 20260921 --out ../data/

# Start the full system
docker compose up
```

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
