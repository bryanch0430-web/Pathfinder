"""A seasonal reference does not stand for a live forecast that timed out.

When the live weather source times out at planning, the archive fills the days with a seasonal reference marked
stale, and the plan names a retryable weather gap. The section counted as held (every day had a forecast), so
"Refresh data" said nothing needed refreshing and a reopened trip asked again only when the reference was six hours
old, while the plan itself listed a retryable gap.
"""

from __future__ import annotations

from app.application.freshness import missing_sections
from app.config import Settings
from app.domain.models import DataGap, DataStatus, FailureKind
from tests.test_modify_targets import _build


def _plan(settings: Settings, *, status: DataStatus, retryable: bool):
    _, _, _, _, plan = _build(settings)
    for day in plan.days:
        assert day.weather is not None
        day.weather.status = status
    plan.data_quality.gaps = [
        gap for gap in plan.data_quality.gaps if gap.section != "weather"
    ] + (
        [DataGap(section="weather", kind=FailureKind.TIMEOUT, message="The forecast source timed out.", retryable=True)]
        if retryable
        else []
    )
    return plan


def test_days_with_only_a_seasonal_reference_and_a_retryable_gap_are_missing_their_forecast(settings: Settings) -> None:
    plan = _plan(settings, status=DataStatus.STALE, retryable=True)

    assert "weather" in missing_sections(plan)


def test_days_with_a_live_forecast_are_not_missing_it_even_with_a_retryable_gap_listed(settings: Settings) -> None:
    plan = _plan(settings, status=DataStatus.OK, retryable=True)

    assert "weather" not in missing_sections(plan)


def test_a_seasonal_reference_with_no_retryable_gap_is_not_asked_again(settings: Settings) -> None:
    plan = _plan(settings, status=DataStatus.STALE, retryable=False)

    assert "weather" not in missing_sections(plan)
