"""Opt-in bounded load exercise against the isolated Supabase schema.

Uses real HTTPS sockets and real database commits, with test-only signed identities.
Never run simultaneously with another integration suite; the fixture resets atm_test.
"""

import asyncio
import json
import math
import os
import socket
import ssl
import threading
import time
from datetime import UTC, datetime
from pathlib import Path
from uuid import uuid4

import httpx
import pytest
import uvicorn
from sqlalchemy import func, select

from atm_backend.adapters import SimulationScorer
from atm_backend.models import Job, Transaction
from atm_backend.worker import run_one

ROOT = Path(__file__).resolve().parents[2]
pytestmark = pytest.mark.skipif(os.environ.get("ATM_RUN_LOAD") != "1", reason="Opt-in load test")


def summary(samples, elapsed, errors, concurrency):
    values = sorted(samples)
    return {
        "requests": len(values),
        "concurrency": concurrency,
        "errors": errors,
        "elapsed_seconds": round(elapsed, 3),
        "requests_per_second": round(len(values) / elapsed, 3),
        "p50_ms": round(values[math.ceil(len(values) * 0.50) - 1] * 1000, 2),
        "p95_ms": round(values[math.ceil(len(values) * 0.95) - 1] * 1000, 2),
        "max_ms": round(max(values) * 1000, 2),
    }


def test_local_https_load_and_durable_processing(env, transaction_body):
    listener = socket.socket()
    listener.bind(("127.0.0.1", 0))
    port = listener.getsockname()[1]
    config = uvicorn.Config(
        env["app"],
        host="127.0.0.1",
        port=port,
        access_log=False,
        log_level="error",
        ssl_certfile=str(ROOT / ".runtime/localhost.pem"),
        ssl_keyfile=str(ROOT / ".runtime/localhost-key.pem"),
    )
    server = uvicorn.Server(config)
    thread = threading.Thread(target=server.run, kwargs={"sockets": [listener]}, daemon=True)
    thread.start()
    deadline = time.monotonic() + 10
    while not server.started and time.monotonic() < deadline:
        time.sleep(0.05)
    assert server.started
    context = ssl.create_default_context(cafile=str(ROOT / ".runtime/localhost.pem"))
    report = {
        "date": datetime.now(UTC).isoformat(),
        "scope": (
            "Local TLS test server, real Supabase atm_test commits, "
            "test identities, simulated scoring; "
            "does not measure real Supabase login, genuine ML or Caddy overhead"
        ),
        "ingestion": [],
    }
    accepted = []

    async def exercise(concurrency):
        gate = asyncio.Semaphore(concurrency)
        durations, errors = [], []
        async with httpx.AsyncClient(
            base_url=f"https://localhost:{port}",
            verify=context,
            trust_env=False,
            timeout=30,
        ) as client:
            # Warm transport, signing and the first database connection outside measurements.
            assert (await client.get("/health/ready")).status_code == 200

            async def one():
                async with gate:
                    body = {**transaction_body, "external_id": str(uuid4())}
                    headers = {**env["headers"]("ingestion"), "Idempotency-Key": str(uuid4())}
                    start = time.perf_counter()
                    try:
                        response = await client.post(
                            "/api/v1/transactions", json=body, headers=headers
                        )
                        if response.status_code != 202:
                            errors.append(response.status_code)
                        else:
                            accepted.append(response.json()["id"])
                    except httpx.HTTPError:
                        errors.append("transport_error")
                    durations.append(time.perf_counter() - start)

            start = time.perf_counter()
            await asyncio.gather(*(one() for _ in range(15)))
            return summary(durations, time.perf_counter() - start, len(errors), concurrency)

    try:
        for concurrency in (1, 3, 5):
            report["ingestion"].append(asyncio.run(exercise(concurrency)))
        processing_start = time.perf_counter()
        processed = 0
        while run_one(env["runtime"], SimulationScorer()):
            processed += 1
        report["processing"] = {
            "jobs": processed,
            "seconds": round(time.perf_counter() - processing_start, 3),
            "scorer": "simulation; measures queue/feature/database work only",
        }
        with env["runtime"].begin() as session:
            report["durability"] = {
                "transactions": session.scalar(select(func.count()).select_from(Transaction)),
                "scored": session.scalar(
                    select(func.count())
                    .select_from(Transaction)
                    .where(Transaction.status == "SCORED")
                ),
                "failed_jobs": session.scalar(
                    select(func.count()).select_from(Job).where(Job.state == "FAILED")
                ),
            }
        output = ROOT / "docs/evidence/local-performance.json"
        output.parent.mkdir(exist_ok=True)
        output.write_text(json.dumps(report, indent=2) + "\n")
        assert len(accepted) == len(set(accepted)) == 45
        assert all(result["errors"] == 0 for result in report["ingestion"])
        assert report["durability"] == {"transactions": 45, "scored": 45, "failed_jobs": 0}
    finally:
        server.should_exit = True
        thread.join(timeout=15)
        listener.close()
        assert not thread.is_alive()
