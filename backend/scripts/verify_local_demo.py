"""Black-box HTTPS, isolation and monitoring checks; never print private data.

Optional --exercise-alert stops only the local synthetic worker briefly and always
restarts it. No application records or Supabase configuration are changed.
"""

import argparse
import asyncio
import json
import math
import socket
import ssl
import subprocess
import time
from datetime import UTC, datetime
from pathlib import Path

import httpx

ROOT = Path(__file__).resolve().parents[2]


def compose(*args):
    return subprocess.run(
        ["docker", "compose", *args],
        cwd=ROOT,
        check=True,
        capture_output=True,
        text=True,
    ).stdout


def wait_for(check, seconds=100):
    deadline = time.monotonic() + seconds
    while time.monotonic() < deadline:
        if check():
            return
        time.sleep(3)
    raise RuntimeError("Monitoring did not reach the expected state in time")


async def measure(context, path, count, concurrency):
    gate = asyncio.Semaphore(concurrency)
    samples, errors = [], []
    async with httpx.AsyncClient(
        base_url="https://localhost:8443",
        verify=context,
        trust_env=False,
        timeout=30,
    ) as client:

        async def one():
            async with gate:
                started = time.perf_counter()
                try:
                    if (await client.get(path)).status_code != 200:
                        errors.append("response")
                except httpx.HTTPError:
                    errors.append("transport")
                samples.append(time.perf_counter() - started)

        started = time.perf_counter()
        await asyncio.gather(*(one() for _ in range(count)))
        elapsed = time.perf_counter() - started
    samples.sort()
    return {
        "path": path,
        "requests": count,
        "concurrency": concurrency,
        "errors": len(errors),
        "requests_per_second": round(count / elapsed, 2),
        "p50_ms": round(samples[math.ceil(count * 0.50) - 1] * 1000, 2),
        "p95_ms": round(samples[math.ceil(count * 0.95) - 1] * 1000, 2),
        "max_ms": round(samples[-1] * 1000, 2),
    }


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--exercise-alert", action="store_true")
    args = parser.parse_args()
    context = ssl.create_default_context(cafile=str(ROOT / ".runtime/localhost.pem"))
    access = json.loads((ROOT / ".runtime/monitor-access.json").read_text())
    checks = []

    def check(name, value):
        if not value:
            raise RuntimeError("Check failed: " + name)
        checks.append(name)

    with httpx.Client(verify=context, trust_env=False, timeout=30) as client:
        response = client.get("https://localhost:8443/health/ready")
        check("HTTPS database readiness", response.status_code == 200)
        for header in (
            "strict-transport-security",
            "x-content-type-options",
            "cache-control",
            "content-security-policy",
            "x-frame-options",
            "referrer-policy",
        ):
            check("Security header: " + header, header in response.headers)
        for path in (
            "/api/v1/cases",
            "/api/v1/openapi.json",
            "/api/v1/audit/export",
            "/api/v1/admin/metrics",
            "/api/v1/team/members",
        ):
            check(
                "Unauthorized access denied: " + path,
                client.get("https://localhost:8443" + path).status_code == 401,
            )
        check(
            "Invalid bearer denied",
            client.get(
                "https://localhost:8443/api/v1/me",
                headers={"Authorization": "Bearer invalid"},
            ).status_code
            == 401,
        )
        check(
            "Oversized request rejected",
            client.post(
                "https://localhost:8443/api/v1/transactions",
                content=b"x" * 131073,
            ).status_code
            == 413,
        )
        check(
            "Host spoofing denied",
            client.get(
                "https://localhost:8443/health/live",
                headers={"Host": "untrusted.example"},
            ).status_code
            in (400, 421),
        )
        check(
            "Cross-origin preflight not allowed",
            "access-control-allow-origin"
            not in client.options(
                "https://localhost:8443/api/v1/auth/session",
                headers={
                    "Origin": "https://untrusted.example",
                    "Access-Control-Request-Method": "POST",
                },
            ).headers,
        )
        check(
            "Monitoring requires password",
            client.get(
                "https://localhost:9443/api/v1/query",
                params={"query": "up"},
            ).status_code
            == 401,
        )
        check(
            "Monitoring rejects wrong password",
            client.get(
                "https://localhost:9443/api/v1/query",
                params={"query": "up"},
                auth=("presenter", "wrong"),
            ).status_code
            == 401,
        )

        for asset in ("/", "/app.js", "/style.css"):
            check(
                "Monitoring asset protected: " + asset,
                client.get("https://localhost:9443" + asset).status_code == 401,
            )
            page = client.get(
                "https://localhost:9443" + asset, auth=(access["username"], access["password"])
            )
            check("Monitoring asset available: " + asset, page.status_code == 200)
            check(
                "Monitoring asset CSP: " + asset,
                "default-src 'none'" in page.headers.get("content-security-policy", ""),
            )
        check(
            "Monitoring writes denied",
            client.post(
                "https://localhost:9443/api/v1/query",
                data={"query": "up"},
                auth=(access["username"], access["password"]),
            ).status_code
            == 405,
        )

        def monitor(path, **params):
            result = client.get(
                "https://localhost:9443/api/v1/" + path,
                params=params,
                auth=(access["username"], access["password"]),
            )
            result.raise_for_status()
            return result.json()["data"]

        def targets_up():
            rows = monitor("query", query='up{job=~"atm-.*"}')["result"]
            return len(rows) == 2 and all(row["value"][1] == "1" for row in rows)

        wait_for(targets_up)
        check("Both metric targets healthy", True)
        check("Six monitoring rules loaded", len(monitor("rules")["groups"][0]["rules"]) == 6)
        if args.exercise_alert:
            try:
                compose("stop", "worker")
                wait_for(
                    lambda: any(
                        alert["labels"].get("alertname") == "BackendProcessUnavailable"
                        and alert["labels"].get("job") == "atm-worker"
                        and alert["state"] == "firing"
                        for alert in monitor("alerts")["alerts"]
                    )
                )
                check("Stopped worker triggers a firing alert", True)
            finally:
                compose("up", "-d", "worker")
            wait_for(targets_up)
            wait_for(
                lambda: (
                    not any(
                        alert["labels"].get("alertname") == "BackendProcessUnavailable"
                        for alert in monitor("alerts")["alerts"]
                    )
                )
            )
            check("Worker recovery clears availability alert", True)

    for version in (ssl.TLSVersion.TLSv1_2, ssl.TLSVersion.TLSv1_3):
        tls = ssl.create_default_context(cafile=str(ROOT / ".runtime/localhost.pem"))
        tls.minimum_version = tls.maximum_version = version
        with socket.create_connection(("127.0.0.1", 8443), timeout=5) as raw:
            with tls.wrap_socket(raw, server_hostname="localhost") as wrapped:
                check("Validated " + version.name, wrapped.version() is not None)
    try:
        with socket.create_connection(("127.0.0.1", 8443), timeout=5) as raw:
            context.wrap_socket(raw, server_hostname="untrusted.example")
    except (ssl.SSLError, ConnectionError):
        check("TLS rejects an incorrect server name", True)
    else:
        raise RuntimeError("Incorrect TLS server name was accepted")

    container_ids = compose("ps", "-q").split()
    containers = json.loads(
        subprocess.check_output(["docker", "inspect", *container_ids], text=True)
    )
    for container in containers:
        service = container["Config"]["Labels"]["com.docker.compose.service"]
        host = container["HostConfig"]
        check(
            service + " has no root privileges",
            container["Config"]["User"] not in ("", "0", "root"),
        )
        check(service + " has a read-only filesystem", host["ReadonlyRootfs"])
        check(service + " drops capabilities", "ALL" in host["CapDrop"])
        check(
            service + " disallows new privileges", "no-new-privileges:true" in host["SecurityOpt"]
        )
        check(
            service + " keeps secrets out of environment",
            not any(
                value.startswith(("ATM_DATABASE_URL=", "ATM_SCORING_TOKEN="))
                for value in container["Config"]["Env"]
            ),
        )
        bindings = host.get("PortBindings") or {}
        check(
            service + " only publishes permitted loopback ports",
            all(
                service == "gateway"
                and binding["HostIp"] == "127.0.0.1"
                and binding["HostPort"] in {"8443", "9443"}
                for entries in bindings.values()
                for binding in (entries or [])
            ),
        )
    for path in [
        ROOT / ".runtime/local-demo.env",
        ROOT / ".runtime/localhost-key.pem",
        ROOT / ".runtime/monitor-access.json",
        ROOT / ".runtime/secrets/ATM_DATABASE_URL",
    ]:
        check("Owner-only file: " + path.name, path.stat().st_mode & 0o077 == 0)
    loads = [
        asyncio.run(measure(context, "/health/live", 200, 10)),
        asyncio.run(measure(context, "/health/ready", 20, 4)),
    ]
    check("Bounded live endpoint load has no errors", all(load["errors"] == 0 for load in loads))
    report = {
        "date": datetime.now(UTC).isoformat(),
        "checks_passed": checks,
        "gateway_load": loads,
        "scope": "Loopback-only local stack; no real user sign-in",
    }
    output = ROOT / "docs/evidence/local-acceptance.json"
    output.parent.mkdir(exist_ok=True)
    output.write_text(json.dumps(report, indent=2) + "\n")
    print(f"{len(checks)} local acceptance checks passed; report: {output}")


if __name__ == "__main__":
    main()
