"""Aggregate operational metrics on a separate, unpublished container port.

Never use subjects, tokens, accounts, teams, raw paths or query values as labels.
The exporter is intended only for the private Compose monitoring network.
"""

from threading import Thread
from wsgiref.simple_server import WSGIServer

from prometheus_client import (
    CollectorRegistry,
    Counter,
    Gauge,
    Histogram,
    ProcessCollector,
    start_http_server,
)


class Telemetry:
    def __init__(self) -> None:
        self.registry = CollectorRegistry()
        ProcessCollector(registry=self.registry)
        self.requests = Counter(
            "atm_http_responses",
            "HTTP responses by matched route and status",
            ["route", "method", "status"],
            registry=self.registry,
        )
        self.latency = Histogram(
            "atm_http_response_seconds",
            "Time to response headers",
            ["route"],
            buckets=(0.01, 0.05, 0.1, 0.2, 0.5, 1, 2, 5, 10, 30),
            registry=self.registry,
        )
        self.worker_success = Gauge(
            "atm_worker_last_success_timestamp_seconds",
            "Last worker iteration with successful storage access",
            registry=self.registry,
        )
        self.worker_errors = Counter(
            "atm_worker_dependency_failures",
            "Worker dependency failures",
            registry=self.registry,
        )
        self.jobs = Gauge(
            "atm_jobs",
            "Current jobs by bounded state",
            ["state"],
            registry=self.registry,
        )
        self.queue_age = Gauge(
            "atm_oldest_ready_job_seconds",
            "Age of the oldest currently runnable job",
            registry=self.registry,
        )
        self.queue_sample = Gauge(
            "atm_queue_sample_timestamp_seconds",
            "Last successful queue metrics refresh",
            registry=self.registry,
        )
        self._server: WSGIServer | None = None
        self._thread: Thread | None = None

    def start(self, port: int) -> None:
        self._server, self._thread = start_http_server(port, registry=self.registry)

    def close(self) -> None:
        if self._server:
            self._server.shutdown()
            self._server.server_close()
        if self._thread:
            self._thread.join(timeout=2)

    def response(self, route: str, method: str, status: int, elapsed: float) -> None:
        method = (
            method
            if method in {"GET", "POST", "PUT", "PATCH", "DELETE", "HEAD", "OPTIONS"}
            else "OTHER"
        )
        self.requests.labels(route, method, str(status)).inc()
        self.latency.labels(route).observe(elapsed)
