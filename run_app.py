"""Launcher: starts the /metrics endpoint and loads the model BEFORE anyone opens the UI,
then starts Streamlit in the same process.

Why: `streamlit run app.py` only executes app.py when a browser connects, so Prometheus
would see the app as DOWN until the first visit. This launcher fixes that.

Usage:  python run_app.py                 (UI on :8501, metrics on :8000)
        CAPTION_MODEL=dummy python run_app.py   (test mode, no model download)
"""
import os
import sys
import threading

from captioner import get_captioner
from metrics import get_metrics

if __name__ == "__main__":
    m = get_metrics()                                   # /metrics is live from this moment
    threading.Thread(target=get_captioner, args=(m,), daemon=True).start()  # load model in background

    from streamlit.web import cli as stcli
    app = os.path.join(os.path.dirname(os.path.abspath(__file__)), "app.py")
    sys.argv = ["streamlit", "run", app, "--server.port", os.environ.get("UI_PORT", "8501")] + sys.argv[1:]
    sys.exit(stcli.main())
