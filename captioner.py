"""CPU-only image captioning model wrapper.

Supported models (set with the CAPTION_MODEL environment variable):
  Salesforce/blip-image-captioning-base   (default, ~1 GB download on first run)
  nlpconnect/vit-gpt2-image-captioning
  dummy                                   (TEST ONLY: no download, fake captions)
"""
import os
import threading
import time

from PIL import Image

DEFAULT_MODEL = "Salesforce/blip-image-captioning-base"


class Captioner:
    def __init__(self, model_name: str = None, num_threads: int = None, max_new_tokens: int = 30):
        self.model_name = model_name or os.environ.get("CAPTION_MODEL", DEFAULT_MODEL)
        self.num_threads = int(num_threads or os.environ.get("CAPTION_THREADS", 4))
        self.max_new_tokens = max_new_tokens
        self.device = "cpu"
        self.lock = threading.Lock()  # one inference at a time, so the CPU is never oversubscribed
        self.load_seconds = 0.0
        self._kind = None

    def load(self):
        t0 = time.perf_counter()
        name = self.model_name
        if name == "dummy":
            self._kind = "dummy"
        else:
            import torch
            torch.set_num_threads(self.num_threads)
            if "blip" in name.lower():
                from transformers import BlipForConditionalGeneration, BlipProcessor
                self.processor = BlipProcessor.from_pretrained(name)
                self.model = BlipForConditionalGeneration.from_pretrained(name).eval()
                self._kind = "blip"
            else:
                from transformers import AutoTokenizer, ViTImageProcessor, VisionEncoderDecoderModel
                self.processor = ViTImageProcessor.from_pretrained(name)
                self.tokenizer = AutoTokenizer.from_pretrained(name)
                self.model = VisionEncoderDecoderModel.from_pretrained(name).eval()
                self._kind = "vit-gpt2"
        self.load_seconds = time.perf_counter() - t0
        return self

    def caption(self, image: Image.Image) -> str:
        """Generate a caption for one RGB PIL image. Caller should hold self.lock."""
        if self._kind == "dummy":
            return _dummy_caption(image)
        import torch
        with torch.inference_mode():
            if self._kind == "blip":
                inputs = self.processor(images=image, return_tensors="pt")
                out = self.model.generate(**inputs, max_new_tokens=self.max_new_tokens, num_beams=1)
                return self.processor.decode(out[0], skip_special_tokens=True).strip()
            pixel_values = self.processor(images=image, return_tensors="pt").pixel_values
            out = self.model.generate(pixel_values, max_new_tokens=self.max_new_tokens, num_beams=1)
            return self.tokenizer.decode(out[0], skip_special_tokens=True).strip()


def _dummy_caption(image: Image.Image) -> str:
    """Stand-in used only for testing the plumbing without downloading a model.
    Burns CPU for a short time (proportional to image size) so the metrics look realistic."""
    w, h = image.size
    deadline = time.perf_counter() + 0.3 + 0.6 * min(w * h / 1_000_000, 1.0)
    x = 0
    while time.perf_counter() < deadline:
        x += 1
    r, g, b = image.resize((1, 1)).getpixel((0, 0))
    tone = "bright" if (r + g + b) / 3 > 128 else "dark"
    dominant = ["red", "green", "blue"][max(range(3), key=lambda i: (r, g, b)[i])]
    return f"a {tone} {'wide' if w > h else 'tall'} picture with mostly {dominant} tones"


_CAPTIONER = None
_CAPTIONER_LOCK = threading.Lock()


def get_captioner(metrics=None) -> Captioner:
    """Load the model once per process (thread-safe). Records model metrics if given."""
    global _CAPTIONER
    with _CAPTIONER_LOCK:
        if _CAPTIONER is None:
            if metrics is not None:
                metrics.model_loaded.set(0)
            cap = Captioner().load()
            if metrics is not None:
                metrics.model_loaded.set(1)
                metrics.model_load_seconds.set(cap.load_seconds)
                metrics.model_info.info({"name": cap.model_name, "device": cap.device,
                                         "threads": str(cap.num_threads)})
            _CAPTIONER = cap
        return _CAPTIONER
