# A4: Observability for a CPU Image-Captioning App (Prometheus + Grafana)

A Streamlit app captions images on the CPU with **BLIP** (`Salesforce/blip-image-captioning-base`), in **single** mode
(one image) or **bulk** mode (a ZIP of images). The app is instrumented with `prometheus_client`; Prometheus scrapes
the app and **node_exporter**; Grafana shows a dashboard that goes from service health down to hardware.

```
 Browser ──► Streamlit UI :8501 ──► BLIP model (CPU, one image at a time)
                   │
                   └── /metrics on a separate port :8000 ──┐
 node_exporter :9100 (CPU / RAM / disk / network) ─────────┼──► Prometheus :9090 ──► Grafana :3000
                                                           ┘   (scrape every 5 s)     (refresh every 5 s)
```

## Repository layout
| Path | What it is |
|---|---|
| `app.py` | Streamlit UI: Single image tab, Bulk (ZIP) tab, How-to-use tab |
| `run_app.py` | Launcher: starts `/metrics` and loads the model **before** the first browser visit, then starts Streamlit |
| `metrics.py` | Every Prometheus metric (Counters, Gauges, Histograms, Summaries, Info, Enum) + `/metrics` server |
| `processing.py` | Captioning logic for single images and ZIPs, with error handling and per-step metrics |
| `captioner.py` | CPU model wrapper (BLIP or ViT-GPT2; `dummy` for tests) |
| `prometheus.yml` | Scrape config: `caption-app` (:8000), `node` (:9100), `prometheus` (:9090) |
| `docker-compose.yml` | Prometheus + node_exporter + Grafana (host networking) |
| `grafana/dashboards/caption_dashboard.json` | **Dashboard JSON export** (auto-loaded by Grafana) |
| `grafana/provisioning/` | Auto-configures the Prometheus data source and the dashboard |
| `scripts/build_dashboard.py` | Generates the dashboard JSON (every panel follows the 7 commandments) |
| `scripts/make_sample_data.py` | Creates test photos and ZIPs (including broken files for error handling) |
| `scripts/ui_traffic.py` | Optional: drives the real UI with a headless browser to generate load |
| `tests/` | Unit tests (pytest) |

## Setup (Ubuntu)

```bash
conda create -n a4 python=3.10 -y && conda activate a4
pip install torch --index-url https://download.pytorch.org/whl/cpu
pip install -r requirements.txt
python scripts/make_sample_data.py --n 40      # sample photos + ZIPs in sample_data/
```

### Start the monitoring stack
**Option A: Docker (recommended)**
```bash
docker compose up -d          # Prometheus :9090, node_exporter :9100, Grafana :3000
```
**Option B: no Docker**
```bash
bash scripts/start_monitoring_native.sh     # Prometheus + node_exporter
# Grafana via apt:
sudo apt-get install -y apt-transport-https wget gnupg
sudo mkdir -p /etc/apt/keyrings
wget -q -O - https://apt.grafana.com/gpg.key | gpg --dearmor | sudo tee /etc/apt/keyrings/grafana.gpg > /dev/null
echo "deb [signed-by=/etc/apt/keyrings/grafana.gpg] https://apt.grafana.com stable main" | sudo tee /etc/apt/sources.list.d/grafana.list
sudo apt-get update && sudo apt-get install -y grafana
sudo cp grafana/provisioning/datasources/prometheus.yml /etc/grafana/provisioning/datasources/
sudo systemctl restart grafana-server
# then in Grafana: Dashboards -> New -> Import -> upload grafana/dashboards/caption_dashboard.json
```

### Start the app
```bash
python run_app.py              # UI http://localhost:8501 , metrics http://localhost:8000/metrics
```
The first run downloads the BLIP model (~1 GB). Test mode without a download: `CAPTION_MODEL=dummy python run_app.py`.

### Check everything is connected
- Prometheus targets: http://localhost:9090/targets: `caption-app`, `node`, `prometheus` should all be **UP**
- Grafana: http://localhost:3000 (admin / admin): folder **A4**, dashboard **CPU Image Captioning: Service Health to Hardware**

## Instrumentation (metrics.py)
| Type | Metric | Labels | Why |
|---|---|---|---|
| Counter | `caption_requests_total` | mode | throughput per mode |
| Counter | `caption_client_requests_total` | mode, **session_id, client_ip, browser** | client tracking (custom labels) |
| Counter | `caption_images_processed_total` | mode, status | success vs failure per image |
| Counter | `caption_errors_total` | mode, error_type | invalid_image, unsupported_file, zip_corrupt, zip_too_large, inference_error, ... |
| Counter | `caption_uploads_total` | mode, file_type | what users upload |
| Counter | `caption_upload_bytes_total` | mode | upload volume |
| Counter | `caption_words_generated_total` | mode | model output volume |
| Counter | `caption_bulk_jobs_total` | outcome | completed / completed_with_errors / rejected / aborted |
| Counter | `caption_page_views_total` | page | UI interactions (Streamlit reruns) |
| Gauge | `caption_active_requests` | mode | in-flight requests |
| Gauge | `caption_active_sessions` | | sessions seen in last 5 min |
| Gauge | `caption_bulk_queue_remaining` | | images left in running ZIP jobs |
| Gauge | `caption_inference_waiting` | | requests waiting for the CPU model lock |
| Gauge | `caption_model_loaded`, `caption_model_load_seconds` | | readiness |
| Gauge | `caption_last_bulk_size`, `caption_app_start_timestamp_seconds` | | context |
| Histogram | `caption_inference_seconds` | mode | latency per image (p50/p95/p99, heatmap) |
| Histogram | `caption_preprocess_seconds`, `caption_queue_wait_seconds` | mode | where time goes besides the model |
| Histogram | `caption_zip_extract_seconds`, `caption_images_per_zip` | | bulk job shape |
| Histogram | `caption_image_megapixels`, `caption_text_length_words` | mode | input size, output length |
| Summary | `caption_request_duration_seconds` | mode | end-to-end time (`_sum`, `_count`) |
| Summary | `caption_upload_size_bytes` | mode | upload size (`_sum`, `_count`) |
| Summary | `caption_bulk_job_duration_seconds` | | ZIP job time (`_sum`, `_count`) |
| Info | `caption_model_info` | name, device, threads | model metadata |
| Enum | `caption_app_state` | | idle / processing_single / processing_bulk |
| (auto) | `process_cpu_seconds_total`, `process_resident_memory_bytes`, `python_gc_*` | | app process resources |

Note: the Python client's **Summary** exposes `_sum` and `_count`; Counters are the ones ending in `_total`.

Design choices:
- **Separate port for `/metrics` (8000)** so scraping never competes with the UI.
- **Label combinations are pre-created at 0**, so `rate()` also counts the very first event of each series.
- **High-cardinality labels (session, IP)** sit only on one counter (`caption_client_requests_total`), never on histograms.
- **One image at a time** through a lock, so bulk jobs and parallel users cannot overload the CPU. The wait shows up in `caption_queue_wait_seconds`.

## Dashboard (logical flow: health → traffic → latency → workload → system)
1. **Health:** app UP/DOWN, node_exporter UP/DOWN, model ready, uptime, active sessions, error rate
2. **Traffic:** requests/min by mode, images/s by outcome, errors by type, requests per client (session · IP · browser)
3. **Latency:** p50/p95/p99 inference time, average request time by mode (log scale), latency heatmap, queue wait and preprocess p95
4. **Workload:** bulk queue and in-flight requests, upload volume, caption length and image size, totals
5. **System (node_exporter):** host CPU vs app CPU, host RAM vs app RSS, load average, disk and network throughput, disk usage

**Correlation:** shaded annotations mark running bulk jobs on every time-series panel, and all panels share one crosshair.

**7 commandments:** colour-blind-safe fixed palette (blue = single/healthy, orange = bulk, red only with text);
points plus different line styles per series (readable in print/PDF); every axis named; units and lin/log scale
in each title and axis; legends (table with mean/max/last) on every multi-series panel; short titles; a description
on every panel (hover ⓘ). Refresh: 5 s.

## Tests
```bash
CAPTION_MODEL=dummy pytest -q          # 7 tests: success, bad image, ZIP with junk, corrupt/empty ZIP, aborted job, label init
```

## Demo video (about 1 minute)
1. Open the dashboard (refresh 5 s, last 15 minutes).
2. In the app, caption a couple of single images, then upload `sample_data/photos_bulk.zip` in Bulk mode.
3. Record the dashboard while the bulk job runs: the queue, throughput, latency, and host/app CPU rise together inside the shaded bulk region.

Optional extra load: `pip install playwright && playwright install chromium && python scripts/ui_traffic.py --rounds 2 --sessions 2`

## Exporting the dashboard JSON from Grafana
Dashboard → **Share** (or Export) → **Export** → **Save to file**. The same JSON is also at `grafana/dashboards/caption_dashboard.json`.
