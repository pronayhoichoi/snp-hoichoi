"""Text extraction from PDF / DOCX / TXT with OCR fallback for scanned PDFs.

Uses PyMuPDF (fitz) for PDFs — C-backed, ~10x lower memory than pdfplumber and
handles 100+ page scripts fine on small containers.

Returns (text, ocr_used).
"""
from __future__ import annotations

import gc
import logging
from pathlib import Path

import pymupdf as fitz
from docx import Document

log = logging.getLogger(__name__)

OCR_LANGS = "ben+eng"
MIN_CHARS_PER_PAGE = 40    # below this we assume scanned/image-based → OCR
OCR_PAGE_DPI = 200         # lower than 300 to keep memory tight during OCR


def extract(path: str, mime: str) -> tuple[str, bool]:
    ext = Path(path).suffix.lower()
    if ext == ".txt" or mime == "text/plain":
        return _read_txt(path), False
    if ext == ".docx" or "word" in mime:
        return _read_docx(path), False
    if ext == ".pdf" or "pdf" in mime:
        return _read_pdf(path)
    raise ValueError(f"Unsupported file type: {ext} / {mime}")


def _read_txt(path: str) -> str:
    with open(path, "r", encoding="utf-8", errors="replace") as f:
        return f.read()


def _read_docx(path: str) -> str:
    doc = Document(path)
    lines = [p.text.strip() for p in doc.paragraphs if p.text.strip()]
    return "\n".join(lines)


def _read_pdf(path: str) -> tuple[str, bool]:
    """Try text extraction first (fast, low-mem). Fall back to OCR only if the
    PDF is genuinely image-based."""
    parts: list[str] = []
    scanned_pages = 0
    total_pages = 0

    doc = fitz.open(path)
    try:
        total_pages = doc.page_count
        log.info("pdf: %d pages", total_pages)
        for i in range(total_pages):
            page = doc.load_page(i)
            t = (page.get_text("text") or "").strip()
            parts.append(t)
            if len(t) < MIN_CHARS_PER_PAGE:
                scanned_pages += 1
            page = None
            if i % 25 == 24:
                gc.collect()
    finally:
        doc.close()

    scanned_ratio = scanned_pages / max(total_pages, 1)
    if scanned_ratio < 0.5:
        return _clean("\n".join(parts)), False

    log.info("pdf: %d/%d pages appear scanned, running OCR", scanned_pages, total_pages)
    del parts
    gc.collect()
    return _ocr_pdf(path), True


def _ocr_pdf(path: str) -> str:
    """OCR one page at a time, releasing each rendered image before the next."""
    import pytesseract
    from PIL import Image

    parts: list[str] = []
    doc = fitz.open(path)
    try:
        zoom = OCR_PAGE_DPI / 72
        mat = fitz.Matrix(zoom, zoom)
        for i in range(doc.page_count):
            page = doc.load_page(i)
            pix = page.get_pixmap(matrix=mat, alpha=False)
            img = Image.frombytes("RGB", (pix.width, pix.height), pix.samples)
            pix = None
            page = None
            text = pytesseract.image_to_string(img, lang=OCR_LANGS)
            parts.append(text)
            img.close()
            del img
            if i % 5 == 4:
                gc.collect()
    finally:
        doc.close()
    return _clean("\n".join(parts))


def _clean(text: str) -> str:
    lines = [ln.strip() for ln in text.splitlines()]
    out: list[str] = []
    blank = False
    for ln in lines:
        if not ln:
            if not blank and out:
                out.append("")
            blank = True
        else:
            out.append(ln)
            blank = False
    return "\n".join(out).strip()
