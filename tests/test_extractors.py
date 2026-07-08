"""Tests for scope/email extraction, including encoding fallbacks."""

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app import extractors

TEST_DATA = Path(__file__).resolve().parents[1] / "test_data"


def test_csv_utf8():
    data = "email_body\nCafé rooftop — add façade lighting?\n".encode("utf-8")
    rows = extractors.parse_email_csv(data)
    assert rows[0]["email_body"].startswith("Café")


def test_csv_latin1_fallback():
    data = "email_body\nCafé extension\n".encode("latin-1")
    rows = extractors.parse_email_csv(data)
    assert "Caf" in rows[0]["email_body"]


def test_csv_missing_column():
    with pytest.raises(ValueError, match="email_body"):
        extractors.parse_email_csv(b"subject,body\na,b\n")


def test_csv_skips_empty_bodies():
    data = b"email_body\n\n\nreal email\n"
    rows = extractors.parse_email_csv(data)
    assert len(rows) == 1


def test_csv_extra_columns_pass_through():
    data = b"sender,email_body\nalice@x.com,add a window\n"
    rows = extractors.parse_email_csv(data)
    assert rows[0]["sender"] == "alice@x.com"


def test_unsupported_scope_type():
    with pytest.raises(ValueError, match="Unsupported"):
        extractors.extract_scope_text("scope.xlsx", b"junk")


def test_txt_scope():
    text = extractors.extract_scope_text("scope.txt", "hello scope".encode())
    assert text == "hello scope"


@pytest.mark.skipif(not (TEST_DATA / "Scope Document.pdf").exists(),
                    reason="sample pdf not present")
def test_real_pdf_extracts_text():
    data = (TEST_DATA / "Scope Document.pdf").read_bytes()
    text = extractors.extract_scope_text("Scope Document.pdf", data)
    assert len(text) > 200


@pytest.mark.skipif(not (TEST_DATA / "Scope Document.docx").exists(),
                    reason="sample docx not present")
def test_real_docx_extracts_text():
    data = (TEST_DATA / "Scope Document.docx").read_bytes()
    text = extractors.extract_scope_text("Scope Document.docx", data)
    assert len(text) > 200


@pytest.mark.skipif(not (TEST_DATA / "cleaned_test_emails.csv").exists(),
                    reason="sample csv not present")
def test_real_email_csv():
    data = (TEST_DATA / "cleaned_test_emails.csv").read_bytes()
    rows = extractors.parse_email_csv(data)
    assert len(rows) == 25
