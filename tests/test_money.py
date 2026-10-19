from decimal import Decimal
import pytest
from family_budget.normalization.money import minor, money, normalized, parse_date


def test_decimal_exactness():
    assert minor("364.87") + minor("207.72") == 57259
    assert money(57259) == "572.59"
    assert minor("1,300.00") == 130000
    assert minor("(1,300.01)") == -130001
    assert minor(Decimal("0.01")) == 1


@pytest.mark.parametrize("value", [0.1, True, "NaN", "Infinity", "1.001", "1,23", "1,000,00", "", "1e99"])
def test_reject_unreliable_money(value):
    with pytest.raises(ValueError):
        minor(value)


def test_hebrew_and_dates():
    assert normalized("\u200fמכולת   לדוגמה") == "מכולת לדוגמה"
    assert parse_date("18/10/2026").isoformat() == "2026-10-18"
    with pytest.raises(ValueError):
        parse_date("10/18/26")
