from apps.candidates.stages import STAGE_ORDER, current_stage


def test_stage_order_follows_the_pipeline() -> None:
    assert STAGE_ORDER == ["shortlisted", "new", "screen", "interview", "offer", "onboarded"]


def test_current_stage_is_the_most_advanced_non_rejected_stage() -> None:
    assert current_stage(["new", "interview", "screen"]) == "interview"
    assert current_stage(["shortlisted"]) == "shortlisted"
    assert current_stage(["offer", "onboarded"]) == "onboarded"


def test_rejected_never_counts_and_empty_is_none() -> None:
    assert current_stage(["rejected"]) is None
    assert current_stage([]) is None
    assert current_stage(["rejected", "screen"]) == "screen"
