"""Step 1 - Ingest: pull text out of PDFs, photos of textbook pages, or plain text.

Returns a list of pages: [{"page": 1, "text": "..."}]."""
import io
import re
from pathlib import Path

IMAGE_EXT = {".png", ".jpg", ".jpeg", ".webp", ".bmp", ".tif", ".tiff"}
TEXT_EXT = {".txt", ".md"}


class IngestError(ValueError):
    pass


def _ocr_image(img) -> str:
    try:
        import pytesseract
    except ImportError:
        raise IngestError("Photo upload needs OCR. Run: pip install pytesseract, and install the Tesseract program.")
    try:
        return pytesseract.image_to_string(img, lang="eng")
    except pytesseract.TesseractNotFoundError:
        raise IngestError("Tesseract OCR is not installed on this computer (see README, section 'Photos').")


def _pdf_pages(data: bytes) -> list[dict]:
    import pymupdf

    pages = []
    try:
        doc = pymupdf.open(stream=data, filetype="pdf")
    except Exception:
        raise IngestError("That PDF could not be opened.")
    with doc:
        for i, page in enumerate(doc, 1):
            text = page.get_text("text").strip()
            if len(text) < 25:  # probably a scanned page -> OCR it
                from PIL import Image

                pix = page.get_pixmap(dpi=200)
                img = Image.open(io.BytesIO(pix.tobytes("png")))
                text = _ocr_image(img).strip()
            pages.append({"page": i, "text": text})
    return pages


def read_upload(filename: str, data: bytes) -> list[dict]:
    ext = Path(filename).suffix.lower()
    if ext == ".pdf":
        pages = _pdf_pages(data)
    elif ext in IMAGE_EXT:
        from PIL import Image

        pages = [{"page": 1, "text": _ocr_image(Image.open(io.BytesIO(data))).strip()}]
    elif ext in TEXT_EXT:
        pages = [{"page": 1, "text": data.decode("utf-8", errors="replace")}]
    else:
        raise IngestError("Upload a PDF, a photo (JPG/PNG), or a .txt file.")
    pages = [p for p in pages if p["text"].strip()]
    if not pages:
        raise IngestError("No readable text found in that file.")
    return pages


def text_pages(text: str) -> list[dict]:
    return [{"page": 1, "text": text}]


def select_pages(pages: list[dict], first: int | None, last: int | None) -> list[dict]:
    lo, hi = first or 1, last or 10**9
    chosen = [p for p in pages if lo <= p["page"] <= hi]
    if not chosen:
        raise IngestError("No text on the page range you chose.")
    return chosen


def split_paragraphs(pages: list[dict], min_len: int = 80, max_len: int = 1100) -> list[dict]:
    """Break pages into teachable paragraphs, each remembering its page number."""
    out: list[dict] = []
    for p in pages:
        text = re.sub(r"-\n(?=[a-z])", "", p["text"])  # re-join hyphenated line breaks
        for block in re.split(r"\n\s*\n", text):
            block = re.sub(r"\s*\n\s*", " ", block).strip()
            block = re.sub(r"\s{2,}", " ", block)
            if not block:
                continue
            if out and out[-1]["page"] == p["page"] and len(out[-1]["text"]) < min_len:
                out[-1]["text"] += " " + block  # glue a short line (heading) onto what follows
            else:
                out.append({"page": p["page"], "text": block})
    final: list[dict] = []
    for para in out:
        if len(para["text"]) <= max_len:
            final.append(para)
            continue
        current = ""
        for sent in re.split(r"(?<=[.!?])\s+", para["text"]):
            if current and len(current) + len(sent) > max_len:
                final.append({"page": para["page"], "text": current})
                current = sent
            else:
                current = f"{current} {sent}".strip()
        if current:
            final.append({"page": para["page"], "text": current})
    return final
