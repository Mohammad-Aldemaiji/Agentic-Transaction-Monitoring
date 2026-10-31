FROM python:3.12-alpine@sha256:1b668429b3511ab407d8e00648891631b0b1a4d7e15e3ca70f38ab5b91ad4ab4 AS builder
COPY --from=ghcr.io/astral-sh/uv:0.12.13@sha256:b485bd65cc2cf1c9a93b3554012c9c3778cf7b1b5fd3d3096ce9e1226c97e1e6 /uv /usr/local/bin/uv
WORKDIR /app
COPY pyproject.toml uv.lock ./
COPY src ./src
RUN uv sync --frozen --no-dev --no-editable

FROM python:3.12-alpine@sha256:1b668429b3511ab407d8e00648891631b0b1a4d7e15e3ca70f38ab5b91ad4ab4
# Bump when refreshing OS security packages so a cached upgrade cannot hide patches.
ARG OS_PATCH_REVISION=2026-10-22
RUN apk upgrade --no-cache && apk add --no-cache espeak-ng && python -m pip uninstall -y pip
RUN addgroup -g 10001 atm && adduser -D -H -u 10001 -G atm atm
WORKDIR /app
COPY --from=builder /app/.venv /app/.venv
ENV PATH="/app/.venv/bin:$PATH" PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1
USER 10001:10001
CMD ["uvicorn", "atm_backend.main:create_app", "--factory", "--host", "0.0.0.0", "--port", "8000", "--no-access-log", "--no-proxy-headers", "--limit-concurrency", "100", "--timeout-keep-alive", "5"]
