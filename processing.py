"""Core captioning logic (no Streamlit code here, so it can be unit-tested).

Every step records its metrics. Bulk ZIP jobs are processed one image at a
time, so a large ZIP never overloads the CPU or memory.
"""
import io
import logging
import os
import time
import zipfile

from PIL import Image, ImageOps, UnidentifiedImageError

log = logging.getLogger("caption.processing")

IMAGE_EXTS = {".jpg", ".jpeg", ".png", ".webp", ".bmp"}
MAX_SIDE = 768                       # images are downscaled to this before inference
MAX_ZIP_FILES = 200                  # safety limits for bulk mode
MAX_ZIP_UNCOMPRESSED = 500 * 1024 * 1024
MAX_IMAGE_PIXELS = 50_000_000

Image.MAX_IMAGE_PIXELS = MAX_IMAGE_PIXELS


class ZipJobError(Exception):
    def __init__(self, error_type, message):
        super().__init__(message)
        self.error_type = error_type


def file_type(name: str) -> str:
    ext = os.path.splitext(name.lower())[1]
    return ext.lstrip(".") or "none"


def load_image(data: bytes):
    """Decode, fix orientation, convert to RGB and downscale. Returns (image, megapixels)."""
    img = Image.open(io.BytesIO(data))
    img.load()
    megapixels = img.width * img.height / 1e6
    img = ImageOps.exif_transpose(img).convert("RGB")
    img.thumbnail((MAX_SIDE, MAX_SIDE))
    return img, megapixels


def caption_one(captioner, m, data: bytes, filename: str, mode: str) -> dict:
    """Caption a single image and record all per-image metrics. Never raises."""
    result = {"file": filename, "caption": "", "status": "success", "error": "", "seconds": 0.0}
    t_start = time.perf_counter()
    try:
        with m.preprocess_seconds.labels(mode).time():
            img, mp = load_image(data)
        m.image_megapixels.labels(mode).observe(mp)
    except (UnidentifiedImageError, OSError, ValueError, Image.DecompressionBombError) as e:
        log.warning("invalid image %s: %s", filename, e)
        m.errors.labels(mode, "invalid_image").inc()
        m.images_processed.labels(mode, "error").inc()
        result.update(status="error", error=f"not a readable image ({type(e).__name__})")
        return result

    try:
        t_wait = time.perf_counter()
        m.inference_lock_waiters.inc()
        with captioner.lock:
            m.inference_lock_waiters.dec()
            m.queue_wait_seconds.labels(mode).observe(time.perf_counter() - t_wait)
            with m.inference_seconds.labels(mode).time():
                text = captioner.caption(img)
    except Exception as e:  # model failure must not crash the app
        log.exception("inference failed for %s", filename)
        m.errors.labels(mode, "inference_error").inc()
        m.images_processed.labels(mode, "error").inc()
        result.update(status="error", error=f"model error ({type(e).__name__})")
        return result

    words = len(text.split())
    m.caption_length_words.labels(mode).observe(words)
    m.words_generated.labels(mode).inc(words)
    m.images_processed.labels(mode, "success").inc()
    result.update(caption=text, seconds=round(time.perf_counter() - t_start, 3))
    return result


def list_zip_images(m, zip_bytes: bytes):
    """Open a ZIP and return [(name, bytes_loader)] for image entries, enforcing safety limits."""
    with m.zip_extract_seconds.time():
        try:
            zf = zipfile.ZipFile(io.BytesIO(zip_bytes))
        except zipfile.BadZipFile as e:
            raise ZipJobError("zip_corrupt", "This file is not a valid ZIP archive.") from e
        infos = [i for i in zf.infolist() if not i.is_dir()
                 and not i.filename.startswith("__MACOSX/")
                 and not os.path.basename(i.filename).startswith(".")]
        total = sum(i.file_size for i in infos)
        if total > MAX_ZIP_UNCOMPRESSED:
            raise ZipJobError("zip_too_large", f"ZIP expands to {total/1e6:.0f} MB (limit {MAX_ZIP_UNCOMPRESSED/1e6:.0f} MB).")
        images = [i for i in infos if os.path.splitext(i.filename.lower())[1] in IMAGE_EXTS]
        skipped = [i.filename for i in infos if i not in images]
        if len(images) > MAX_ZIP_FILES:
            raise ZipJobError("zip_too_many_files", f"ZIP has {len(images)} images (limit {MAX_ZIP_FILES}).")
        if not images:
            raise ZipJobError("zip_empty", "No images (.jpg, .png, .webp, .bmp) found in the ZIP.")
    return zf, images, skipped


def process_zip(captioner, m, zip_bytes: bytes, on_progress=None) -> dict:
    """Caption every image in a ZIP, one at a time. Returns a summary dict. Never raises."""
    mode = "bulk"
    t0 = time.perf_counter()
    try:
        zf, images, skipped = list_zip_images(m, zip_bytes)
    except ZipJobError as e:
        m.errors.labels(mode, e.error_type).inc()
        m.bulk_jobs.labels("rejected").inc()
        return {"ok": False, "error": str(e), "results": [], "skipped": []}

    for name in skipped:
        m.errors.labels(mode, "unsupported_file").inc()

    n = len(images)
    m.images_per_zip.observe(n)
    m.last_bulk_size.set(n)
    m.bulk_queue_remaining.inc(n)
    results = []
    aborted = True
    try:
        for idx, info in enumerate(images, start=1):
            try:
                data = zf.read(info)
            except Exception as e:  # corrupt member inside an otherwise valid ZIP
                log.warning("cannot read %s from zip: %s", info.filename, e)
                m.errors.labels(mode, "zip_member_unreadable").inc()
                m.images_processed.labels(mode, "error").inc()
                results.append({"file": info.filename, "caption": "", "status": "error",
                                "error": "could not extract from ZIP", "seconds": 0.0})
            else:
                results.append(caption_one(captioner, m, data, info.filename, mode))
            m.bulk_queue_remaining.dec()
            if on_progress:
                on_progress(idx, n, results[-1])
        aborted = False
    finally:
        if aborted:  # user left / clicked something else mid-job (Streamlit stops the script)
            m.bulk_jobs.labels("aborted").inc()
        # if the user closes the tab mid-job, remove what is left from the queue gauge
        remaining = n - len(results)
        if remaining:
            m.bulk_queue_remaining.dec(remaining)

    elapsed = time.perf_counter() - t0
    m.bulk_job_duration.observe(elapsed)
    failed = sum(r["status"] == "error" for r in results)
    m.bulk_jobs.labels("completed" if failed == 0 else "completed_with_errors").inc()
    return {"ok": True, "error": "", "results": results, "skipped": skipped, "seconds": elapsed}
