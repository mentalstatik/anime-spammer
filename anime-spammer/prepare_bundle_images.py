"""Create compact WebP copies of the image collection for the frozen executable."""

from pathlib import Path
import shutil
import sys

from PIL import Image


SOURCE = Path(__file__).resolve().parent / "images"
DESTINATION = Path(__file__).resolve().parent / ".bundle-images"
WEBP_QUALITY = 30
SUPPORTED_EXTENSIONS = {".jpg", ".jpeg", ".png", ".webp"}


def main() -> None:
    if DESTINATION.exists():
        shutil.rmtree(DESTINATION)
    DESTINATION.mkdir()
    images = sorted(
        path
        for path in SOURCE.iterdir()
        if path.is_file() and path.suffix.lower() in SUPPORTED_EXTENSIONS
    )
    if not images:
        raise SystemExit(f"No supported images found in {SOURCE}")
    for path in images:
        with Image.open(path) as image:
            image.load()
            image.save(
                DESTINATION / f"{path.stem}.webp",
                "WEBP",
                quality=WEBP_QUALITY,
                method=6,
            )
    print(f"Prepared {len(images)} compact WebP images in {DESTINATION}")


if __name__ == "__main__":
    main()
