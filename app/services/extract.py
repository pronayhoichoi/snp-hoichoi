"""Text extraction from PDF / DOCX / TXT with OCR fallback for scanned PDFs.

Returns (text, ocr_used). `text` is line-numbered-friendly (one paragraph per line).
"""
from __future__ import annotations

import io
import os
from pathlib import Path

import pdfplumber
from docx import Document
from pdf2image import convert_from_path
import pytesseract

OCR_LANGS = "ben+eng"  # Bengali + English
MIN_CHARS_PER_PAGE = 40  # below this we treat as scanned and OCR


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
    lines: list[str] = []
    for p in doc.paragraphs:
        t = p.text.strip()
        if t:
            lines.append(t)
    return "\n".join(lines)


def _read_pdf(path: str) -> tuple[str, bool]:
    text_pages: list[str] = []
    scanned = False
    with pdfplumber.open(path) as pdf:
        for page in pdf.pages:
            t = (page.extract_text() or "").strip()
            text_pages.append(t)
            if len(t) < MIN_CHARS_PER_PAGE:
                scanned = True
    if not scanned:
        return _clean("\n".join(text_pages)), False
    return _ocr_pdf(path), True


def _ocr_pdf(path: str) -> str:
    images = convert_from_path(path, dpi=300)
    parts: list[str] = []
    for img in images:
        parts.append(pytesseract.image_to_string(img, lang=OCR_LANGS))
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
