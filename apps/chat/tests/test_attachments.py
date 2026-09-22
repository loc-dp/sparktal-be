import io
import zipfile

import pytest
from django.core.exceptions import ValidationError
from django.core.files.uploadedfile import SimpleUploadedFile
from PIL import Image

from apps.chat.attachments import MAX_ATTACHMENT_BYTES, validate_attachment
from apps.core.testing.pdf import make_pdf


def png_bytes() -> bytes:
    buffer = io.BytesIO()
    Image.new("RGB", (4, 4), "navy").save(buffer, format="PNG")
    return buffer.getvalue()


def jpg_bytes() -> bytes:
    buffer = io.BytesIO()
    Image.new("RGB", (4, 4), "navy").save(buffer, format="JPEG")
    return buffer.getvalue()


def docx_bytes() -> bytes:
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as archive:
        archive.writestr("word/document.xml", "<document/>")
    return buffer.getvalue()


@pytest.mark.parametrize(
    ("name", "payload"),
    [
        ("CV.pdf", make_pdf("CV")),
        ("brief.docx", docx_bytes()),
        ("shot.png", png_bytes()),
        ("photo.jpg", jpg_bytes()),
        ("photo.JPEG", jpg_bytes()),
    ],
    ids=["pdf", "docx", "png", "jpg", "jpeg-uppercase"],
)
def test_allowed_files_pass(name: str, payload: bytes) -> None:
    validate_attachment(SimpleUploadedFile(name, payload))


def test_an_unknown_extension_is_rejected() -> None:
    with pytest.raises(ValidationError, match="PDF, DOCX, PNG or JPG"):
        validate_attachment(SimpleUploadedFile("payload.exe", b"MZ"))


def test_a_file_over_ten_megabytes_is_rejected() -> None:
    upload = SimpleUploadedFile("big.pdf", make_pdf("x") + b"0" * MAX_ATTACHMENT_BYTES)
    with pytest.raises(ValidationError, match="10 MB"):
        validate_attachment(upload)


@pytest.mark.parametrize(
    ("name", "payload"),
    [
        ("fake.pdf", b"not pdf"),
        ("fake.docx", b"PK\x03\x04 not docx"),
        ("fake.png", b"\x89PNG\r\n\x1a\n truncated"),
        ("mismatch.png", jpg_bytes()),
    ],
)
def test_content_must_match_the_extension(name: str, payload: bytes) -> None:
    with pytest.raises(ValidationError, match="does not look like"):
        validate_attachment(SimpleUploadedFile(name, payload))


def test_the_file_pointer_is_rewound_for_the_caller() -> None:
    upload = SimpleUploadedFile("CV.pdf", make_pdf("CV"))
    validate_attachment(upload)
    assert upload.tell() == 0
