"""Prepare localhost-only TLS and file-mounted secrets without printing secrets.

Reads the existing operator-owned runtime env file. Does not create identities,
change database state, install trust roots, or publish a network service.
"""

import ipaddress
import json
import os
import secrets
import subprocess
from datetime import UTC, datetime, timedelta
from pathlib import Path

from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import ec
from cryptography.x509.oid import ExtendedKeyUsageOID, NameOID
from dotenv import dotenv_values

ROOT = Path(__file__).resolve().parents[2]
RUNTIME = ROOT / ".runtime"


def private_write(path: Path, content: bytes) -> None:
    if path.is_symlink():
        raise ValueError("Refusing a symlink for a secret")
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "wb") as handle:
        handle.write(content)
    path.chmod(0o600)


def certificate() -> None:
    cert_path = RUNTIME / "localhost.pem"
    if cert_path.exists():
        cert = x509.load_pem_x509_certificate(cert_path.read_bytes())
        if cert.not_valid_after_utc < datetime.now(UTC) + timedelta(days=7):
            raise ValueError("Local certificate expires soon; renew it before demonstrating")
        return
    key = ec.generate_private_key(ec.SECP256R1())
    name = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, "localhost")])
    current = datetime.now(UTC)
    cert = (
        x509.CertificateBuilder()
        .subject_name(name)
        .issuer_name(name)
        .public_key(key.public_key())
        .serial_number(x509.random_serial_number())
        .not_valid_before(current - timedelta(minutes=5))
        .not_valid_after(current + timedelta(days=90))
        .add_extension(x509.BasicConstraints(ca=False, path_length=None), critical=True)
        .add_extension(
            x509.SubjectAlternativeName(
                [
                    x509.DNSName("localhost"),
                    x509.IPAddress(ipaddress.ip_address("127.0.0.1")),
                    x509.IPAddress(ipaddress.ip_address("::1")),
                ]
            ),
            critical=False,
        )
        .add_extension(x509.ExtendedKeyUsage([ExtendedKeyUsageOID.SERVER_AUTH]), critical=False)
        .sign(key, hashes.SHA256())
    )
    private_write(
        RUNTIME / "localhost-key.pem",
        key.private_bytes(
            serialization.Encoding.PEM,
            serialization.PrivateFormat.PKCS8,
            serialization.NoEncryption(),
        ),
    )
    private_write(cert_path, cert.public_bytes(serialization.Encoding.PEM))


def main() -> None:
    os.umask(0o077)
    RUNTIME.mkdir(mode=0o700, exist_ok=True)
    RUNTIME.chmod(0o700)
    values = dotenv_values(RUNTIME / "supabase-app.env")
    connection = values.pop("ATM_DATABASE_URL", None)
    if not connection or "atm_app." not in connection:
        raise ValueError("Existing restricted application credentials are required")
    secret_dir = RUNTIME / "secrets"
    secret_dir.mkdir(mode=0o700, exist_ok=True)
    private_write(secret_dir / "ATM_DATABASE_URL", connection.encode())
    scoring_token = values.pop("ATM_SCORING_TOKEN", "") or ""
    private_write(secret_dir / "ATM_SCORING_TOKEN", scoring_token.encode())
    values.update(
        {
            "ATM_BROWSER_ORIGIN": "https://localhost:8443",
            "ATM_ALLOWED_HOSTS": '["localhost"]',
            "ATM_METRICS_ENABLED": "true",
        }
    )
    private_write(
        RUNTIME / "local-demo.env",
        "".join(
            f"{key}='{value}'\n" for key, value in values.items() if value is not None
        ).encode(),
    )
    certificate()
    access = RUNTIME / "monitor-access.json"
    if not access.exists():
        password = secrets.token_urlsafe(32)
        images = json.loads((ROOT / "backend/deploy/images.json").read_text())
        result = subprocess.run(
            [
                "docker",
                "run",
                "--rm",
                "-i",
                "--network",
                "none",
                images["caddy"],
                "caddy",
                "hash-password",
            ],
            input=password + "\n",
            capture_output=True,
            text=True,
            check=True,
        )
        password_hash = result.stdout.strip()
        if not password_hash.startswith("$2"):
            raise ValueError("Unexpected password hash format")
        private_write(access, json.dumps({"username": "presenter", "password": password}).encode())
        private_write(
            RUNTIME / "monitor-auth.caddy",
            ("basic_auth {\n    presenter " + password_hash + "\n}\n").encode(),
        )
    print("Local TLS and protected configuration prepared. No credentials printed.")
    print("API: https://localhost:8443 | Monitoring: https://localhost:9443")
    print("Monitoring credentials: .runtime/monitor-access.json (private)")


if __name__ == "__main__":
    main()
