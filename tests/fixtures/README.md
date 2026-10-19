# Synthetic, anonymized interchange fixtures

These are application-owned test contracts, **not** exports validated against Hapoalim or Isracard native formats. Names and amounts illustrate classification, but references and banking records are synthetic. Do not treat these as actual household transactions.

`hapoalim_canonical.csv`: bank salary, settlement and loan movements.
`isracard_canonical.csv`: two card purchases matching the synthetic settlement.
`mapped_hebrew.csv` + `hebrew_mapping.json`: explicit user-selected Hebrew headings and debit-positive convention.

XLSX and text-table PDF fixtures are generated locally by the parser tests, including a synthetic copy of the validated Isracard website export layout (`isracard_web_export` in `test_parsers.py`); no real financial information is included. To validate a new native layout, obtain a redacted sample with date, debit/credit sign, reference, currency and totals intact. Add a dedicated versioned adapter and a regression fixture before claiming support. A native PDF is never accepted merely because it mentions a bank name.
