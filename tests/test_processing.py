"""Unit tests for the captioning logic and its metrics (uses the dummy model, no download).

Run:  CAPTION_MODEL=dummy pytest -q
"""
import io
import os
import sys
import zipfile

import pytest
from PIL import Image
from prometheus_client import CollectorRegistry

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
from captioner import Captioner  # noqa: E402
from metrics import AppMetrics  # noqa: E402
from processing import caption_one, process_zip  # noqa: E402


def jpeg(w=320, h=240, color=(200, 30, 30)):
    buf = io.BytesIO()
    Image.new("RGB", (w, h), color).save(buf, "JPEG")
    return buf.getvalue()


def make_zip(files):
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as z:
        for name, data in files.items():
            z.writestr(name, data)
    return buf.getvalue()


@pytest.fixture
def env():
    reg = CollectorRegistry()
    return Captioner("dummy").load(), AppMetrics(reg), reg


def val(reg, name, labels=None):
    return reg.get_sample_value(name, labels or {}) or 0.0


def test_single_image_success(env):
    cap, m, reg = env
    res = caption_one(cap, m, jpeg(), "a.jpg", "single")
    assert res["status"] == "success" and res["caption"]
    assert val(reg, "caption_images_processed_total", {"mode": "single", "status": "success"}) == 1
    assert val(reg, "caption_inference_seconds_count", {"mode": "single"}) == 1
    assert val(reg, "caption_text_length_words_sum", {"mode": "single"}) > 0


def test_invalid_image_is_counted_not_raised(env):
    cap, m, reg = env
    res = caption_one(cap, m, b"not an image", "bad.jpg", "single")
    assert res["status"] == "error"
    assert val(reg, "caption_errors_total", {"mode": "single", "error_type": "invalid_image"}) == 1


def test_bulk_zip_with_junk(env):
    cap, m, reg = env
    z = make_zip({"a.jpg": jpeg(), "b.png": jpeg(), "notes.txt": b"hi", "broken.jpg": b"xx",
                  "__MACOSX/._a.jpg": b"meta"})
    out = process_zip(cap, m, z)
    assert out["ok"]
    assert [r["status"] for r in out["results"]].count("success") == 2
    assert out["skipped"] == ["notes.txt"]
    assert val(reg, "caption_errors_total", {"mode": "bulk", "error_type": "unsupported_file"}) == 1
    assert val(reg, "caption_errors_total", {"mode": "bulk", "error_type": "invalid_image"}) == 1
    assert val(reg, "caption_bulk_jobs_total", {"outcome": "completed_with_errors"}) == 1
    assert val(reg, "caption_bulk_queue_remaining") == 0


def test_corrupt_zip_rejected(env):
    cap, m, reg = env
    out = process_zip(cap, m, b"not a zip")
    assert not out["ok"]
    assert val(reg, "caption_errors_total", {"mode": "bulk", "error_type": "zip_corrupt"}) == 1
    assert val(reg, "caption_bulk_jobs_total", {"outcome": "rejected"}) == 1


def test_empty_zip_rejected(env):
    cap, m, reg = env
    out = process_zip(cap, m, make_zip({"readme.txt": b"no images"}))
    assert not out["ok"]
    assert val(reg, "caption_errors_total", {"mode": "bulk", "error_type": "zip_empty"}) == 1


def test_aborted_job_cleans_up_queue(env):
    cap, m, reg = env
    z = make_zip({f"{i}.jpg": jpeg() for i in range(5)})

    def stop_after_two(i, n, last):
        if i == 2:
            raise KeyboardInterrupt  # stands in for Streamlit stopping the script

    with pytest.raises(KeyboardInterrupt):
        process_zip(cap, m, z, stop_after_two)
    assert val(reg, "caption_bulk_queue_remaining") == 0
    assert val(reg, "caption_bulk_jobs_total", {"outcome": "aborted"}) == 1


def test_labels_preinitialised_at_zero(env):
    _, _, reg = env
    # series must exist at 0 before any traffic, so rate() sees the first event
    assert reg.get_sample_value("caption_errors_total", {"mode": "single", "error_type": "inference_error"}) == 0.0
    assert reg.get_sample_value("caption_requests_total", {"mode": "bulk"}) == 0.0
