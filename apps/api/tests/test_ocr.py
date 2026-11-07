"""N-1: scanned PDFs and pictures are read with Tesseract when it is installed."""

from __future__ import annotations

import io
import shutil

import pytest
from test_knowledge import _pdf

from agentcanvas.services import knowledge as kn
from agentcanvas.services import ocr
from agentcanvas.settings import get_settings

needs_tesseract = pytest.mark.skipif(shutil.which("tesseract") is None, reason="Tesseract is not installed")


def picture(words: str, size: tuple[int, int] = (1700, 400)):
    from PIL import Image, ImageDraw, ImageFont

    img = Image.new("RGB", size, "white")
    ImageDraw.Draw(img).text((60, 120), words, fill="black", font=ImageFont.load_default(size=90))
    return img


def scanned_pdf(*lines: str) -> bytes:
    """A PDF whose pages are only pictures, like the output of a scanner."""
    pages = [picture(line) for line in lines]
    out = io.BytesIO()
    pages[0].save(out, format="PDF", save_all=True, append_images=pages[1:], resolution=200)
    return out.getvalue()


@pytest.fixture(autouse=True)
def fresh_settings(monkeypatch):
    get_settings.cache_clear()
    yield
    get_settings.cache_clear()


@needs_tesseract
def test_a_scanned_pdf_is_read_page_by_page():
    title, text = kn.extract("scan.pdf", scanned_pdf("Invoice total 4200 dollars", "Delivery within thirty days"))
    pages = text.split("\f")
    assert title == "scan" and len(pages) == 2
    assert "Invoice" in pages[0] and "4200" in pages[0]
    assert "Delivery" in pages[1] and "thirty" in pages[1]


@needs_tesseract
def test_a_picture_file_is_read_and_a_pdf_with_text_is_not_sent_to_ocr(monkeypatch):
    _, text = kn.extract("receipt.png", _png(picture("Refunds within thirty days")))
    assert "Refunds" in text and "thirty" in text

    def boom(*a, **k):
        raise AssertionError("a page with text must not be OCR'd")

    monkeypatch.setattr(ocr, "read_image", boom)
    assert kn.extract("guide.pdf", _pdf(["Pension plan basics are described here"]))[1].startswith("Pension plan")


def _png(img) -> bytes:
    out = io.BytesIO()
    img.save(out, format="PNG")
    return out.getvalue()


def test_reading_a_picture_leaves_pillows_limit_and_checks_each_frame(monkeypatch):
    """RT-9: reading a picture raised Pillow's decompression-bomb limit for the whole process, and decoded every
    frame of a multi-page file before reading any. Each frame is now checked by its size and read in turn."""
    from PIL import Image

    monkeypatch.setattr(Image, "MAX_IMAGE_PIXELS", Image.MAX_IMAGE_PIXELS)  # put back whatever a run changes
    before = Image.MAX_IMAGE_PIXELS
    read: list[tuple[int, int]] = []
    monkeypatch.setattr(ocr, "read_image", lambda eng, frame: read.append(frame.size) or "text")
    eng = ocr.Engine("tesseract", "eng", 10, 5)
    small, big = Image.new("L", (40, 40), "white"), Image.new("L", (60, 60), "white")
    tiff = io.BytesIO()
    small.save(tiff, format="TIFF", save_all=True, append_images=[small, big])
    assert ocr.read_file(eng, _png(small)) == "text"
    assert Image.MAX_IMAGE_PIXELS == before
    monkeypatch.setattr(ocr, "MAX_PIXELS", 500)  # 4 x 500 = 2000 pixels a page: 40 x 40 fits, 60 x 60 does not
    read.clear()
    with pytest.raises(kn.InvalidInput, match="too large to read"):
        ocr.read_file(eng, tiff.getvalue())
    assert read == [(40, 40), (40, 40)]  # the frames before it were read one by one


def test_ocr_off_or_missing_says_what_to_do(monkeypatch):
    monkeypatch.setenv("AGENTCANVAS_OCR", "off")
    get_settings.cache_clear()
    assert not ocr.available()
    _, text = kn.extract("scan.pdf", scanned_pdf("Invoice total 4200 dollars"))
    assert text.strip() == ""  # nothing to read: the document is refused when it is added, with the OCR hint
    with pytest.raises(Exception, match="needs OCR"):
        kn.extract("receipt.png", _png(picture("hello")))


def test_only_pages_without_text_are_read_and_the_page_cap_is_said(monkeypatch):
    monkeypatch.setenv("AGENTCANVAS_OCR_MAX_PAGES", "1")
    monkeypatch.setattr(ocr, "engine", lambda: ocr.Engine("tesseract", "eng", 10, 1))
    seen: list[list[int]] = []

    def read(eng, data, pages):
        seen.append(list(pages))
        return {i: f"scanned {i}" for i in pages[: eng.max_pages]}

    monkeypatch.setattr(ocr, "read_pdf_pages", read)
    _, text = kn.extract("mixed.pdf", scanned_pdf("one", "two", "three"))
    assert seen == [[0, 1, 2]]
    assert text.split("\f")[0] == "scanned 0" and "OCR read 1 of the 3 pages" in text.split("\f")[2]


def test_the_language_setting_cannot_carry_arguments(monkeypatch):
    monkeypatch.setattr(shutil, "which", lambda name: "/usr/bin/tesseract")
    for bad in ("eng --oem 0", "eng;rm", "../x", "e"):
        monkeypatch.setenv("AGENTCANVAS_OCR_LANGUAGES", bad)
        get_settings.cache_clear()
        with pytest.raises(Exception, match="language codes"):
            ocr.engine()
    monkeypatch.setenv("AGENTCANVAS_OCR_LANGUAGES", "eng+deu")
    get_settings.cache_clear()
    engine = ocr.engine()
    assert engine is not None and engine.languages == "eng+deu"


@needs_tesseract
async def test_a_scanned_pdf_is_searchable_through_the_api(api_client):
    r = await api_client.post("/v1/projects/prj_default/knowledge", json={"name": "scans"})
    assert r.status_code in (200, 201), r.text
    kb = r.json()["id"]
    up = await api_client.post(
        f"/v1/knowledge/{kb}/documents",
        files=[("files", ("scan.pdf", scanned_pdf("Warranty lasts twenty four months"), "application/pdf"))],
    )
    assert up.status_code in (200, 201), up.text
    hits = await api_client.post(f"/v1/knowledge/{kb}/search", json={"query": "warranty months"})
    assert hits.status_code == 200 and "Warranty" in hits.json()["results"][0]["text"]
