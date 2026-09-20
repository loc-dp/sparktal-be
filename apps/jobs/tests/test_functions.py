import pytest

from apps.jobs.functions import guess_function


@pytest.mark.parametrize(
    ("title", "skills", "expected"),
    [
        ("Head of Finance", [], "finance"),
        ("Senior Backend Engineer", [], "engineering"),
        ("QA Engineer", [], "engineering"),
        ("Data Analyst", [], "data"),
        ("Product Designer", [], "design"),
        ("Compliance Manager", [], "legal"),
        ("Growth Marketing Lead", [], "marketing"),
        ("Country Manager", [], "sales"),
        ("Supply Chain Lead", [], "operations"),
        ("Chief of Staff", [], "other"),
        ("Chief of Staff", ["FP&A", "Treasury"], "finance"),
    ],
)
def test_guess_function(title: str, skills: list[str], expected: str) -> None:
    assert guess_function(title, skills) == expected
