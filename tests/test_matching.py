from datetime import date
from types import SimpleNamespace
from family_budget.classification.matching import matches


def transaction(**values):
    return SimpleNamespace(
        **{
            "merchant": "PAYBOX",
            "amount_minor": -10000,
            "currency": "ILS",
            "transaction_date": date(2026, 10, 13),
            **values,
        }
    )


def test_specific_rule_never_promotes_other_dates_or_currencies():
    rule = {
        "merchant_in": ["PAYBOX"],
        "amount_minor": -10000,
        "transaction_date": "2026-10-13",
        "currency": "ILS",
        "direction": "debit",
    }
    assert matches(transaction(), rule)
    assert not matches(transaction(transaction_date=date(2026, 10, 14)), rule)
    assert not matches(transaction(currency="USD"), rule)
    assert not matches(transaction(amount_minor=10000), rule)
    assert not matches(transaction(merchant="PAYBOX OTHER PERSON"), rule)


def test_merchant_unicode_and_unknown_condition_fail_closed():
    assert matches(transaction(merchant="\u200fPAYBOX  "), {"merchant_in": ["paybox"]})
    assert not matches(transaction(), {"unrecognized_condition": "anything"})


def test_aggregate_date_window_is_bounded():
    rule = {"date_from": "2026-10-05", "date_to": "2026-10-17", "merchant_in": ["AliExpress"]}
    assert matches(transaction(merchant="AliExpress"), rule)
    assert not matches(transaction(merchant="AliExpress", transaction_date=date(2026, 10, 18)), rule)
