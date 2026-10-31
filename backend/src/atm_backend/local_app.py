"""Same-origin native HTTPS profile: existing protected API plus built dashboard."""

from pathlib import Path
from urllib.parse import urlsplit

from starlette.exceptions import HTTPException
from starlette.middleware.trustedhost import TrustedHostMiddleware
from starlette.responses import Response
from starlette.staticfiles import StaticFiles
from starlette.types import Message, Receive, Scope, Send

from atm_backend.main import create_app as api_app
from atm_backend.settings import Settings


class LocalApp:
    def __init__(self, settings: Settings, directory: Path) -> None:
        self.api = api_app(settings)
        self.static = StaticFiles(directory=str(directory))
        origin = urlsplit(settings.issuer)
        self.auth_origin = f"{origin.scheme}://{origin.netloc}"

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        path = scope.get("path", "")
        if scope["type"] != "http" or path.startswith(("/api/", "/health/")):
            await self.api(scope, receive, send)
            return

        async def secure_send(message: Message) -> None:
            if message["type"] == "http.response.start":
                headers = list(message.get("headers", []))
                csp = (
                    "default-src 'none'; script-src 'self'; style-src 'self'; "
                    f"connect-src 'self' {self.auth_origin}; img-src 'self' data:; "
                    "media-src 'self'; font-src 'self'; frame-ancestors 'none'; "
                    "base-uri 'none'; form-action 'self'; object-src 'none'"
                )
                headers.extend(
                    [
                        (b"content-security-policy", csp.encode()),
                        (b"x-content-type-options", b"nosniff"),
                        (b"x-frame-options", b"DENY"),
                        (b"referrer-policy", b"no-referrer"),
                        (b"cache-control", b"no-store"),
                    ]
                )
                message["headers"] = headers
            await send(message)

        try:
            await self.static(scope, receive, secure_send)
        except HTTPException as error:
            # Only page navigations receive the SPA; missing assets remain 404s.
            if error.status_code == 404 and "." not in path and scope["method"] in {"GET", "HEAD"}:
                await self.static({**scope, "path": "/index.html"}, receive, secure_send)
            else:
                await Response(status_code=error.status_code)(scope, receive, secure_send)


def create_app(settings: Settings, directory: Path) -> TrustedHostMiddleware:
    return TrustedHostMiddleware(
        LocalApp(settings, directory), allowed_hosts=settings.allowed_hosts
    )
