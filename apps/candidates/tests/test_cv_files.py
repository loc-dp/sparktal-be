import io
import zipfile

import pytest
from django.core.exceptions import ValidationError
from django.core.files.uploadedfile import SimpleUploadedFile

from apps.candidates.cv_files import MAX_CV_BYTES, validate_cv
from apps.core.testing.pdf import make_pdf

DOC_MAGIC = b"\xd0\xcf\x11\xe0\xa1\xb1\x1a\xe1"


def docx_bytes() -> bytes:
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as archive:
        archive.writestr("[Content_Types].xml", "<Types/>")
        archive.writestr("word/document.xml", "<document/>")
    return buffer.getvalue()


@pytest.mark.parametrize(
    ("name", "payload"),
    [
        ("CV_Truong_My_Hanh.pdf", make_pdf("CV")),
        ("cv.PDF", make_pdf("CV")),
        ("cv.docx", docx_bytes()),
        ("cv.doc", DOC_MAGIC + b"legacy word blob"),
    ],
    # A .docx is a zip, and a zip stores the build time, so its bytes differ per
    # process. Fixed ids keep xdist workers collecting the same test names.
    ids=("pdf", "pdf-uppercase", "docx", "doc"),
)
def test_allowed_cv_formats_pass(name: str, payload: bytes) -> None:
    validate_cv(SimpleUploadedFile(name, payload))


def test_an_image_is_rejected() -> None:
    with pytest.raises(ValidationError, match="PDF, DOCX or DOC"):
        validate_cv(SimpleUploadedFile("cv.png", b"\x89PNG\r\n\x1a\n"))


def test_a_file_over_ten_megabytes_is_rejected() -> None:
    with pytest.raises(ValidationError, match="10 MB"):
        validate_cv(SimpleUploadedFile("big.pdf", make_pdf("x") + b"0" * MAX_CV_BYTES))


@pytest.mark.parametrize(
    ("name", "payload"),
    [
        ("fake.pdf", b"MZ not a pdf"),
        ("fake.docx", b"PK\x03\x04 but not a docx"),
        ("fake.doc", b"plain text pretending"),
    ],
)
def test_content_must_match_the_extension(name: str, payload: bytes) -> None:
    with pytest.raises(ValidationError, match="does not look like"):
        validate_cv(SimpleUploadedFile(name, payload))


def test_the_file_pointer_is_rewound_for_the_caller() -> None:
    upload = SimpleUploadedFile("cv.doc", DOC_MAGIC + b"body")

    validate_cv(upload)

    assert upload.tell() == 0
