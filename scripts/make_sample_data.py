"""Builds test inputs for the app:
  sample_data/single/*.jpg        a few photos for single mode
  sample_data/photos_bulk.zip     N photos for bulk mode
  sample_data/photos_with_junk.zip  photos + a text file + a corrupt image (tests error handling)
  sample_data/not_a_zip.zip       an invalid ZIP (tests the zip_corrupt error)

Photos come from picsum.photos (random real photos). If there is no internet,
it falls back to generated shapes so the pipeline can still be tested.

Usage: python scripts/make_sample_data.py --n 40
"""
import argparse
import io
import os
import random
import zipfile

from PIL import Image, ImageDraw

OUT = os.path.join(os.path.dirname(__file__), "..", "sample_data")


def download(i, w=800, h=600):
    import requests
    r = requests.get(f"https://picsum.photos/seed/caption{i}/{w}/{h}", timeout=15)
    r.raise_for_status()
    return r.content


def synthetic(i, w=800, h=600):
    rnd = random.Random(i)
    img = Image.new("RGB", (w, h), tuple(rnd.randint(0, 255) for _ in range(3)))
    d = ImageDraw.Draw(img)
    for _ in range(8):
        x0, y0 = rnd.randint(0, w - 50), rnd.randint(0, h - 50)
        box = [x0, y0, x0 + rnd.randint(40, 300), y0 + rnd.randint(40, 300)]
        (d.ellipse if rnd.random() < 0.5 else d.rectangle)(box, fill=tuple(rnd.randint(0, 255) for _ in range(3)))
    buf = io.BytesIO()
    img.save(buf, "JPEG", quality=90)
    return buf.getvalue()


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--n", type=int, default=40, help="number of photos in the bulk ZIP")
    ap.add_argument("--offline", action="store_true", help="skip downloading, use generated images")
    args = ap.parse_args()
    os.makedirs(os.path.join(OUT, "single"), exist_ok=True)

    photos, online = [], not args.offline
    for i in range(args.n):
        data = None
        if online:
            try:
                data = download(i)
            except Exception as e:
                print(f"download failed ({e.__class__.__name__}), switching to generated images")
                online = False
        photos.append(data or synthetic(i))
        print(f"\rphotos: {i + 1}/{args.n}", end="")
    print()

    for i in range(5):
        with open(os.path.join(OUT, "single", f"photo_{i:02d}.jpg"), "wb") as f:
            f.write(photos[i])

    with zipfile.ZipFile(os.path.join(OUT, "photos_bulk.zip"), "w") as z:
        for i, p in enumerate(photos):
            z.writestr(f"photos/photo_{i:03d}.jpg", p)

    with zipfile.ZipFile(os.path.join(OUT, "photos_with_junk.zip"), "w") as z:
        for i, p in enumerate(photos[:10]):
            z.writestr(f"photo_{i:03d}.jpg", p)
        z.writestr("notes.txt", "this is not an image")
        z.writestr("broken.jpg", b"\xff\xd8\xff\xe0 definitely not a real jpeg")

    with open(os.path.join(OUT, "not_a_zip.zip"), "wb") as f:
        f.write(b"this is not a zip file at all")

    print(f"done -> {os.path.normpath(OUT)}  (source: {'picsum.photos' if online else 'generated'})")


if __name__ == "__main__":
    main()
