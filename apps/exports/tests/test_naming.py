from types import SimpleNamespace

from apps.exports.naming import entry_name, extension_of, sanitise, unique_entry_names


def test_entry_name_is_position_candidate_recruiter_extension() -> None:
    assert (
        entry_name(1, "Nguyễn Thị Hằng", "NorthStar Talent", "hang-cv.pdf")
        == "01 - Nguyễn Thị Hằng - NorthStar Talent.pdf"
    )


def test_position_is_zero_padded_to_two_digits() -> None:
    assert entry_name(7, "A", "B", "x.pdf").startswith("07 - ")
    assert entry_name(123, "A", "B", "x.pdf").startswith("123 - ")


def test_path_separators_and_control_characters_are_stripped() -> None:
    assert "/" not in entry_name(1, "a/b", "c\\d", "x.pdf")
    assert ".." not in entry_name(1, "../../etc/passwd", "r", "x.pdf")


def test_extension_falls_back_to_pdf() -> None:
    assert extension_of("cv.DOCX") == "docx"
    assert extension_of("cv") == "pdf"
    assert extension_of("") == "pdf"


def test_sanitise_keeps_vietnamese_letters() -> None:
    assert sanitise("Trần Minh Khoa") == "Trần Minh Khoa"


def test_unique_entry_names_keeps_positioned_rows_distinct() -> None:
    def stub(name: str) -> SimpleNamespace:
        return SimpleNamespace(
            candidate=SimpleNamespace(full_name=name),
            recruiter=SimpleNamespace(nickname="NorthStar Talent"),
            cv_filename="cv.pdf",
        )

    names = unique_entry_names([stub("Lê Văn A"), stub("Lê Văn A")])

    assert names == [
        "01 - Lê Văn A - NorthStar Talent.pdf",
        "02 - Lê Văn A - NorthStar Talent.pdf",
    ]
