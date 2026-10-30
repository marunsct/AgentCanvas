"""Reading text from pictures and scanned PDF pages with Tesseract (N-1).

Tesseract is a separate program, found on PATH (the Docker image installs it). Nothing here imports a framework, and
the program is called with fixed arguments and the picture on standard input, so a file's content or name can never
reach a command line.
"""

from __future__ import annotations

import io
import re
import shutil
import subprocess
from dataclasses import dataclass

from agentcanvas.errors import InvalidInput
from agentcanvas.settings import get_settings

IMAGE_SUFFIXES = {".png", ".jpg", ".jpeg", ".tif", ".tiff", ".bmp", ".webp"}
MIN_PAGE_CHARS = 20  # a PDF page with less text than this is treated as a picture of text
RENDER_DPI = 200  # Tesseract reads 200-300 dpi best; more costs time without reading more
MAX_PIXELS = 40_000_000  # a page or picture larger than this is scaled down first (decompression-bomb guard)
_LANGS = re.compile(r"^[A-Za-z0-9_]{2,30}(\+[A-Za-z0-9_]{2,30}){0,4}$")


@dataclass(frozen=True)
class Engine:
    binary: str
    languages: str
    page_timeout_s: int
    max_pages: int


def engine() -> Engine | None:
    """The OCR engine to use, or None when it is switched off or Tesseract is not installed."""
    cfg = get_settings()
    mode = (cfg.ocr or "auto").lower()
    if mode == "off":
        return None
    if mode not in ("auto", "tesseract"):
        raise InvalidInput(f"AGENTCANVAS_OCR must be auto, tesseract or off, not '{cfg.ocr}'")
    binary = shutil.which("tesseract")
    if not binary:
        return None
    langs = cfg.ocr_languages.strip() or "eng"
    if not _LANGS.match(langs):
        raise InvalidInput("AGENTCANVAS_OCR_LANGUAGES must be Tesseract language codes joined by '+', e.g. eng+deu")
    return Engine(binary, langs, max(5, cfg.ocr_page_timeout_s), max(1, cfg.ocr_max_pages))


def available() -> bool:
    try:
        return engine() is not None
    except InvalidInput:
        return False


def _png(image) -> bytes:  # type: ignore[no-untyped-def]
    from PIL import Image

    if image.width * image.height > MAX_PIXELS:
        scale = (MAX_PIXELS / (image.width * image.height)) ** 0.5
        image = image.resize((max(1, int(image.width * scale)), max(1, int(image.height * scale))), Image.Resampling.LANCZOS)
    if image.mode not in ("L", "RGB"):
        image = image.convert("RGB")
    out = io.BytesIO()
    image.save(out, format="PNG")
    return out.getvalue()


def read_image(eng: Engine, image) -> str:  # type: ignore[no-untyped-def]
    """The text in one picture (a Pillow image)."""
    try:
        done = subprocess.run(
            [eng.binary, "stdin", "stdout", "-l", eng.languages, "--psm", "3"],
            input=_png(image), capture_output=True, timeout=eng.page_timeout_s, check=False,
        )  # fmt: skip
    except subprocess.TimeoutExpired:
        raise InvalidInput(f"OCR took longer than {eng.page_timeout_s} s for one page") from None
    if done.returncode != 0:
        detail = done.stderr.decode("utf-8", "replace").strip().splitlines()[-1:] or ["no message"]
        raise InvalidInput(f"OCR failed: {detail[0][:200]}")
    return done.stdout.decode("utf-8", "replace").strip()


def read_file(eng: Engine, data: bytes) -> str:
    """The text in a picture file (PNG, JPEG, TIFF, BMP, WebP); a multi-page TIFF gives one page per frame."""
    from PIL import Image, ImageSequence

    Image.MAX_IMAGE_PIXELS = MAX_PIXELS * 4
    try:
        with Image.open(io.BytesIO(data)) as img:
            frames = [f.copy() for f, _ in zip(ImageSequence.Iterator(img), range(eng.max_pages), strict=False)]
    except (OSError, Image.DecompressionBombError, ValueError) as exc:
        raise InvalidInput(f"the picture could not be read: {exc}") from None
    return "\f".join(read_image(eng, f) for f in frames)


def read_pdf_pages(eng: Engine, data: bytes, pages: list[int]) -> dict[int, str]:
    """OCR of the given zero-based pages of a PDF, rendered to pictures first. At most `max_pages` are read."""
    import pypdfium2 as pdfium

    out: dict[int, str] = {}
    pdf = pdfium.PdfDocument(data)
    try:
        for i in pages[: eng.max_pages]:
            page = pdf[i]
            try:
                w, h = page.get_size()
                scale = RENDER_DPI / 72
                if w * h * scale * scale > MAX_PIXELS:
                    scale = (MAX_PIXELS / (w * h)) ** 0.5
                shot = page.render(scale=scale)  # type: ignore[arg-type] # a float works
                out[i] = read_image(eng, shot.to_pil())
            finally:
                page.close()
    finally:
        pdf.close()
    return out
