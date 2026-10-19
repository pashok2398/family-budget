# Ingestion and duplicates

Flow: `preview()` stores the document (SHA-256 addressed, `imports/`), parses rows into `import_rows`, and `assess()`
gives each row a status. The user resolves ambiguous/rejected rows (`resolve_row`), then `commit()` creates
transactions atomically. `refresh()` re-runs `assess()` on an open preview.

## Row statuses (`assess()` in `ingestion/service.py`)

- **new**: nothing similar in the ledger.
- **matched**: same `source_reference` (voucher / אסמכתא) and either same `base_fingerprint` or
  `same_financial_details()` (purchase date, amount, currency, merchant prefix). Charge date is deliberately ignored.
- **ambiguous**: same reference but different money/merchant, or no reference and an identical `base_fingerprint`
  exists. Needs an explicit `duplicate:<id>` / `new` / `reject`.
- **rejected**: parse error, repeated reference inside one statement, or conflicting installment history.

`base_fingerprint` hashes source, dates, amounts, merchant, description and installment fields.
For fetcher JSON (`whole_days`), the n-th identical movement of a day matches the n-th already imported.

## Charge dates and off-cycle charges

`posting_date` is the charge date (תאריך חיוב). Refunds, interest adjustments and forex orders on a card are often
**off-cycle**: the Isracard web XLSX lists them under "עסקאות בחיוב מחוץ למועד" with a real bank debit date
("חיוב בחשבון הבנק"), e.g. 02.11 while the card's bill is 10.11. The bank shows a separate "ישראכרט" movement that day.

The fetcher's `processedDate` is the bill date even for these rows, so it is wrong for off-cycle charges.
Therefore `rebill()` (on commit of a reference-matched row) updates the stored charge date **only from web/manual
statements, never from fetcher JSON**. Covered by `test_fetcher_bill_date_does_not_override_off_cycle_charge`.

## Card bills in reports

`reporting.charges()` groups card consumption by `posting_date`; the date most cards share is labelled
"חיוב ישראכרט החודשי", others "חיוב נפרד בכרטיס". A wrong charge date moves an amount between bills, so a gap against the
Isracard site usually means a row with the wrong `posting_date`. Compare against the web XLSX in `imports/`.
