"""Optional: drives the real Streamlit UI with a headless browser to generate traffic
(useful for testing and for a lively dashboard in the demo video).

Setup (once):  pip install playwright && playwright install chromium
Usage:         python scripts/ui_traffic.py --rounds 3 --sessions 2
"""
import argparse
import glob
import os
import re
import threading
import time
import urllib.request

from playwright.sync_api import sync_playwright

ROOT = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "sample_data")


def active_requests(metrics_url):
    try:
        txt = urllib.request.urlopen(metrics_url, timeout=5).read().decode()
    except Exception:
        return 0
    return sum(float(v) for v in re.findall(r'^caption_active_requests\{[^}]*\} ([0-9.]+)$', txt, re.M))


def wait_done(page, metrics_url, timeout=900):
    """Wait until the app has finished processing (no in-flight requests) and the page settled."""
    page.wait_for_timeout(1500)
    t0 = time.time()
    while active_requests(metrics_url) > 0 and time.time() - t0 < timeout:
        page.wait_for_timeout(1000)
    page.wait_for_timeout(1500)


def upload(page, index, path):
    page.locator('[data-testid="stFileUploaderDropzoneInput"]').nth(index).set_input_files(path)
    page.get_by_text(os.path.basename(path)).first.wait_for(timeout=60_000)
    page.wait_for_timeout(800)


def run_session(url, rounds, sid, shots, metrics_url="http://localhost:8000/metrics"):
    singles = sorted(glob.glob(os.path.join(ROOT, "single", "*.jpg")))
    with sync_playwright() as p:
        browser = p.chromium.launch()
        page = browser.new_page(viewport={"width": 1400, "height": 1000})
        page.goto(url)
        page.get_by_text("CPU Image Captioning").first.wait_for(timeout=60_000)
        for r in range(rounds):
            # ---- single mode ----
            page.get_by_role("tab", name="Single image").click()
            for img in singles[:3]:
                upload(page, 0, img)
                page.get_by_role("button", name="Generate caption").click()
                wait_done(page, metrics_url)
            if shots and r == 0:
                page.screenshot(path=os.path.join(shots, f"single_s{sid}.png"), full_page=True)
            # ---- bulk mode: good zip, zip with junk, invalid zip ----
            page.get_by_role("tab", name="Bulk (ZIP)").click()
            for z in ("photos_bulk.zip", "photos_with_junk.zip", "not_a_zip.zip"):
                upload(page, 1, os.path.join(ROOT, z))
                page.get_by_role("button", name="Caption all images").click()
                wait_done(page, metrics_url)
                if shots and r == 0:
                    page.screenshot(path=os.path.join(shots, f"bulk_{z[:-4]}_s{sid}.png"), full_page=True)
            print(f"session {sid}: round {r + 1}/{rounds} done")
        browser.close()


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--url", default="http://localhost:8501")
    ap.add_argument("--rounds", type=int, default=2)
    ap.add_argument("--sessions", type=int, default=1, help="parallel browser sessions")
    ap.add_argument("--screenshots", default="", help="folder to save UI screenshots (optional)")
    a = ap.parse_args()
    if a.screenshots:
        os.makedirs(a.screenshots, exist_ok=True)
    threads = [threading.Thread(target=run_session, args=(a.url, a.rounds, i, a.screenshots)) for i in range(a.sessions)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
