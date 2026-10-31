from fastapi.testclient import TestClient
from test_scoring_contract import config

from atm_backend.local_app import create_app


def test_native_profile_serves_pages_but_keeps_api_protected(signing_keys, tmp_path):
    (tmp_path / "index.html").write_text("<html><body>Merged dashboard</body></html>")
    settings = config(signing_keys)
    with TestClient(create_app(settings, tmp_path)) as client:
        for page in ["/", "/cases/one"]:
            result = client.get(page)
            assert result.status_code == 200
            assert "Merged dashboard" in result.text
            assert "default-src 'none'" in result.headers["content-security-policy"]
            assert result.headers["cache-control"] == "no-store"
        assert client.get("/assets/missing.js").status_code == 404
        assert client.get("/api/v1/me").status_code == 401
        assert client.get("/api/v1/not-a-real-route").status_code == 404
        assert client.get("/health/live").status_code == 200
        assert client.get("/", headers={"Host": "untrusted.example"}).status_code == 400
