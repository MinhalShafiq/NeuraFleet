"""
NeuraFleet shared observability: structured logs, request IDs, Prometheus metrics.

``install_observability(app, "<service>")`` does three things:

* switches logging to one JSON-per-line format (``LOG_FORMAT=text`` for humans,
  ``LOG_LEVEL`` for verbosity) where every record carries the current request ID;
* adds an ASGI middleware that reads ``X-Request-ID`` (or generates one), exposes it
  to logs through a context variable, echoes it on the response, and records
  request count / latency / in-flight WebSocket metrics;
* serves Prometheus text at ``GET /metrics``.

The gateway forwards its request ID to downstream services (see ``request_id_var``),
so one user action can be followed across all four services' logs.
"""

from __future__ import annotations

import contextvars
import json
import logging
import os
import re
import time
import uuid
from datetime import UTC, datetime

from prometheus_client import (
    CONTENT_TYPE_LATEST,
    CollectorRegistry,
    Counter,
    Gauge,
    Histogram,
    ProcessCollector,
    generate_latest,
)
from starlette.requests import Request
from starlette.responses import Response

REQUEST_ID_HEADER = "X-Request-ID"
request_id_var: contextvars.ContextVar[str] = contextvars.ContextVar("request_id", default="-")

_SAFE_ID = re.compile(r"[^A-Za-z0-9._-]")
# Probe / scrape traffic would drown real requests at INFO.
_QUIET_PATHS = frozenset({"/health", "/healthz", "/metrics"})
_LATENCY_BUCKETS = (0.005, 0.01, 0.025, 0.05, 0.1, 0.25, 0.5, 1, 2.5, 5, 10)


class JsonFormatter(logging.Formatter):
    def __init__(self, service: str) -> None:
        super().__init__()
        self.service = service

    def format(self, record: logging.LogRecord) -> str:
        entry = {
            "ts": datetime.fromtimestamp(record.created, UTC).isoformat(timespec="milliseconds"),
            "level": record.levelname,
            "service": self.service,
            "logger": record.name,
            "msg": record.getMessage(),
            "request_id": request_id_var.get(),
        }
        entry.update(getattr(record, "http", None) or {})
        if record.exc_info:
            entry["exc"] = self.formatException(record.exc_info)
        return json.dumps(entry, default=str)


def setup_logging(service: str) -> None:
    """Idempotent: one handler on the root logger, uvicorn routed through it."""
    handler = logging.StreamHandler()
    if os.getenv("LOG_FORMAT", "json").lower() == "text":
        handler.setFormatter(logging.Formatter("%(asctime)s [%(levelname)s] %(name)s: %(message)s"))
    else:
        handler.setFormatter(JsonFormatter(service))
    root = logging.getLogger()
    root.handlers[:] = [handler]
    root.setLevel(os.getenv("LOG_LEVEL", "info").upper())
    for name in ("uvicorn", "uvicorn.error"):
        lg = logging.getLogger(name)
        lg.handlers.clear()
        lg.propagate = True
    # Replaced by the request line the middleware emits (which has the request ID).
    logging.getLogger("uvicorn.access").disabled = True


class Observability:
    """Handle returned by ``install_observability``; create domain metrics through it."""

    def __init__(self, service: str) -> None:
        self.service = service
        # A registry per app (not the global one) so several services can live in one
        # process, as they do in the test-suite, without duplicate-metric errors.
        self.registry = CollectorRegistry()
        ProcessCollector(registry=self.registry)
        self.requests = Counter(
            "http_requests_total",
            "HTTP requests handled",
            ["service", "method", "route", "status"],
            registry=self.registry,
        )
        self.latency = Histogram(
            "http_request_duration_seconds",
            "HTTP request latency",
            ["service", "method", "route"],
            buckets=_LATENCY_BUCKETS,
            registry=self.registry,
        )
        self.websockets = Gauge(
            "websocket_connections",
            "Open WebSocket connections",
            ["service"],
            registry=self.registry,
        )

    def counter(self, name: str, doc: str, labels: tuple[str, ...] = ()) -> Counter:
        return Counter(name, doc, labels, registry=self.registry)

    def gauge(self, name: str, doc: str, labels: tuple[str, ...] = ()) -> Gauge:
        return Gauge(name, doc, labels, registry=self.registry)

    def histogram(
        self,
        name: str,
        doc: str,
        buckets: tuple[float, ...] = _LATENCY_BUCKETS,
        labels: tuple[str, ...] = (),
    ) -> Histogram:
        return Histogram(name, doc, labels, buckets=buckets, registry=self.registry)


def _route_template(scope: dict) -> str:
    """The matched route's path *template* ("/fleet/{robot_id}"), never the raw URL: raw
    paths would make one metric series per robot id / attacker-chosen string."""
    route = scope.get("route")
    return getattr(route, "path", None) or "unmatched"


def _clean_id(raw: str) -> str:
    cleaned = _SAFE_ID.sub("", raw)[:64]
    return cleaned or uuid.uuid4().hex[:16]


class ObservabilityMiddleware:
    """Pure ASGI (not BaseHTTPMiddleware) so WebSockets and streaming are untouched."""

    def __init__(self, app, obs: Observability) -> None:
        self.app = app
        self.obs = obs
        self.log = logging.getLogger(f"{obs.service}.access")

    async def __call__(self, scope, receive, send):
        kind = scope["type"]
        if kind not in ("http", "websocket"):
            await self.app(scope, receive, send)
            return

        incoming = dict(scope["headers"]).get(REQUEST_ID_HEADER.lower().encode(), b"").decode()
        rid = _clean_id(incoming)
        token = request_id_var.set(rid)
        start = time.perf_counter()
        status = 500

        async def send_with_id(message):
            nonlocal status
            if message["type"] == "http.response.start":
                status = message["status"]
                message = {
                    **message,
                    "headers": [*message.get("headers", []), (b"x-request-id", rid.encode())],
                }
            await send(message)

        try:
            if kind == "websocket":
                self.obs.websockets.labels(self.obs.service).inc()
                try:
                    await self.app(scope, receive, send)
                finally:
                    self.obs.websockets.labels(self.obs.service).dec()
                status = 101
            else:
                await self.app(scope, receive, send_with_id)
        finally:
            elapsed = time.perf_counter() - start
            route = _route_template(scope)
            method = scope.get("method", "WS")
            if kind == "http":
                self.obs.requests.labels(self.obs.service, method, route, str(status)).inc()
                self.obs.latency.labels(self.obs.service, method, route).observe(elapsed)
            path = scope.get("path", "")
            level = logging.DEBUG if path in _QUIET_PATHS else logging.INFO
            self.log.log(
                level,
                "%s %s -> %s in %.1f ms",
                method,
                path,
                status,
                elapsed * 1000,
                extra={
                    "http": {
                        "method": method,
                        "path": path,
                        "route": route,
                        "status": status,
                        "duration_ms": round(elapsed * 1000, 2),
                    }
                },
            )
            request_id_var.reset(token)


def install_observability(app, service: str) -> Observability:
    setup_logging(service)
    obs = Observability(service)

    async def metrics(_: Request) -> Response:
        return Response(generate_latest(obs.registry), media_type=CONTENT_TYPE_LATEST)

    app.add_route("/metrics", metrics, include_in_schema=False)
    app.add_middleware(ObservabilityMiddleware, obs=obs)
    return obs
