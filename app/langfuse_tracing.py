"""Run-scoped ADK telemetry using explicit, console-saved Langfuse credentials.

A separate OTLP provider per invocation avoids SDK project singletons and
environment fallbacks. Existing runs keep their destination when settings change.
"""
import asyncio
import base64
from contextlib import asynccontextmanager
from contextvars import ContextVar
import logging
import threading

from opentelemetry import trace
from opentelemetry.sdk.trace import ReadableSpan, SpanProcessor, TracerProvider
from opentelemetry.sdk.trace.export import SpanExporter, SpanExportResult
from opentelemetry.sdk.trace.sampling import ALWAYS_ON

from app.services.tracing_config import TracingSettings

logger = logging.getLogger(__name__)
_provider: ContextVar[TracerProvider | None] = ContextVar("run_trace_provider", default=None)
_instrumented = False
_instrument_lock = threading.Lock()


class _MetadataOnlyExporter(SpanExporter):
    """ADK also adds raw request attributes outside OpenInference's redaction."""
    def __init__(self, exporter):
        self.exporter = exporter

    def export(self, spans):
        allowed = {"openinference.span.kind", "llm.model_name", "llm.provider", "tool.name",
                   "gen_ai.request.model", "gen_ai.response.model", "gen_ai.system",
                   "langfuse.session.id", "langfuse.trace.name"}
        sanitized = [ReadableSpan(
            name=span.name, context=span.context, parent=span.parent, resource=span.resource,
            attributes={key: value for key, value in span.attributes.items()
                        if key in allowed or key.startswith(("llm.token_count.", "gen_ai.usage."))},
            kind=span.kind, status=trace.Status(span.status.status_code),
            start_time=span.start_time, end_time=span.end_time,
            instrumentation_scope=span.instrumentation_scope,
        ) for span in spans]
        try:
            return self.exporter.export(sanitized)
        except Exception:
            logger.warning("Langfuse export failed; local tracing is unaffected.")
            return SpanExportResult.FAILURE

    def shutdown(self):
        self.exporter.shutdown()

    def force_flush(self, timeout_millis=30000):
        return self.exporter.force_flush(timeout_millis)


class _RunTracer(trace.Tracer):
    def __init__(self, name, version=None, schema_url=None, attributes=None):
        self.scope = (name, version, schema_url, attributes)

    def _current(self):
        provider = _provider.get()
        return provider.get_tracer(*self.scope) if provider else trace.NoOpTracer()

    def start_span(self, *args, **kwargs):
        return self._current().start_span(*args, **kwargs)

    def start_as_current_span(self, *args, **kwargs):
        return self._current().start_as_current_span(*args, **kwargs)


class _RunTracerProvider(trace.TracerProvider):
    def get_tracer(self, instrumenting_module_name, instrumenting_library_version=None, schema_url=None, attributes=None):
        return _RunTracer(instrumenting_module_name, instrumenting_library_version, schema_url, attributes)


class _RunAttributes(SpanProcessor):
    def __init__(self, run_id: str):
        self.run_id = run_id

    def on_start(self, span, parent_context=None):
        span.set_attribute("langfuse.session.id", self.run_id)
        span.set_attribute("langfuse.trace.name", "Carousel")


def _instrument():
    global _instrumented
    with _instrument_lock:
        if _instrumented:
            return
        from openinference.instrumentation import TraceConfig
        from openinference.instrumentation.google_adk import GoogleADKInstrumentor

        GoogleADKInstrumentor().instrument(
            tracer_provider=_RunTracerProvider(),
            config=TraceConfig(hide_inputs=True, hide_outputs=True, hide_llm_invocation_parameters=True),
        )
        _instrumented = True


def _make_provider(config: TracingSettings, run_id: str) -> TracerProvider:
    from opentelemetry.exporter.otlp.proto.http.trace_exporter import OTLPSpanExporter
    from opentelemetry.sdk.resources import Resource
    from opentelemetry.sdk.trace.export import BatchSpanProcessor

    auth = base64.b64encode(f"{config.public_key}:{config.secret_key}".encode()).decode()
    exporter = OTLPSpanExporter(
        endpoint=config.base_url + "/api/public/otel/v1/traces",
        headers={"Authorization": "Basic " + auth, "x-langfuse-ingestion-version": "4"}, timeout=5,
    )
    provider = TracerProvider(resource=Resource({"service.name": "carousel"}), sampler=ALWAYS_ON, shutdown_on_exit=False)
    provider.add_span_processor(_RunAttributes(run_id))
    provider.add_span_processor(BatchSpanProcessor(
        _MetadataOnlyExporter(exporter), max_queue_size=512, max_export_batch_size=64,
        schedule_delay_millis=1000, export_timeout_millis=5000,
    ))
    return provider


def _shutdown(provider):
    try:
        provider.force_flush(timeout_millis=5000)
    except Exception:
        logger.warning("Langfuse flush failed; local trace and token totals are unaffected.")
    finally:
        try:
            provider.shutdown()
        except Exception:
            logger.warning("Langfuse exporter could not shut down cleanly.")


@asynccontextmanager
async def run_tracing(config: TracingSettings, run_id: str):
    provider = None
    if config.enabled and config.configured:
        try:
            _instrument()
            provider = _make_provider(config, run_id)
        except Exception:
            logger.warning("Langfuse tracing could not start; local trace and token totals are unaffected.")
    token = _provider.set(provider)
    try:
        yield
    finally:
        _provider.reset(token)
        if provider is not None:
            await asyncio.to_thread(_shutdown, provider)


def record_image_span(model: str, endpoint: str, input_tokens: int, output_tokens: int, total_tokens: int):
    provider = _provider.get()
    if provider is None:
        return
    try:
        span = provider.get_tracer("carousel.images").start_span(endpoint, attributes={
            "openinference.span.kind": "LLM", "llm.model_name": model,
            "llm.token_count.prompt": input_tokens, "llm.token_count.completion": output_tokens,
            "llm.token_count.total": total_tokens,
        })
        span.end()
    except Exception:
        logger.debug("Langfuse image span failed; local token totals are unaffected.")
