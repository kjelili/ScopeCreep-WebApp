"""
Scope-document and email-CSV extraction (FR1, FR2).

PDF text:  pypdf   — https://pypdf.readthedocs.io/
DOCX text: python-docx — https://python-docx.readthedocs.io/
CSV: utf-8 first, then utf-8-sig, then latin-1 (the original prototype
forced latin-1 for everything, which mangled UTF-8 email bodies).
"""

from __future__ import annotations

import csv
import io


def extract_scope_text(filename: str, data: bytes) -> str:
    name = (filename or "").lower()
    if name.endswith(".pdf"):
        from pypdf import PdfReader
        reader = PdfReader(io.BytesIO(data))
        return "\n".join((page.extract_text() or "") for page in reader.pages)
    if name.endswith(".docx"):
        import docx
        document = docx.Document(io.BytesIO(data))
        return "\n".join(p.text for p in document.paragraphs if p.text.strip())
    if name.endswith(".txt"):
        return _decode(data)
    raise ValueError("Unsupported scope document type. Use PDF, DOCX or TXT.")


def _decode(data: bytes) -> str:
    for enc in ("utf-8", "utf-8-sig", "latin-1"):
        try:
            return data.decode(enc)
        except UnicodeDecodeError:
            continue
    return data.decode("utf-8", errors="replace")


def parse_email_csv(data: bytes) -> list[dict]:
    """Returns a list of rows. Requires an 'email_body' column (matching the
    original data format); passes other columns through untouched."""
    text = _decode(data)
    reader = csv.DictReader(io.StringIO(text))
    if not reader.fieldnames:
        raise ValueError("CSV appears to be empty.")
    fields = [f.strip().lower() for f in reader.fieldnames]
    if "email_body" not in fields:
        raise ValueError(
            "CSV must contain an 'email_body' column. "
            f"Found columns: {', '.join(reader.fieldnames)}")
    body_key = reader.fieldnames[fields.index("email_body")]
    rows = []
    for raw in reader:
        body = (raw.get(body_key) or "").strip()
        if body:
            rows.append({"email_body": body,
                         **{k: v for k, v in raw.items() if k != body_key}})
    if not rows:
        raise ValueError("No non-empty email bodies found in the CSV.")
    return rows
