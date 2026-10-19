# Classification and review

`classify()` (`classification/engine.py`) runs once per new transaction at import commit. Manual transactions are
never reclassified.

## Order of decision

1. **Confirmed rules** matching the transaction (`classification/matching.py`), lowest `priority` wins.
   Equal-priority rules with different results are a conflict: nothing applied, row stays pending.
2. **History**: if no confirmed rule, past manual classifications of the same normalized merchant and direction.
   One consistent signature becomes a 70% suggestion (`history_suggestion`), pending.
3. **Unconfirmed rules** (shown as "הצעה בלבד"): applied as 65% suggestions, pending.
4. Otherwise `unclassified`, pending.

Pending rows get an open `ReviewItem` and appear in the סקירה tab, where checkboxes + "אישור N הצעות" confirm in bulk.

## Rule kinds

- Merchant rule: `merchant_in`, `direction`, `currency`. Created from the correction dialog checkbox (priority 15).
- Exception: also `amount_minor` and/or `transaction_date` / `date_from`..`date_to`. Matches one purchase only.
  Seeded exceptions use priority 10 so they beat merchant rules.

## Review-all preference

Setting `review_all_imports` (`periods.py`, Settings → חודש פיננסי) keeps every imported row pending even when a
confirmed rule classified it: the rule's category is filled in as the suggestion, confidence stays 100, provenance
`rule:<id>`. Exists because one merchant can sell for different purposes.

## Corrections

`classification/corrections.py::correct()` applies a reviewed decision with optimistic `version`, writes a
`ClassificationDecision` and `AuditEvent`, and optionally creates a merchant rule (not allowed for splits or projects).
