"""OpenTelemetry setup shared by all services.

Traces go to Langfuse (LLM-aware UI: conversations, turns, token cost) when its
keys are set, and to any extra OTLP/HTTP endpoint (Tempo, Jaeger, ...) when
OTEL_EXPORTER_OTLP_ENDPOINT is set. Metrics go to the OTLP endpoint if present.
Sentry is initialised when SENTRY_DSN is set.
"""

from __future__ import annotations

import base64
import logging

from opentelemetry import metrics, trace
from opentelemetry.sdk.metrics import MeterProvider
from opentelemetry.sdk.metrics.export import PeriodicExportingMetricReader
from opentelemetry.sdk.resources import Resource
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import BatchSpanProcessor

from avatar_core.settings import ServiceSettings

log = logging.getLogger(__name__)
_tracer_provider: TracerProvider | None = None


def _parse_headers(raw: str | None) -> dict[str, str]:
    if not raw:
        return {}
    pairs = (item.split("=", 1) for item in raw.split(",") if "=" in item)
    return {k.strip(): v.strip() for k, v in pairs}


def langfuse_otlp(settings: ServiceSettings) -> tuple[str, dict[str, str]] | None:
    if not (settings.langfuse_host and settings.langfuse_public_key and settings.langfuse_secret_key):
        return None
    token = base64.b64encode(f"{settings.langfuse_public_key}:{settings.langfuse_secret_key}".encode()).decode()
    endpoint = settings.langfuse_host.rstrip("/") + "/api/public/otel/v1/traces"
    return endpoint, {"Authorization": f"Basic {token}"}


def setup_telemetry(settings: ServiceSettings) -> TracerProvider:
    """Idempotent: returns the process-wide TracerProvider."""
    global _tracer_provider
    if _tracer_provider is not None:
        return _tracer_provider

    from opentelemetry.exporter.otlp.proto.http.metric_exporter import OTLPMetricExporter
    from opentelemetry.exporter.otlp.proto.http.trace_exporter import OTLPSpanExporter

    resource = Resource.create(
        {"service.name": settings.service_name, "deployment.environment": settings.environment}
    )
    provider = TracerProvider(resource=resource)
    exporters: list[str] = []

    if lf := langfuse_otlp(settings):
        endpoint, headers = lf
        provider.add_span_processor(BatchSpanProcessor(OTLPSpanExporter(endpoint=endpoint, headers=headers)))
        exporters.append("langfuse")

    if settings.otel_exporter_otlp_endpoint:
        base = settings.otel_exporter_otlp_endpoint.rstrip("/")
        headers = _parse_headers(settings.otel_exporter_otlp_headers)
        provider.add_span_processor(
            BatchSpanProcessor(OTLPSpanExporter(endpoint=f"{base}/v1/traces", headers=headers))
        )
        reader = PeriodicExportingMetricReader(
            OTLPMetricExporter(endpoint=f"{base}/v1/metrics", headers=headers), export_interval_millis=15000
        )
        metrics.set_meter_provider(MeterProvider(resource=resource, metric_readers=[reader]))
        exporters.append("otlp")

    trace.set_tracer_provider(provider)
    _tracer_provider = provider

    if settings.sentry_dsn:
        try:
            import sentry_sdk

            sentry_sdk.init(
                dsn=settings.sentry_dsn,
                environment=settings.environment,
                traces_sample_rate=0.0,  # tracing handled by OTel
            )
            exporters.append("sentry")
        except ImportError:
            log.warning("SENTRY_DSN set but sentry-sdk not installed (avatar-core[sentry])")

    log.info("telemetry ready", extra={"fields": {"exporters": exporters or ["none"]}})
    return provider


def shutdown_telemetry() -> None:
    if _tracer_provider is not None:
        _tracer_provider.force_flush(timeout_millis=5000)


def meter(name: str = "avatar_talk") -> metrics.Meter:
    return metrics.get_meter(name)
