"""Generates grafana/dashboards/caption_dashboard.json.

Every panel follows the 7 commandments:
 1. colour-blind-safe palette (fixed colours per series, never Grafana's auto rainbow)
 2. markers that survive print/PDF (points + different line styles per series)
 3. named axes            4. units and scale (linear/log) stated
 5. legend when >1 series 6. title (+ subtitle in the title's second clause)
 7. a one-line description of the plot's story (hover the (i) icon)

Run:  python scripts/build_dashboard.py
"""
import json
import os

DS = {"type": "prometheus", "uid": "${datasource}"}

# validated colour-blind-safe categorical palette (fixed order)
BLUE, ORANGE, AQUA, YELLOW, MAGENTA, VIOLET = "#2a78d6", "#eb6834", "#1baf7a", "#eda100", "#e87ba4", "#4a3aa7"
GOOD, BAD, NEUTRAL = "#2a78d6", "#e34948", "#8e8e8e"   # status: blue = ok, red = problem (always with text)

_id = [0]


def nid():
    _id[0] += 1
    return _id[0]


def target(expr, legend="", ref="A", fmt="time_series", instant=False):
    t = {"datasource": DS, "expr": expr, "legendFormat": legend, "refId": ref, "format": fmt}
    if instant:
        t["instant"] = True
        t["range"] = False
    return t


def color_override(name, color, dash=None):
    props = [{"id": "color", "value": {"mode": "fixed", "fixedColor": color}}]
    if dash:
        props.append({"id": "custom.lineStyle", "value": {"fill": "dash", "dash": dash}})
    return {"matcher": {"id": "byName", "options": name}, "properties": props}


def row(title, y):
    return {"type": "row", "id": nid(), "title": title, "collapsed": False,
            "gridPos": {"h": 1, "w": 24, "x": 0, "y": y}, "panels": []}


def timeseries(title, desc, targets, unit, axis, x, y, w=12, h=8, series=None, log=False,
               min0=True, stack=False, decimals=None):
    """series: list of (legend_name, colour, dash or None) in fixed order."""
    custom = {
        "axisLabel": axis,
        "axisPlacement": "auto",
        "drawStyle": "line",
        "lineWidth": 2,
        "fillOpacity": 10 if stack else 0,
        "showPoints": "always",
        "pointSize": 4,
        "spanNulls": True,
        "scaleDistribution": {"type": "log", "log": 10} if log else {"type": "linear"},
        "stacking": {"mode": "normal" if stack else "none", "group": "A"},
        "lineStyle": {"fill": "solid"},
    }
    defaults = {"unit": unit, "custom": custom, "color": {"mode": "fixed", "fixedColor": BLUE}}
    if min0 and not log:
        defaults["min"] = 0
    if decimals is not None:
        defaults["decimals"] = decimals
    overrides = [color_override(n, c, d) for n, c, d in (series or [])]
    return {
        "type": "timeseries", "id": nid(), "title": title, "description": desc, "datasource": DS,
        "gridPos": {"h": h, "w": w, "x": x, "y": y},
        "targets": targets,
        "fieldConfig": {"defaults": defaults, "overrides": overrides},
        "options": {
            "legend": {"showLegend": True, "displayMode": "table", "placement": "bottom",
                       "calcs": ["mean", "max", "lastNotNull"]},
            "tooltip": {"mode": "multi", "sort": "desc"},
        },
    }


def stat(title, desc, expr, unit, x, y, w=4, h=4, mappings=None, thresholds=None, decimals=None,
         color_mode="background"):
    defaults = {"unit": unit, "mappings": mappings or [],
                "thresholds": thresholds or {"mode": "absolute", "steps": [{"color": GOOD, "value": None}]},
                "color": {"mode": "thresholds"}}
    if decimals is not None:
        defaults["decimals"] = decimals
    return {
        "type": "stat", "id": nid(), "title": title, "description": desc, "datasource": DS,
        "gridPos": {"h": h, "w": w, "x": x, "y": y},
        "targets": [target(expr, "", "A", instant=True)],
        "fieldConfig": {"defaults": defaults, "overrides": []},
        "options": {"reduceOptions": {"calcs": ["lastNotNull"], "fields": "", "values": False},
                    "colorMode": color_mode, "graphMode": "none", "textMode": "value_and_name",
                    "justifyMode": "center", "orientation": "auto"},
    }


UPDOWN = [{"type": "value", "options": {"0": {"text": "DOWN ✖", "color": BAD},
                                        "1": {"text": "UP ✔", "color": GOOD}}},
          {"type": "special", "options": {"match": "null", "result": {"text": "NO DATA", "color": NEUTRAL}}}]
UPDOWN_TH = {"mode": "absolute", "steps": [{"color": BAD, "value": None}, {"color": GOOD, "value": 1}]}

M = 'mode=~"$mode"'
APP = 'job="caption-app"'
NCPU = 'count(count by (cpu) (node_cpu_seconds_total))'

panels = []
y = 0

panels.append({
    "type": "text", "id": nid(), "title": "How to read this dashboard",
    "gridPos": {"h": 4, "w": 24, "x": 0, "y": y},
    "options": {"mode": "markdown", "content": (
        "**CPU image-captioning service.** Read top to bottom: **1 Health** (is it up?) → **2 Traffic** (how much work?) "
        "→ **3 Latency** (how fast?) → **4 Workload** (what is the app doing internally?) → **5 System** (what does it cost "
        "the machine?).  \nColours are colour-blind safe and fixed: **blue = single-image mode / healthy**, "
        "**orange = bulk (ZIP) mode**, red only for problems (always with text). Shaded regions on time-series panels "
        "mark **running bulk jobs**, so app spikes can be matched to CPU/RAM spikes in section 5. "
        "Hover the ⓘ on any panel for what it shows. All panels refresh every 5 s.")},
})
y += 4

# ---------------- 1 HEALTH ----------------
panels.append(row("1 · Health: is everything up?", y)); y += 1
panels += [
    stat("Caption app", "Is Prometheus able to scrape the app's /metrics endpoint (port 8000)? UP = app process alive.",
         f'up{{{APP}}}', "none", 0, y, mappings=UPDOWN, thresholds=UPDOWN_TH),
    stat("node_exporter", "Is the host metrics exporter (port 9100) reachable? Needed for section 5.",
         'up{job="node"}', "none", 4, y, mappings=UPDOWN, thresholds=UPDOWN_TH),
    stat("Model loaded", "1 when the captioning model has finished loading into memory and can serve requests.",
         "caption_model_loaded", "none", 8, y,
         mappings=[{"type": "value", "options": {"0": {"text": "LOADING", "color": BAD},
                                                 "1": {"text": "READY ✔", "color": GOOD}}}],
         thresholds=UPDOWN_TH),
    stat("App uptime", "Time since the Streamlit app process started.",
         "time() - caption_app_start_timestamp_seconds", "s", 12, y, color_mode="none"),
    stat("Active sessions (5 min)", "Distinct browser sessions that interacted with the app in the last 5 minutes.",
         "caption_active_sessions", "short", 16, y, color_mode="none"),
    stat("Image error rate (5 min)",
         "Share of images that failed (unreadable file, model error) over the last 5 minutes. Red above 5 %.",
         '100 * sum(rate(caption_images_processed_total{status="error"}[5m])) / '
         'clamp_min(sum(rate(caption_images_processed_total[5m])), 1e-9) or vector(0)',
         "percent", 20, y, decimals=1,
         thresholds={"mode": "absolute", "steps": [{"color": GOOD, "value": None}, {"color": BAD, "value": 5}]}),
]
y += 4

# ---------------- 2 TRAFFIC ----------------
panels.append(row("2 · Traffic: how much work is arriving?", y)); y += 1
panels += [
    timeseries("Requests per minute, by mode (linear)",
               "How many caption requests users start. A bulk request is one whole ZIP. Spikes = users uploading.",
               [target(f'sum by (mode) (rate(caption_requests_total{{{M}}}[1m])) * 60', "{{mode}}")],
               "short", "requests / minute", 0, y, series=[("single", BLUE, None), ("bulk", ORANGE, [10, 10])]),
    timeseries("Images captioned per second, by outcome (linear)",
               "Model throughput. 'success' should track uploads; any 'error' line means files were rejected or failed.",
               [target('sum by (status) (rate(caption_images_processed_total[1m]))', "{{status}}")],
               "short", "images / second", 12, y, series=[("success", BLUE, None), ("error", BAD, [10, 10])]),
]
y += 8
panels += [
    timeseries("Errors by type, per minute (linear)",
               "What is going wrong: invalid_image, unsupported_file, zip_corrupt, zip_too_large, inference_error...",
               [target('sum by (error_type) (rate(caption_errors_total[1m])) * 60', "{{error_type}}")],
               "short", "errors / minute", 0, y, w=12,
               series=[("invalid_image", BAD, None), ("unsupported_file", YELLOW, [10, 10]),
                       ("zip_corrupt", MAGENTA, [4, 4]), ("inference_error", VIOLET, [2, 6])]),
    {
        "type": "table", "id": nid(), "title": "Requests by client (session · IP · browser)",
        "description": "Who is using the app. Each row is one browser session, from the custom labels on caption_client_requests_total.",
        "datasource": DS, "gridPos": {"h": 8, "w": 12, "x": 12, "y": y},
        "targets": [target('sum by (session_id, client_ip, browser, mode) (caption_client_requests_total)', "", "A",
                           fmt="table", instant=True)],
        "fieldConfig": {"defaults": {"unit": "short"}, "overrides": [
            {"matcher": {"id": "byName", "options": "Value"},
             "properties": [{"id": "displayName", "value": "requests"}]}]},
        "options": {"showHeader": True, "sortBy": [{"displayName": "requests", "desc": True}]},
        "transformations": [{"id": "organize", "options": {"excludeByName": {"Time": True}}}],
    },
]
y += 8

# ---------------- 3 LATENCY ----------------
panels.append(row("3 · Latency: how fast is each caption?", y)); y += 1
q = lambda p: f'histogram_quantile({p}, sum by (le) (rate(caption_inference_seconds_bucket{{{M}}}[1m])))'
panels += [
    timeseries("Model inference time per image: p50 / p95 / p99 (seconds, linear)",
               "From the caption_inference_seconds histogram. p95 = 95 % of images finish faster than this line. "
               "A rising p99 with flat p50 means occasional slow images.",
               [target(q(0.5), "p50", "A"), target(q(0.95), "p95", "B"), target(q(0.99), "p99", "C")],
               "s", "seconds per image", 0, y,
               series=[("p50", BLUE, None), ("p95", ORANGE, [10, 10]), ("p99", MAGENTA, [2, 6])]),
    timeseries("Average end-to-end request time, by mode (seconds, LOG scale)",
               "From the caption_request_duration_seconds summary (_sum / _count). Single = one image, bulk = a "
               "whole ZIP, so they differ by orders of magnitude, hence the log10 axis.",
               [target(f'sum by (mode) (rate(caption_request_duration_seconds_sum{{{M}}}[5m])) / '
                       f'sum by (mode) (rate(caption_request_duration_seconds_count{{{M}}}[5m]))', "{{mode}}")],
               "s", "seconds per request (log10)", 12, y, log=True,
               series=[("single", BLUE, None), ("bulk", ORANGE, [10, 10])]),
]
y += 8
panels += [
    {
        "type": "heatmap", "id": nid(), "title": "Inference time distribution over time (seconds, bucketed)",
        "description": "Each column is one minute; darker cells = more images finished in that latency bucket. "
                       "Shows the whole distribution, not just percentiles.",
        "datasource": DS, "gridPos": {"h": 8, "w": 12, "x": 0, "y": y},
        "targets": [target(f'sum by (le) (increase(caption_inference_seconds_bucket{{{M}}}[1m]))', "{{le}}",
                           fmt="heatmap")],
        "fieldConfig": {"defaults": {"custom": {"scaleDistribution": {"type": "linear"}}}, "overrides": []},
        "options": {"calculate": False, "cellGap": 1,
                    "color": {"mode": "scheme", "scheme": "Blues", "fill": BLUE, "scale": "exponential",
                              "exponent": 0.5, "steps": 64, "reverse": False},
                    "yAxis": {"axisLabel": "inference time bucket (s)", "unit": "s", "axisPlacement": "left"},
                    "rowsFrame": {"layout": "auto"},
                    "legend": {"show": True},
                    "tooltip": {"mode": "single", "yHistogram": True, "showColorScale": True},
                    "cellValues": {"unit": "short"}},
    },
    timeseries("Waiting + preprocessing time per image, p95 (seconds, linear)",
               "Queue wait = time an image waited for the single CPU model slot (grows when users overlap). "
               "Preprocess = decode + resize. Both should stay far below inference time.",
               [target(f'histogram_quantile(0.95, sum by (le) (rate(caption_queue_wait_seconds_bucket{{{M}}}[1m])))',
                       "queue wait p95", "A"),
                target(f'histogram_quantile(0.95, sum by (le) (rate(caption_preprocess_seconds_bucket{{{M}}}[1m])))',
                       "preprocess p95", "B")],
               "s", "seconds per image", 12, y,
               series=[("queue wait p95", ORANGE, [10, 10]), ("preprocess p95", AQUA, None)]),
]
y += 8

# ---------------- 4 WORKLOAD ----------------
panels.append(row("4 · Workload: what is the app doing right now?", y)); y += 1
panels += [
    timeseries("Bulk queue and in-flight requests (count, linear)",
               "Images still waiting in running ZIP jobs, plus requests currently being processed. Use this panel "
               "to line up bulk uploads with the CPU / RAM panels directly below in section 5.",
               [target("caption_bulk_queue_remaining", "bulk images queued", "A"),
                target(f'sum by (mode) (caption_active_requests{{{M}}})', "in-flight {{mode}}", "B"),
                target("caption_inference_waiting", "waiting for model", "C")],
               "short", "count", 0, y, decimals=0,
               series=[("bulk images queued", ORANGE, None), ("in-flight single", BLUE, [10, 10]),
                       ("in-flight bulk", MAGENTA, [4, 4]), ("waiting for model", VIOLET, [2, 6])]),
    timeseries("Upload volume, by mode (bytes per second, linear)",
               "How much data users push into the app. Large ZIPs show up as tall orange spikes.",
               [target(f'sum by (mode) (rate(caption_upload_bytes_total{{{M}}}[1m]))', "{{mode}}")],
               "Bps", "upload rate", 12, y,
               series=[("single", BLUE, None), ("bulk", ORANGE, [10, 10])]),
]
y += 8
panels += [
    timeseries("Caption length and image size (averages, linear)",
               "Average words per caption and average uploaded image size, from the caption_text_length_words and "
               "caption_image_megapixels histograms (_sum / _count). Checks the model output stays sensible.",
               [target('sum(rate(caption_text_length_words_sum[5m])) / sum(rate(caption_text_length_words_count[5m]))',
                       "words per caption", "A"),
                target('sum(rate(caption_image_megapixels_sum[5m])) / sum(rate(caption_image_megapixels_count[5m]))',
                       "megapixels per image", "B")],
               "short", "words  |  megapixels", 0, y, decimals=1,
               series=[("words per caption", BLUE, None), ("megapixels per image", AQUA, [10, 10])]),
    stat("Images processed (total since start)", "Lifetime count of images captioned, split by mode.",
         'sum by (mode) (caption_images_processed_total{status="success"})', "short", 12, y, w=6, h=8,
         color_mode="none"),
    stat("Bulk jobs (total since start)", "ZIP jobs by outcome: completed, completed_with_errors, rejected.",
         'sum by (outcome) (caption_bulk_jobs_total)', "short", 18, y, w=6, h=8, color_mode="none"),
]
y += 8

# ---------------- 5 SYSTEM ----------------
panels.append(row("5 · System (node_exporter): what does it cost the machine?", y)); y += 1
panels += [
    timeseries("CPU usage: whole machine vs caption app (% of all cores, linear)",
               "Blue = total host CPU from node_exporter; orange = share used by the Streamlit app process. "
               "When a bulk job runs (shaded), both should rise together; the gap is everything else on the machine.",
               [target(f'100 * (1 - avg(rate(node_cpu_seconds_total{{mode="idle"}}[1m])))', "host total", "A"),
                target(f'100 * sum(rate(process_cpu_seconds_total{{{APP}}}[1m])) / scalar({NCPU})', "caption app", "B")],
               "percent", "% of all CPU cores", 0, y,
               series=[("host total", BLUE, None), ("caption app", ORANGE, [10, 10])]),
    timeseries("Memory: host used vs caption app RSS (bytes, linear)",
               "Host used = total minus available RAM. App RSS = memory held by the app (mostly the model). "
               "Bulk jobs should NOT make RSS grow steadily; if they do, something leaks.",
               [target('node_memory_MemTotal_bytes - node_memory_MemAvailable_bytes', "host used", "A"),
                target(f'process_resident_memory_bytes{{{APP}}}', "caption app RSS", "B")],
               "bytes", "memory", 12, y,
               series=[("host used", BLUE, None), ("caption app RSS", ORANGE, [10, 10])]),
]
y += 8
panels += [
    timeseries("Load average, 1 / 5 / 15 min (runnable tasks, linear)",
               "Average number of tasks wanting CPU. Above the core count means the machine is overloaded.",
               [target("node_load1", "1 min", "A"), target("node_load5", "5 min", "B"),
                target("node_load15", "15 min", "C")],
               "short", "tasks", 0, y, w=8,
               series=[("1 min", BLUE, None), ("5 min", ORANGE, [10, 10]), ("15 min", AQUA, [2, 6])]),
    timeseries("Disk throughput, read vs write (bytes per second, linear)",
               "Disk I/O from node_exporter. Model loading and ZIP extraction cause short read bursts.",
               [target('sum(rate(node_disk_read_bytes_total[1m]))', "read", "A"),
                target('sum(rate(node_disk_written_bytes_total[1m]))', "write", "B")],
               "Bps", "disk throughput", 8, y, w=8,
               series=[("read", BLUE, None), ("write", ORANGE, [10, 10])]),
    timeseries("Network traffic, receive vs transmit (bytes per second, linear)",
               "Host network excluding loopback. Uploads from other devices on the network appear as receive spikes.",
               [target('sum(rate(node_network_receive_bytes_total{device!="lo"}[1m]))', "receive", "A"),
                target('sum(rate(node_network_transmit_bytes_total{device!="lo"}[1m]))', "transmit", "B")],
               "Bps", "network throughput", 16, y, w=8,
               series=[("receive", BLUE, None), ("transmit", ORANGE, [10, 10])]),
]
y += 8
panels += [
    {
        "type": "gauge", "id": nid(), "title": "Root disk used (%)",
        "description": "How full the main disk is. Orange above 80 %, red above 90 %.",
        "datasource": DS, "gridPos": {"h": 6, "w": 8, "x": 0, "y": y},
        "targets": [target('100 * (1 - max(node_filesystem_avail_bytes{mountpoint="/"}) / '
                           'max(node_filesystem_size_bytes{mountpoint="/"}))', "root /", instant=True)],
        "fieldConfig": {"defaults": {"unit": "percent", "min": 0, "max": 100, "decimals": 1,
                                     "thresholds": {"mode": "absolute", "steps": [
                                         {"color": GOOD, "value": None}, {"color": ORANGE, "value": 80},
                                         {"color": BAD, "value": 90}]}}, "overrides": []},
        "options": {"showThresholdMarkers": True, "showThresholdLabels": True,
                    "reduceOptions": {"calcs": ["lastNotNull"], "values": False}},
    },
    timeseries("Caption app threads and open files (count, linear)",
               "Python process internals from prometheus_client's process collector. Steady values = no leaks.",
               [target(f'process_open_fds{{{APP}}}', "open files", "A"),
                target(f'sum(python_gc_objects_collected_total{{{APP}}})', "GC objects collected", "B")],
               "short", "count", 8, y, w=16, h=6,
               series=[("open files", BLUE, None), ("GC objects collected", ORANGE, [10, 10])]),
]

dashboard = {
    "uid": "caption-a4",
    "title": "CPU Image Captioning: Service Health to Hardware",
    "description": "A4 observability dashboard: Streamlit BLIP captioning app + node_exporter.",
    "tags": ["A4", "streamlit", "prometheus", "node_exporter"],
    "timezone": "browser",
    "editable": True,
    "graphTooltip": 1,          # shared crosshair across all panels -> line app spikes up with system spikes
    "refresh": "5s",
    "time": {"from": "now-15m", "to": "now"},
    "timepicker": {"refresh_intervals": ["5s", "10s", "30s", "1m", "5m"]},
    "schemaVersion": 39,
    "version": 1,
    "templating": {"list": [
        {"name": "datasource", "label": "Data source", "type": "datasource", "query": "prometheus",
         "current": {"text": "Prometheus", "value": "prometheus"}, "hide": 0},
        {"name": "mode", "label": "Mode", "type": "custom", "query": "single,bulk", "multi": True,
         "includeAll": True, "allValue": ".*", "current": {"text": "All", "value": "$__all"},
         "options": [], "hide": 0},
    ]},
    "annotations": {"list": [
        {"name": "Bulk jobs running", "datasource": DS, "enable": True, "iconColor": ORANGE,
         "expr": "caption_bulk_queue_remaining > 0", "step": "5s",
         "titleFormat": "Bulk ZIP job running", "textFormat": "{{value}} images left", "useValueForTime": False},
    ]},
    "panels": panels,
}

if __name__ == "__main__":
    out = os.path.join(os.path.dirname(__file__), "..", "grafana", "dashboards", "caption_dashboard.json")
    with open(out, "w") as f:
        json.dump(dashboard, f, indent=2, ensure_ascii=False)
    print(f"wrote {os.path.normpath(out)} with {len([p for p in panels if p['type'] != 'row'])} panels")
