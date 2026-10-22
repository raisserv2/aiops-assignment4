"""Streamlit CPU image-captioning app with Prometheus instrumentation.

Run:   streamlit run app.py
UI:    http://localhost:8501
Metrics (separate port): http://localhost:8000/metrics
"""
import io
import logging
import os
import time

import pandas as pd
import streamlit as st
from PIL import Image
from streamlit.runtime.scriptrunner import get_script_run_ctx

from captioner import get_captioner
from metrics import get_metrics
from processing import caption_one, file_type, process_zip

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
log = logging.getLogger("caption.app")

st.set_page_config(page_title="CPU Image Captioning", page_icon="🖼️", layout="wide")


# ---------- created once per process (Streamlit re-runs this script on every click) ----------
@st.cache_resource(show_spinner="Loading the captioning model on CPU (first run downloads it)...")
def load_model():
    cap = get_captioner(get_metrics())
    log.info("model %s ready (load took %.1fs)", cap.model_name, cap.load_seconds)
    return cap


def client_labels():
    """Client metadata used as metric labels: short session id, IP address, browser family."""
    ctx = get_script_run_ctx()
    session_id = ctx.session_id[:8] if ctx else "unknown"
    ip = st.context.ip_address or "127.0.0.1"
    ua = (st.context.headers.get("User-Agent") or "").lower()
    if "edg/" in ua:
        browser = "edge"
    elif "firefox" in ua:
        browser = "firefox"
    elif "chrome" in ua:
        browser = "chrome"
    elif "safari" in ua:
        browser = "safari"
    else:
        browser = "other"
    return session_id, ip, browser


m = get_metrics()
captioner = load_model()
session_id, client_ip, browser = client_labels()
m.touch_session(session_id)
m.page_views.labels("app").inc()

# ---------------------------------- sidebar ----------------------------------
with st.sidebar:
    st.header("Status")
    st.success(f"Model ready: `{captioner.model_name}`")
    st.caption(f"Device: CPU · {captioner.num_threads} threads · loaded in {captioner.load_seconds:.1f}s")
    if captioner.model_name == "dummy":
        st.warning("TEST MODE: dummy model, captions are fake.")
    st.divider()
    st.subheader("Monitoring")
    port = os.environ.get("METRICS_PORT", "8000")
    st.markdown(f"- Metrics: [localhost:{port}/metrics](http://localhost:{port}/metrics)\n"
                "- Prometheus: [localhost:9090](http://localhost:9090)\n"
                "- Grafana: [localhost:3000](http://localhost:3000)")
    st.divider()
    st.caption(f"Your session: `{session_id}` · IP `{client_ip}` · {browser}")

st.title("🖼️ CPU Image Captioning")
st.write("Upload a photo to get a one-line description, or upload a ZIP of photos to caption them all at once. "
         "Everything runs on the CPU of this machine.")

tab_single, tab_bulk, tab_help = st.tabs(["Single image", "Bulk (ZIP)", "How to use"])

# ---------------------------------- single mode ----------------------------------
with tab_single:
    up = st.file_uploader("Choose an image", type=["jpg", "jpeg", "png", "webp", "bmp"], key="single_up")
    if up is not None:
        data = up.getvalue()
        col_img, col_out = st.columns([1, 1])
        with col_img:
            try:
                st.image(Image.open(io.BytesIO(data)), caption=up.name, width="stretch")
            except Exception:
                st.info("Preview not available for this file.")
        with col_out:
            if st.button("Generate caption", type="primary", key="single_btn"):
                mode = "single"
                m.requests.labels(mode).inc()
                m.client_requests.labels(mode, session_id, client_ip, browser).inc()
                m.uploads.labels(mode, file_type(up.name)).inc()
                m.upload_bytes.labels(mode).inc(len(data))
                m.upload_size.labels(mode).observe(len(data))
                m.app_state.state("processing_single")
                try:
                    with m.active_requests.labels(mode).track_inprogress(), \
                            m.request_duration.labels(mode).time(), \
                            st.spinner("Captioning..."):
                        res = caption_one(captioner, m, data, up.name, mode)
                finally:
                    m.app_state.state("idle")
                if res["status"] == "success":
                    st.subheader("Caption")
                    st.markdown(f"### “{res['caption']}”")
                    st.caption(f"Generated in {res['seconds']:.2f} s on CPU")
                else:
                    st.error(f"Could not caption this file: {res['error']}")

# ---------------------------------- bulk mode ----------------------------------
with tab_bulk:
    st.write("Upload a **.zip** containing .jpg / .png / .webp / .bmp images "
             "(up to 200 images). Images are captioned one at a time to keep the CPU stable.")
    zup = st.file_uploader("Choose a ZIP file", type=["zip"], key="bulk_up")
    if zup is not None and st.button("Caption all images", type="primary", key="bulk_btn"):
        mode = "bulk"
        data = zup.getvalue()
        m.requests.labels(mode).inc()
        m.client_requests.labels(mode, session_id, client_ip, browser).inc()
        m.uploads.labels(mode, "zip").inc()
        m.upload_bytes.labels(mode).inc(len(data))
        m.upload_size.labels(mode).observe(len(data))

        progress = st.progress(0.0, text="Starting...")
        live = st.empty()

        def on_progress(i, n, last):
            progress.progress(i / n, text=f"Captioned {i} of {n} images")
            if last["status"] == "success":
                live.caption(f"{last['file']}: {last['caption']}")

        m.app_state.state("processing_bulk")
        try:
            with m.active_requests.labels(mode).track_inprogress(), m.request_duration.labels(mode).time():
                out = process_zip(captioner, m, data, on_progress)
        finally:
            m.app_state.state("idle")

        if not out["ok"]:
            progress.empty()
            st.error(out["error"])
        else:
            res = out["results"]
            ok = sum(r["status"] == "success" for r in res)
            c1, c2, c3, c4 = st.columns(4)
            c1.metric("Images", len(res))
            c2.metric("Captioned", ok)
            c3.metric("Failed", len(res) - ok)
            c4.metric("Total time", f"{out['seconds']:.1f} s")
            if out["skipped"]:
                st.info(f"Skipped {len(out['skipped'])} non-image file(s): " + ", ".join(out["skipped"][:5]))
            df = pd.DataFrame(res)[["file", "caption", "status", "error", "seconds"]]
            st.dataframe(df, width="stretch", hide_index=True)
            st.download_button("Download captions (CSV)", df.to_csv(index=False).encode(),
                               file_name="captions.csv", mime="text/csv")

# ---------------------------------- help ----------------------------------
with tab_help:
    st.markdown("""
**Single image**
1. Open the *Single image* tab and choose a photo (.jpg, .png, .webp, .bmp).
2. Click **Generate caption**. The caption appears next to the photo in a few seconds.

**Many images at once**
1. Put your photos in a folder and compress it to a **.zip** file.
2. Open the *Bulk (ZIP)* tab, choose the ZIP and click **Caption all images**.
3. Watch the progress bar, then download all captions as a CSV file.

**Good to know**
- Everything runs on this computer's CPU; nothing is sent to the internet.
- Large images are automatically resized, so any photo size works.
- Files that are not images (or are damaged) are skipped and reported, they do not stop the job.
""")
