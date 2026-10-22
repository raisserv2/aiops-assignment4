# A4: Observability for a CPU Image-Captioning App (Prometheus + Grafana)

A Streamlit app captions images on the CPU with **BLIP** (`Salesforce/blip-image-captioning-base`).

Browser ──► Streamlit UI :8501 ──► BLIP model (CPU, one image at a time)
│
└── /metrics on a separate port :8000 ──┐
node_exporter :9100 (CPU / RAM / disk / network) ────────┼──► Prometheus :9090 ──► Grafana :3000
┘ (scrape every 5 s) (refresh every 5 s)

## Repository layout

| Path | What it is |
|---|---|
| `app.py` | Streamlit UI: Single image, Bulk (ZIP), How-to-use tabs |
| `run_app.py` | Launcher: starts `/metrics` and loads model before first browser visit |
| `metrics.py` | Prometheus metrics + `/metrics` server |
| `processing.py` | Captioning logic with error handling and per-step metrics |
| `captioner.py` | CPU model wrapper (BLIP, ViT-GPT2, or `dummy` for tests) |
| `prometheus.yml` | Scrape config: caption-app, node, prometheus |
| `docker-compose.yml` | Prometheus + node_exporter + Grafana (host networking) |
| `grafana/dashboards/caption_dashboard.json` | Dashboard JSON (27 panels) |
| `grafana/provisioning/` | Auto-configures datasource and dashboard |
| `scripts/build_dashboard.py` | Generates dashboard JSON (7 commandments) |
| `scripts/make_sample_data.py` | Creates test photos and ZIPs |
| `scripts/ui_traffic.py` | Optional: headless browser load generator |
| `scripts/start_monitoring_native.sh` | Fallback: native Prometheus + node_exporter |
| `tests/test_processing.py` | Unit tests (7 tests) |
| `demo.mp4` | 1-minute video: live dashboard during bulk job |

## Setup (Ubuntu)

```bash
conda create -n a4 python=3.10 -y && conda activate a4
pip install torch --index-url https://download.pytorch.org/whl/cpu
pip install -r requirements.txt
python scripts/make_sample_data.py --n 40
```

### Start monitoring

**Docker (recommended):**
```bash
docker-compose up -d
```

**Native (no Docker):**
```bash
bash scripts/start_monitoring_native.sh
```

### Start app

```bash
python run_app.py
```

Launches on `http://localhost:8501`. First run downloads BLIP (~1 GB).

Test without download:
```bash
CAPTION_MODEL=dummy python run_app.py
```

### Verify services

- Prometheus: http://localhost:9090/targets (all three targets **UP**)
- Grafana: http://localhost:3000 (admin/admin) → folder **A4**
- Metrics: http://localhost:8000/metrics

## Instrumentation

Counters for requests, images processed, errors, uploads; Gauges for active requests, queue depth, model readiness; Histograms for inference time, preprocessing, queue wait; Summaries for end-to-end latency; custom labels for session_id, client_ip, browser.

Pre-initialised label combinations ensure `rate()` captures the first event. High-cardinality labels isolated on single counter only. Separate `/metrics` port prevents scraping competition.

## Dashboard

27 panels: Health (UP/DOWN, uptime, active sessions) → Traffic (requests/min, images/s, errors) → Latency (p50/p95/p99, heatmap) → Workload (queue, upload volume) → System (host vs app CPU/RAM, disk, network).

Colour-blind safe palette (blue=single, orange=bulk, red=errors). Points + line styles readable in print. Every axis named with units and scale. Legends with mean/max/last. Shaded annotations mark bulk jobs. Shared crosshair, 5 s refresh.

## Testing

```bash
CAPTION_MODEL=dummy pytest -q
```

7 tests (success, errors, ZIPs, label init). All pass with dummy model.

## Demo video

`demo.mp4` shows the live dashboard during bulk image processing. Queue depth, throughput, latency, and host/app CPU correlation visible in real time.

## Optional load generation

```bash
pip install playwright && playwright install chromium
python scripts/ui_traffic.py --rounds 2 --sessions 2
```

## Deliverables

- **Source:** App, metrics, processing, tests
- **Infrastructure:** Docker Compose, Prometheus, node_exporter, Grafana configs
- **Dashboard:** `grafana/dashboards/caption_dashboard.json` (27 panels, 40+ PromQL)
- **Demo:** `demo.mp4` (1-minute recording)
