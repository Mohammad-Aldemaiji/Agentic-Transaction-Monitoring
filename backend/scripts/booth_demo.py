"""Isolated localhost booth: same API/workflow, scripted identities, genuine E05 inference.

Transaction data stays in the dedicated atm-booth-db container. Presenter sign-in
uses the existing Supabase authentication and read-only identity checks.
"""

import argparse
import hashlib
import json
import os
import secrets
import subprocess
import threading
import time
from datetime import timedelta
from pathlib import Path
from uuid import NAMESPACE_URL, uuid5

import psycopg
import uvicorn
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import rsa
from fastapi.testclient import TestClient
from psycopg import sql
from pydantic import SecretStr
from sqlalchemy import create_engine, select, text
from sqlalchemy.engine import URL, make_url
from sqlalchemy.orm import Session

from atm_backend.e05 import E05Scorer, model_digest
from atm_backend.local_app import create_app
from atm_backend.models import Audit, Base, Case, Principal, Transaction, now
from atm_backend.settings import Settings
from atm_backend.worker import expire_waits, run_one

ROOT = Path(__file__).resolve().parents[2]
RUNTIME = ROOT / ".runtime/booth"
ORIGIN = "https://localhost:8555"


def credentials():
    RUNTIME.mkdir(parents=True, exist_ok=True, mode=0o700)
    path = RUNTIME / "database.json"
    if not path.exists():
        path.write_text(
            json.dumps({"owner": secrets.token_urlsafe(32), "runtime": secrets.token_urlsafe(32)})
        )
        path.chmod(0o600)
    return json.loads(path.read_text())


def database_start(values):
    env = RUNTIME / "postgres.env"
    env.write_text("POSTGRES_DB=atm_booth\nPOSTGRES_PASSWORD=" + values["owner"] + "\n")
    env.chmod(0o600)
    result = subprocess.run(["docker", "inspect", "atm-booth-db"], capture_output=True, text=True)
    if result.returncode:
        subprocess.run(
            [
                "docker",
                "run",
                "--pull=never",
                "-d",
                "--name",
                "atm-booth-db",
                "--label",
                "atm.purpose=isolated-booth",
                "-p",
                "127.0.0.1:55439:5432",
                "--env-file",
                str(env),
                "-v",
                "atm-booth-data:/var/lib/postgresql/data",
                "postgres:17.7",
            ],
            check=True,
            stdout=subprocess.DEVNULL,
        )
    else:
        info = json.loads(result.stdout)[0]
        if info["Config"]["Labels"].get("atm.purpose") != "isolated-booth":
            raise RuntimeError("Existing container is not owned by the booth demo")
        subprocess.run(["docker", "start", "atm-booth-db"], check=True, stdout=subprocess.DEVNULL)
    for _ in range(40):
        try:
            connection = psycopg.connect(
                host="127.0.0.1",
                port=55439,
                dbname="atm_booth",
                user="postgres",
                password=values["owner"],
                connect_timeout=2,
            )
            connection.close()
            return
        except psycopg.OperationalError:
            time.sleep(0.5)
    raise RuntimeError("Booth database did not become ready")


def configuration(values):
    key = RUNTIME / "public.pem"
    if not key.exists():
        private = rsa.generate_private_key(public_exponent=65537, key_size=2048)
        key.write_bytes(
            private.public_key().public_bytes(
                serialization.Encoding.PEM, serialization.PublicFormat.SubjectPublicKeyInfo
            )
        )
    data, model = ROOT / "data/sample/transactions_mini.csv", ROOT / "models/fraud_e05"
    url = URL.create(
        "postgresql+psycopg",
        username="atm_booth_runtime",
        password=values["runtime"],
        host="127.0.0.1",
        port=55439,
        database="atm_booth",
    )
    return Settings(
        _env_file=None,
        environment="booth",
        database_schema="atm_booth",
        database_url=url.render_as_string(hide_password=False),
        issuer="https://booth.local",
        auth_provider="oidc",
        audience="atm-booth",
        public_key_file=key,
        jwks_url=None,
        browser_origin=ORIGIN,
        allowed_hosts=["localhost"],
        require_mfa=False,
        scoring_mode="e05_sample",
        scoring_model_name="fraud_e05",
        scoring_model_digest=model_digest(model),
        e05_model_dir=model,
        e05_data_file=data,
        e05_data_digest=hashlib.sha256(data.read_bytes()).hexdigest(),
        sample_traffic_enabled=True,
        evaluation_report_file=ROOT / ".runtime/e05-evaluation.json",
        agent_enabled=True,
        agent_model="qwen3:4b",
        requests_per_minute=10000,
        piper_voice_dir=ROOT / ".runtime/local-ai/voices",
        metrics_enabled=False,
    )


def initialize(values, cfg):
    owner = URL.create(
        "postgresql+psycopg",
        username="postgres",
        password=values["owner"],
        host="127.0.0.1",
        port=55439,
        database="atm_booth",
    )
    engine = create_engine(
        owner,
        hide_parameters=True,
        execution_options={"schema_translate_map": {"atm": "atm_booth"}},
    )
    with engine.begin() as c:
        c.execute(text("CREATE SCHEMA IF NOT EXISTS atm_booth"))
        Base.metadata.create_all(c)
    with psycopg.connect(
        host="127.0.0.1", port=55439, dbname="atm_booth", user="postgres", password=values["owner"]
    ) as c:
        if not c.execute("SELECT 1 FROM pg_roles WHERE rolname='atm_booth_runtime'").fetchone():
            c.execute(
                sql.SQL("CREATE ROLE atm_booth_runtime LOGIN PASSWORD {}").format(
                    sql.Literal(values["runtime"])
                )
            )
        c.execute("GRANT USAGE ON SCHEMA atm_booth TO atm_booth_runtime")
        c.execute(
            "GRANT SELECT,INSERT,UPDATE,DELETE ON ALL TABLES "
            "IN SCHEMA atm_booth TO atm_booth_runtime"
        )
        c.execute("GRANT USAGE,SELECT ON ALL SEQUENCES IN SCHEMA atm_booth TO atm_booth_runtime")
        for table in ("audit_events", "case_events", "executions"):
            # Match existing table names without assuming a migration-specific alias.
            if c.execute("SELECT to_regclass(%s)", ("atm_booth." + table,)).fetchone()[0]:
                c.execute(
                    sql.SQL("REVOKE UPDATE,DELETE ON atm_booth.{} FROM atm_booth_runtime").format(
                        sql.Identifier(table)
                    )
                )
    with Session(engine) as s, s.begin():
        for role in ("ingestion", "analyst", "supervisor", "admin", "participant", "auditor"):
            subject = "booth-" + role
            if s.scalar(select(Principal.id).where(Principal.subject == subject)) is None:
                s.add(
                    Principal(
                        id=uuid5(NAMESPACE_URL, subject),
                        issuer=cfg.issuer,
                        subject=subject,
                        team="booth",
                        role=role,
                        active=True,
                        valid_after=now() - timedelta(days=1),
                    )
                )
    # Upgrade only the isolated presentation records; keep the old ranking in metadata.
    with Session(engine) as session, session.begin():
        for transaction in session.scalars(
            select(Transaction).where(Transaction.status == "SCORED")
        ):
            result = dict(transaction.result or {})
            metadata = dict(result.get("metadata", {}))
            probability = result.get("supervised_probability")
            if result.get("model_version") != "fraud_e05" or not isinstance(
                probability, (int, float)
            ):
                continue
            if metadata.get("score_kind") == "uncalibrated_supervised_output":
                continue
            metadata.update(
                legacy_ranking_score=result.get("risk_score"),
                score_kind="uncalibrated_supervised_output",
                score_definition="100 times the XGBoost output; not calibrated certainty",
            )
            bands = result.get(
                "severity_thresholds", {"medium": 0.35, "high": 0.65, "critical": 0.85}
            )
            result.update(
                risk_score=probability,
                metadata=metadata,
                severity="CRITICAL"
                if probability >= bands["critical"]
                else "HIGH"
                if probability >= bands["high"]
                else "MEDIUM"
                if probability >= bands["medium"]
                else "LOW",
            )
            transaction.result = result
            case = session.scalar(select(Case).where(Case.transaction_id == transaction.id))
            if case:
                case.version += 1
            session.add(
                Audit(
                    team="booth",
                    actor="presentation-upgrade",
                    kind="score.display_updated",
                    object_id=str(transaction.id),
                    request_id="score-output-v2",
                )
            )
    engine.dispose()


def bootstrap(api, cfg):
    with TestClient(api, base_url=ORIGIN) as client:
        client.headers["Origin"] = ORIGIN

        def role(name):
            r = client.post("/api/v1/booth/role", json={"role": name})
            r.raise_for_status()
            client.headers["X-CSRF-Token"] = r.json()["csrf_token"]

        role("admin")
        models = client.get("/api/v1/models").json()["items"]
        if not models:
            report = json.loads((ROOT / "docs/E05_SAMPLE_RESULTS.json").read_text())["test"][
                "baseline"
            ]
            r = client.post(
                "/api/v1/models",
                json={
                    "name": "fraud_e05",
                    "digest": cfg.scoring_model_digest,
                    "simulation": False,
                    "precision": report["precision"],
                    "recall": report["recall"],
                },
            )
            r.raise_for_status()
            role("supervisor")
            client.post(
                f"/api/v1/models/{r.json()['id']}/review",
                json={"expected_version": 1, "approved": True},
            ).raise_for_status()
        role("admin")
        if not client.get("/api/v1/admin/risk-policy").json()["items"]:
            r = client.post(
                "/api/v1/admin/risk-policy",
                json={
                    "threshold": 0.5,
                    "reason": "Scripted synthetic booth policy; not production approval",
                },
            )
            r.raise_for_status()
            role("supervisor")
            client.post(f"/api/v1/admin/risk-policy/{r.json()['id']}/activate").raise_for_status()
        role("ingestion")
        if not client.get("/api/v1/transactions?limit=1").json()["items"]:
            client.post("/api/v1/booth/feed", json={"count": 100}).raise_for_status()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("action", choices=["serve", "prepare"])
    args = parser.parse_args()
    os.umask(0o077)
    values = credentials()
    database_start(values)
    cfg = configuration(values)
    initialize(values, cfg)
    outer = create_app(cfg, ROOT / "frontend/dist")
    api = outer.app.api
    api.state.booth_bootstrap = True
    try:
        bootstrap(api, cfg)
    finally:
        api.state.booth_bootstrap = False
    if args.action == "prepare":
        return
    from atm_backend.presenter_auth import install

    auth_cfg = Settings(_env_file=ROOT / ".runtime/supabase-app.env")
    connection = make_url(auth_cfg.database_url.get_secret_value())
    if not (connection.username or "").startswith("atm_app."):
        raise ValueError("Presenter checks require the restricted application login")
    connection = connection.update_query_dict(
        {"sslrootcert": str(ROOT / ".runtime/supabase-ca.pem")}
    )
    auth_cfg.database_url = SecretStr(connection.render_as_string(hide_password=False))
    install(api, auth_cfg)
    outer.app.auth_origin = auth_cfg.issuer.removesuffix("/auth/v1")
    stopped = threading.Event()
    scorer = E05Scorer(cfg)

    def worker():
        while not stopped.is_set():
            try:
                expire_waits(api.state.factory)
                if not run_one(api.state.factory, scorer, cfg):
                    stopped.wait(0.5)
            except Exception:
                # Do not log database credentials or payloads; retries are bounded by jobs.
                print("Booth worker waiting after processing error", flush=True)
                stopped.wait(2)

    thread = threading.Thread(target=worker, daemon=True)
    thread.start()
    try:
        uvicorn.run(
            outer,
            host="127.0.0.1",
            port=8555,
            access_log=False,
            proxy_headers=False,
            ssl_certfile=str(ROOT / ".runtime/localhost.pem"),
            ssl_keyfile=str(ROOT / ".runtime/localhost-key.pem"),
        )
    finally:
        stopped.set()
        thread.join(timeout=10)
        api.state.verifier.provider_engine.dispose()


if __name__ == "__main__":
    main()
