"""Prometheus instrumentation for the captioning app.

All metrics live in one dedicated CollectorRegistry that is created once and
exposed on its own port (default 8000) by prometheus_client's HTTP server,
separate from the Streamlit UI port (8501).

Metric types used:
  Counter   - things that only go up (requests, images, errors, bytes)
  Gauge     - values that go up and down (active requests, queue size, memory)
  Histogram - distributions with buckets (latency, image size, caption length)
  Summary   - _sum and _count pairs for averages (request duration, upload size)
  Info/Enum - static model metadata and the app's current state
"""
import os
import threading
import time

from prometheus_client import (
    CollectorRegistry,
    Counter,
    Enum,
    Gauge,
    GCCollector,
    Histogram,
    Info,
    PlatformCollector,
    ProcessCollector,
    Summary,
    start_http_server,
)

NS = "caption"

LATENCY_BUCKETS = (0.1, 0.25, 0.5, 0.75, 1, 1.5, 2, 3, 5, 8, 13, 20, 30)
SMALL_LATENCY_BUCKETS = (0.005, 0.01, 0.025, 0.05, 0.1, 0.25, 0.5, 1, 2.5, 5)


class AppMetrics:
    """Holds every metric the app records. Created exactly once per process."""

    def __init__(self, registry: CollectorRegistry):
        r = registry
        self.registry = registry

        # ---------------- Counters ----------------
        self.requests = Counter(
            f"{NS}_requests", "Caption requests started (one per single image or one per ZIP)",
            ["mode"], registry=r)
        self.client_requests = Counter(
            f"{NS}_client_requests", "Caption requests per client (custom labels: session, IP, browser)",
            ["mode", "session_id", "client_ip", "browser"], registry=r)
        self.images_processed = Counter(
            f"{NS}_images_processed", "Images that went through the captioning model",
            ["mode", "status"], registry=r)
        self.errors = Counter(
            f"{NS}_errors", "Errors by type",
            ["mode", "error_type"], registry=r)
        self.uploads = Counter(
            f"{NS}_uploads", "Files uploaded by users",
            ["mode", "file_type"], registry=r)
        self.upload_bytes = Counter(
            f"{NS}_upload_bytes", "Total bytes uploaded",
            ["mode"], registry=r)
        self.words_generated = Counter(
            f"{NS}_words_generated", "Total words produced in captions",
            ["mode"], registry=r)
        self.bulk_jobs = Counter(
            f"{NS}_bulk_jobs", "ZIP (bulk) jobs by outcome",
            ["outcome"], registry=r)
        self.page_views = Counter(
            f"{NS}_page_views", "Streamlit script runs (page loads and UI interactions)",
            ["page"], registry=r)

        # ---------------- Gauges ----------------
        self.active_requests = Gauge(
            f"{NS}_active_requests", "Requests currently being processed",
            ["mode"], registry=r)
        self.active_sessions = Gauge(
            f"{NS}_active_sessions", "Browser sessions seen in the last 5 minutes", registry=r)
        self.model_loaded = Gauge(
            f"{NS}_model_loaded", "1 if the captioning model is loaded and ready", registry=r)
        self.model_load_seconds = Gauge(
            f"{NS}_model_load_seconds", "Time it took to load the model at startup", registry=r)
        self.bulk_queue_remaining = Gauge(
            f"{NS}_bulk_queue_remaining", "Images left to caption in the running ZIP job(s)", registry=r)
        self.last_bulk_size = Gauge(
            f"{NS}_last_bulk_size", "Number of images in the most recent ZIP", registry=r)
        self.inference_lock_waiters = Gauge(
            f"{NS}_inference_waiting", "Requests waiting for the CPU model lock", registry=r)
        self.start_time = Gauge(
            f"{NS}_app_start_timestamp_seconds", "Unix time when the app started", registry=r)
        self.start_time.set(time.time())

        # ---------------- Histograms ----------------
        self.inference_seconds = Histogram(
            f"{NS}_inference_seconds", "Model inference time per image",
            ["mode"], buckets=LATENCY_BUCKETS, registry=r)
        self.preprocess_seconds = Histogram(
            f"{NS}_preprocess_seconds", "Image decode + resize time per image",
            ["mode"], buckets=SMALL_LATENCY_BUCKETS, registry=r)
        self.queue_wait_seconds = Histogram(
            f"{NS}_queue_wait_seconds", "Time an image waited for the model lock",
            ["mode"], buckets=SMALL_LATENCY_BUCKETS, registry=r)
        self.zip_extract_seconds = Histogram(
            f"{NS}_zip_extract_seconds", "Time to open and list a ZIP",
            buckets=SMALL_LATENCY_BUCKETS, registry=r)
        self.image_megapixels = Histogram(
            f"{NS}_image_megapixels", "Size of uploaded images in megapixels",
            ["mode"], buckets=(0.1, 0.3, 0.5, 1, 2, 4, 8, 12, 20, 50), registry=r)
        self.caption_length_words = Histogram(
            f"{NS}_text_length_words", "Number of words per generated caption",
            ["mode"], buckets=(2, 4, 6, 8, 10, 12, 15, 20, 30), registry=r)
        self.images_per_zip = Histogram(
            f"{NS}_images_per_zip", "Number of images inside each uploaded ZIP",
            buckets=(1, 5, 10, 20, 50, 100, 200), registry=r)

        # ---------------- Summaries (expose _sum and _count) ----------------
        self.request_duration = Summary(
            f"{NS}_request_duration_seconds", "End-to-end time per request (single image or whole ZIP)",
            ["mode"], registry=r)
        self.upload_size = Summary(
            f"{NS}_upload_size_bytes", "Size of each uploaded file",
            ["mode"], registry=r)
        self.bulk_job_duration = Summary(
            f"{NS}_bulk_job_duration_seconds", "Wall-clock time per ZIP job", registry=r)

        # ---------------- Info / Enum ----------------
        self.model_info = Info(f"{NS}_model", "Captioning model metadata", registry=r)
        self.app_state = Enum(
            f"{NS}_app_state", "What the app is doing right now",
            states=["idle", "processing_single", "processing_bulk"], registry=r)

        # Pre-create every known label combination at 0. Otherwise a series is born at 1 and
        # rate()/increase() miss that first event (Prometheus needs a previous sample).
        modes = ("single", "bulk")
        error_types = ("invalid_image", "unsupported_file", "inference_error", "zip_corrupt",
                       "zip_too_large", "zip_too_many_files", "zip_empty", "zip_member_unreadable")
        for mode in modes:
            self.requests.labels(mode)
            self.upload_bytes.labels(mode)
            self.words_generated.labels(mode)
            self.active_requests.labels(mode)
            for status in ("success", "error"):
                self.images_processed.labels(mode, status)
            for et in error_types:
                self.errors.labels(mode, et)
            for h in (self.inference_seconds, self.preprocess_seconds, self.queue_wait_seconds,
                      self.image_megapixels, self.caption_length_words, self.request_duration, self.upload_size):
                h.labels(mode)
        for outcome in ("completed", "completed_with_errors", "rejected", "aborted"):
            self.bulk_jobs.labels(outcome)
        self.page_views.labels("app")

        # session bookkeeping for the active_sessions gauge
        self._sessions = {}
        self._lock = threading.Lock()

    def touch_session(self, session_id: str, window_s: int = 300):
        now = time.time()
        with self._lock:
            self._sessions[session_id] = now
            for sid in [s for s, t in self._sessions.items() if now - t > window_s]:
                del self._sessions[sid]
            self.active_sessions.set(len(self._sessions))


_METRICS = None
_METRICS_LOCK = threading.Lock()


def get_metrics() -> AppMetrics:
    """Process-wide singleton: metrics + /metrics server are created exactly once,
    no matter how many times Streamlit re-runs app.py."""
    global _METRICS
    with _METRICS_LOCK:
        if _METRICS is None:
            _METRICS = create_metrics()
        return _METRICS


def create_metrics(port: int = None) -> AppMetrics:
    """Build the registry + metrics and start the /metrics HTTP server on its own port."""
    port = int(port or os.environ.get("METRICS_PORT", 8000))
    registry = CollectorRegistry()
    # standard process / python runtime metrics (process_cpu_seconds_total, process_resident_memory_bytes, ...)
    ProcessCollector(registry=registry)
    PlatformCollector(registry=registry)
    GCCollector(registry=registry)
    metrics = AppMetrics(registry)
    try:
        start_http_server(port, registry=registry)
        print(f"[metrics] serving Prometheus metrics on :{port}/metrics")
    except OSError as e:  # port already taken (e.g. a second Streamlit process)
        print(f"[metrics] could not bind :{port} ({e}); metrics endpoint not started")
    return metrics
