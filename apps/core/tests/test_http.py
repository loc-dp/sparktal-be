from apps.core.http import content_disposition


def test_ascii_names_keep_a_plain_filename() -> None:
    header = content_disposition("cv.pdf")

    assert header == "attachment; filename=\"cv.pdf\"; filename*=UTF-8''cv.pdf"


def test_vietnamese_names_get_an_ascii_fallback_and_rfc5987() -> None:
    header = content_disposition("Nguyễn Thị Hằng.pdf")

    assert 'filename="Nguyen Thi Hang.pdf"' in header
    assert "filename*=UTF-8''Nguy%E1%BB%85n%20Th%E1%BB%8B%20H%E1%BA%B1ng.pdf" in header


def test_quotes_and_paths_cannot_escape_the_header() -> None:
    header = content_disposition('../../etc/pa"sswd.pdf')

    assert ".." not in header.split("filename=")[1].split(";")[0]
    assert '\\"' not in header


def test_inline_disposition() -> None:
    assert content_disposition("cv.pdf", inline=True).startswith("inline;")
