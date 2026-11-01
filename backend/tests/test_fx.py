from app.config import Settings
from app.infrastructure.providers.fx import FxService


def test_hkd_is_the_default_currency() -> None:
    assert Settings(_env_file=None).default_currency == "HKD"


def test_fx_snapshot_is_hkd_based_and_converts() -> None:
    fx = FxService()
    snapshot = fx.snapshot("HKD")
    assert snapshot["base"] == "HKD"
    assert snapshot["rates"]["HKD"] == 1.0
    assert snapshot["rates"]["USD"] > 0
    converted = fx.convert(1000, "HKD", "USD")
    assert 100 < converted < 200


def test_fx_conversion_round_trips() -> None:
    fx = FxService()
    usd = fx.convert(780, "HKD", "USD")
    back = fx.convert(usd, "USD", "HKD")
    assert abs(back - 780) < 0.001
