import io

from django.core.files.uploadedfile import SimpleUploadedFile
from docx import Document

from apps.candidates.parsing import extract_text, guess_fields, is_scanned
from apps.core.testing.pdf import make_pdf

CV_TEXT = """Truong My Hanh
Payments Backend Lead

Email: myhanh.truong@gmail.com
Phone: 0909 172 846

Summary
9 years of experience building payment systems.

Skills
Go, Kafka, PostgreSQL, Kubernetes

Experience
VNG - Payments Backend Lead
"""


def docx_bytes(text: str) -> bytes:
    document = Document()
    for line in text.splitlines():
        document.add_paragraph(line)
    buffer = io.BytesIO()
    document.save(buffer)
    return buffer.getvalue()


def test_text_is_extracted_from_a_pdf() -> None:
    upload = SimpleUploadedFile("cv.pdf", make_pdf(CV_TEXT))

    text = extract_text(upload)

    assert "Payments Backend Lead" in text
    assert "myhanh.truong@gmail.com" in text


def test_text_is_extracted_from_a_docx() -> None:
    upload = SimpleUploadedFile("cv.docx", docx_bytes(CV_TEXT))

    text = extract_text(upload)

    assert "Kafka" in text
    assert "Truong My Hanh" in text


def test_an_unreadable_file_yields_an_empty_string() -> None:
    upload = SimpleUploadedFile("cv.pdf", b"%PDF-1.4 but truncated")

    assert extract_text(upload) == ""


def test_an_unknown_extension_yields_an_empty_string() -> None:
    assert extract_text(SimpleUploadedFile("cv.txt", b"Truong My Hanh")) == ""


def test_the_file_pointer_is_rewound_for_the_caller() -> None:
    upload = SimpleUploadedFile("cv.pdf", make_pdf(CV_TEXT))

    extract_text(upload)

    assert upload.tell() == 0


def test_guess_fields_returns_exactly_the_contract_keys() -> None:
    fields = guess_fields(CV_TEXT)

    assert set(fields) == {
        "full_name",
        "current_title",
        "email",
        "phone",
        "skills",
        "years_experience",
    }


def test_guess_fields_reads_the_whole_header() -> None:
    fields = guess_fields(CV_TEXT)

    assert fields["full_name"] == "Truong My Hanh"
    assert fields["current_title"] == "Payments Backend Lead"
    assert fields["email"] == "myhanh.truong@gmail.com"
    assert fields["phone"] == "+84909172846"
    assert fields["years_experience"] == 9


def test_guess_fields_reads_the_skills_section() -> None:
    assert guess_fields(CV_TEXT)["skills"] == ["Go", "Kafka", "PostgreSQL", "Kubernetes"]


def test_skills_accept_bullets_and_pipes_and_are_capped() -> None:
    text = "Skills\n" + " | ".join(f"Skill{index}" for index in range(30))

    skills = guess_fields(text)["skills"]

    assert len(skills) == 20
    assert skills[0] == "Skill0"


def test_an_email_is_lowercased() -> None:
    assert guess_fields("MyHanh.Truong@Gmail.COM")["email"] == "myhanh.truong@gmail.com"


def test_an_international_phone_is_kept() -> None:
    assert guess_fields("Phone +84 28 3933 1234")["phone"] == "+842839331234"


def test_a_line_with_digits_is_never_taken_as_a_name() -> None:
    fields = guess_fields("0909 172 846\nTruong My Hanh\nBackend Lead")

    assert fields["full_name"] == "Truong My Hanh"
    assert fields["current_title"] == "Backend Lead"


def test_an_explicit_title_label_wins() -> None:
    text = "Truong My Hanh\nHo Chi Minh City\nCurrent title: Engineering Manager"

    assert guess_fields(text)["current_title"] == "Engineering Manager"


def test_empty_text_gives_empty_values_and_never_raises() -> None:
    assert guess_fields("") == {
        "full_name": "",
        "current_title": "",
        "email": "",
        "phone": "",
        "skills": [],
        "years_experience": None,
    }


def test_a_pdf_with_almost_no_text_counts_as_scanned() -> None:
    assert is_scanned("cv.pdf", "") is True
    assert is_scanned("CV.PDF", "  Page 1  ") is True
    assert is_scanned("cv.pdf", CV_TEXT) is False


def test_only_pdfs_can_be_scanned() -> None:
    assert is_scanned("cv.docx", "") is False
    assert is_scanned("cv.doc", "") is False
