from pathlib import Path

from pypdf import PdfReader

from apps.core.testing.pdf import make_pdf


def test_make_pdf_is_readable_and_has_one_page(tmp_path: Path) -> None:
    path = tmp_path / "cv.pdf"
    path.write_bytes(make_pdf("Phạm Minh Quân\nBackend Engineer · Tiki"))

    reader = PdfReader(str(path))

    assert len(reader.pages) == 1
    assert "Backend Engineer" in reader.pages[0].extract_text()


def test_make_pdf_starts_with_pdf_magic() -> None:
    assert make_pdf("hello").startswith(b"%PDF-")
