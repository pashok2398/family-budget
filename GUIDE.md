# Family Budget — user guide

This guide starts with the app already running. Instructions are in English, with the Hebrew labels you will see on screen.

**Start here:** add your account and card in **הגדרות**, import one month's statements with **ייבוא דוח**, then complete **סקירה שבועית**. Review the dashboard after checking classifications and credit-card settlements.

For installation, server commands and API details, see [README.md](README.md). For the implementation's recorded test coverage, see [verification notes](docs/VERIFICATION.md).

## 1. Your first session

1. Open **הגדרות** → **חשבונות וכרטיסים**. Add an account alias such as `חשבון משותף`. You do not need to enter a full bank account number.
2. Add each credit card under the account that pays its bill. Use recognizable aliases such as `ישראכרט משפחתי`.
3. Select the month you want to work on using the month selector at the top. Start with a month for which you have both bank and card statements.
4. Open **תקציב וקרנות**. Check the planned income and category allocations, then save any changes for that month.
5. Click **ייבוא דוח**. Import one statement, inspect its preview and approve it. Repeat for the other sources.
6. Open **סקירה שבועית**. Correct or confirm pending classifications, then inspect reconciliation proposals.
7. Return to **סקירה כללית**. Clear filters to see the household-wide picture.

The application starts with an example provisional ₪32,000 budget, two salary sources, maintenance/vacation funds, mortgage/loan records and classification rules. **It does not create actual transactions from those plans.** Zero income or expenses before importing data is expected. Fund balances also start at zero.

## 2. Where everything lives

| Screen | Use it for |
|---|---|
| **סקירה כללית** — Overview | Monthly income, household consumption, debt payments, savings contributions, bank cash flow, category spending, trends and installment commitments. |
| **תנועות** — Transactions | Find transactions, filter them, correct classifications, split purchases and inspect correction history. |
| **סקירה שבועית** — Weekly Review | Resolve pending classifications, approve matches, inspect expected reimbursements and see import history. |
| **תקציב וקרנות** — Budget and Funds | Edit the selected month's plan, record fund movements, compare project spending with budgets and inspect loan balances. |
| **הגדרות** — Settings | Add accounts/cards/projects, create or rename categories, edit salary expectations and loan details, record principal/interest breakdowns, add recurring reminders and disable rules. |

Overview and Transactions offer filters for account, card, category, household/pub/project/investment scope, project and review status. Use **איפוס** to clear them.

Some overview panels remain household-wide: the trends chart explicitly covers all accounts, while fund balances, project totals and loan balances are not filtered transaction subtotals. Clear filters before evaluating the overall budget.

## 3. Importing your first statement

### What files can I use?

| Format | Current behavior |
|---|---|
| CSV | UTF-8, including UTF-8 with BOM. Uses the documented columns or an explicit column mapping. |
| XLSX | Reads a simple table from the first worksheet. Uses the same mapping approach. |
| PDF | Requires a readable table and an explicit table mapping. Native bank layouts have not yet been validated against real samples. Scans and encrypted PDFs are unsupported. |

The limit is **20 MiB and 20,000 transaction rows per file**. Selecting Hapoalim or Isracard identifies the source; it does not mean every statement from that institution can be read automatically. A CSV/XLSX export with verified columns is the most practical starting point for the current importer.

Bank reports must use the bank account source. Isracard reports must select a card linked to its settlement account. Keep using the same account/card records for later imports; creating a new source for every statement prevents reliable historical deduplication.

### Prepare the columns

The application needs these five fields:

| Field | Meaning |
|---|---|
| `transaction_date` | Date of the purchase or transaction. |
| `posting_date` | Date it was posted or billed; used for bank cash flow and settlement matching. |
| `merchant` | Merchant or counterparty name. |
| `amount` | Signed amount: expenses negative, money received positive. |
| `currency` | Three-letter code such as `ILS`. |

Useful optional fields are `reference` (a stable source transaction identifier), `description`, `kind` and `allocation_date` (the date used for the budget).

This is an **illustrative format**, not actual household data or a bank's native layout:

```csv
transaction_date,posting_date,merchant,amount,currency,reference,kind
2026-10-05,2026-10-10,Example grocery store,-100.25,ILS,example-purchase-001,purchase
```

Use dates with four-digit years, such as `2026-10-05` or `05/10/2026`. Keep amounts to two decimal places. Preserve real source references where available; leave them blank if unavailable instead of inventing banking identifiers. Do not guess missing dates or amounts.

If you transform a bank export into this format, keep the original export separately in your private data directory. The application preserves the exact file you upload, which may be the transformed file rather than the original bank document.

### Map existing headings

In the upload dialog, expand **מיפוי עמודות, סימן סכום וסך דוח**. Enter JSON mapping the application's fields to your file's actual headings. For example, **only if your file has these headings**:

```json
{
  "columns": {
    "transaction_date": "תאריך עסקה",
    "posting_date": "תאריך חיוב",
    "merchant": "שם עסק",
    "amount": "סכום",
    "currency": "מטבע",
    "reference": "אסמכתא"
  },
  "sign": "signed",
  "expected_total": "-100.25"
}
```

Use `signed` when expenses are already negative. Use `debit_positive` only when expenses are positive and credits are negative: it reverses **every** amount's sign. It cannot distinguish separate debit/credit columns or a file where both expenses and credits are positive. Such exports need an explicit conversion into one signed amount column first.

`expected_total` is optional. It means the signed sum of the imported transactions after sign normalization, **not the closing bank balance**. Enter it as a quoted decimal string. Do not copy a statement balance or unrelated subtotal into this field. A mismatch blocks commitment; without it, totals remain unverified. Never combine different currencies into a single total.

### Preview, decide, commit

Click **תצוגה מקדימה** and compare the extracted merchants, dates and amounts with the source.

| Preview state | What to do |
|---|---|
| `new` / **חדשות** | Check the details. These rows will become transactions after commitment. |
| `matched` / **התאמות** | Already identified as imported. These rows will not create another transaction. |
| `ambiguous` / **לא חד־משמעיות** | Choose a specific historical duplicate, a genuinely separate new transaction, or explicit rejection. |
| `rejected` / **נדחו** | Inspect the error. Fix the source/mapping or explicitly acknowledge rejection. An excluded real transaction will be missing from reports. |

Two purchases with the same merchant, date and amount can both be legitimate. Compare the source before deciding. Conflicting stable references or installment identities cannot be forced into new transactions.

After every ambiguous/rejected row has a decision and any total mismatch is resolved, click **אישור וייבוא**. Until then, the upload is a preview and does not affect spending totals.

To resume a preview, upload the same file again under the same source. Its decisions are retained when the options are unchanged. To fix its mapping or expected total, close the preview, reopen **ייבוא דוח**, and submit the same file with corrected options; this reparses an **uncommitted** batch and replaces its row decisions. Use **בדיקה חוזרת** when another import has changed historical matches. **הורדת המקור** downloads the uploaded source.

Re-uploading an already committed document to the same source is a no-op. There is currently no browser action to delete or roll back a committed import, so inspect the preview carefully.

## 4. A short weekly review

1. Upload the new bank and card reports and commit their checked previews.
2. Open **סקירה שבועית**. By default, it shows pending classifications from all months; uncheck **הצגת ממתינים מכל החודשים** to restrict it to the selected month.
3. Read each suggestion. Select the checkboxes for correct suggestions and use **אישור … הצעות** to approve them together.
4. For an incorrect or incomplete suggestion, click **בדיקה**, make the correction and choose **אישור ושמירה**.
5. In **התאמות בלי ספירה כפולה**, inspect and approve valid settlement, transfer and refund proposals.
6. Inspect **החזרים ממתינים**, then check the overview and category budgets with filters cleared.

Known confirmed rules can classify transactions without placing them in the pending queue. On your first few imports, also inspect **תנועות** with review status **מאושר** to check how the seeded rules apply to your real statement descriptions.

### Correcting a transaction

The **בדיקה וסיווג תנועה** dialog separates several decisions:

| Field | Example |
|---|---|
| **סוג תנועה** — Purpose | Purchase, salary, credit-card settlement, transfer, refund, loan payment or savings transfer. |
| **קטגוריה** — Category | Groceries, hobbies, household shopping or maintenance. |
| **שיוך** — Scope | Household, community pub, project or investment. |
| **פרויקט** — Project | Select an existing project when using project scope. |
| **מקור הכנסה** — Income source | Household salary or partner salary. |
| **תאריך הקצאה לתקציב** — Allocation date | Which month should carry the consumption. This does not change the bank posting date. |

Enter a reason in **סיבת התיקון**. **היסטוריית שינויים** shows the previous decisions. The dialog also displays the transaction ID, which you can copy for manual matches, fund links and loan-payment breakdowns.

The option **להחיל כלל מדויק על בית העסק הזה גם בעתיד** creates an explicit merchant rule. Leave it unchecked for an exception, a mixed-use merchant, or a payment service such as PAYBOX. A normal correction still contributes to future suggestions, which require approval.

Examples:

- Ordinary IKEA purchases: household scope and household shopping.
- QTOOLS purchases for personal use: household scope and hobbies/tools.
- Community pub purchases: pub scope, even if paid from your own account.
- Planned garden work: project scope and the garden project, rather than routine maintenance.
- Ordinary repairs: household scope and the maintenance category, with a separate fund withdrawal if using that fund.

Some seeded exceptions require an exact date and amount. Aggregate information, such as a reported total across multiple purchases, remains a suggestion rather than proof that every matching merchant entry belongs to it.

### Splitting one purchase

Use **פיצול הוצאה** → **הוספת חלק**. Each part has an amount, category, scope and optional project.

For a `-300.00` purchase containing ₪220 of household goods and ₪80 for the pub, enter `-220.00` under household scope and `-80.00` under pub scope. The parts must have the same sign and sum exactly to the original amount. Do not import another transaction to represent the split.

## 5. Settlements, transfers and refunds

### Credit-card bills

Import both the detailed card purchases and the bank statement. The purchases count toward consumption; the bank payment is **סילוק כרטיס אשראי**, not another purchase.

For example, ₪600 of card purchases plus a ₪600 bank settlement should produce ₪600 of consumption and ₪600 of bank cash outflow—not ₪1,200 of expenses.

Confirm the classifications first. The current settlement matcher requires one bank debit and a set of charges on one card, with the same settlement account, currency and posting/billing date, and an exactly equal total. If a proposal is missing, check missing charges, refunds, billing dates and the bank payment's purpose. Do not change a true source date just to force a match.

### Transfers between your own accounts

Import both sides under their respective accounts. Verify the equal opposite amounts and approve the transfer match. The current matcher requires the same currency and dates within seven days. These movements are not consumption. If both sides post in the same month, their combined household bank cash flow cancels out; an individual account still shows its own movement.

Classify a real outgoing savings contribution as **העברה לחיסכון**. A fund allocation in the budgeting screen is a separate planning entry, not a bank transfer.

### Purchase refund versus reimbursement

| Situation | How to handle it |
|---|---|
| A shop refunds a household purchase | Confirm the credit as **החזר רכישה**, then link it to the original expense. Ordinary refunds offset consumption in their allocation month. |
| You paid an expense for someone else | On the original purchase, check **הוצאה עבור אחרים — צפוי החזר מלא**. Use the correct scope, such as pub for committee purchases. |
| The other party repays you | Confirm the credit as **החזר הוצאה** and approve the reimbursement match. |

Confirmed purchases marked for reimbursement are tracked separately from household consumption. Expected repayments appear as receivables, **not money already received**. The bank cash flow still shows the actual debit and eventual credit.

The current matcher supports exact, full, one-expense/one-credit refunds and reimbursements. Partial repayments, one repayment covering multiple expenses, and split-purchase refunds are not supported by these matching actions. Keep unresolved cases visible rather than inventing balancing transactions or marking them fully repaid.

Use **ביטול התאמה** to undo a match. A settled card purchase can also have a refund relationship; the two matches can be undone independently. To edit a matched transaction's classification, first undo its active matches. Transactions linked to fund or loan ledgers also have editing restrictions.

## 6. Reading the dashboard correctly

| Figure | What it means |
|---|---|
| **הכנסות החודש** | Confirmed household income, by posting date. Planned salary is not counted as received income. |
| **הוצאות משק הבית** | Confirmed household consumption by allocation date. Excludes settlements, transfers, debt service, pub/projects and reimbursable outlays. |
| **משכנתא והלוואות** | Mortgage and loan payments, shown separately from consumption. |
| **הפקדות לחיסכון** | Confirmed movements classified as savings/investment transfers within household scope. |
| **תזרים מזומנים נטו** | Imported bank inflows minus outflows by posting date. Includes bank settlements; excludes card-detail rows. Pending bank classifications still affect cash flow. |
| **נותר בתקציב השוטף** | Ordinary planned allocations minus confirmed ordinary spending and debt service. Planned fund/savings allocations and fund-category spending are handled separately. |
| **קרנות למחר** | Accumulated earmarked balances from explicitly recorded fund movements, through the selected month. |
| **מחוץ לשגרה** | Consumption outside household scope, with expected reimbursements displayed separately. |

The full initial plan is ₪32,000. Of that, ₪1,200 is for vacation, ₪1,000 for maintenance and ₪1,500 for savings. Therefore, before any spending, the ordinary remaining-budget figure is **₪28,300**, not ₪32,000.

Neither remaining budget nor net monthly cash flow is a verified bank balance. Pending classifications can make confirmed consumption look low. The dashboard excludes non-ILS movements from its shekel totals and shows an exclusion count; there is no automatic currency conversion.

An October card purchase paid through the bank in November can appear in October consumption and November cash flow. The Transactions month filter uses the allocation date. Regular purchases default to their transaction date; imported installments default to posting date unless an allocation date is supplied.

## 7. Budgets, funds and projects

### Monthly budget

Open **תקציב וקרנות**, select the month, edit the planned income and allocations, then click **שמירת תקציב באישור מפורש**. Keep the provisional-income checkbox selected until you have a reliable plan.

Changes apply to the selected month. A month without its own saved plan uses the initial template; editing October does not automatically copy that plan into November. Salary-source expectations in Settings are also separate from the overall monthly income plan—update both when appropriate.

Suggestions need at least three preceding months with reviewed ILS records. You must still check that all statements were imported. **הוספה לטיוטת התקציב** changes the draft only; save explicitly to adopt it.

### Sinking funds

Use **תקציב וקרנות** → **קרנות מצטברות** → **תנועות קרן**.

- Enter a positive amount to earmark money for the fund.
- Enter a negative amount to spend or release earmarked money.
- Add a date and description. A withdrawal cannot use funds allocated only on a later date.
- Optionally link the actual purchase using its transaction ID. Its signed ILS amount and household category must match the fund withdrawal.

Example: allocate `1000.00` to maintenance in September and another `1000.00` in October. Record a `-975.00` withdrawal for a repair. The remaining earmarked balance is ₪1,025. The imported repair is still one ₪975 purchase; the fund withdrawal does not create another expense.

Monthly targets **do not automatically add money to funds**, move bank money, or reduce a fund when a purchase is categorized. Record the intended fund movements explicitly. Treat a balance as an earmarking record, not independent proof that cash is available.

### Projects

Create a project and its separate budget in **הגדרות** before assigning project spending. In a transaction's review dialog, choose scope **פרויקט** and select the project. The Budget screen shows cumulative project spending and the remaining project plan through the selected month.

Use the maintenance fund for ordinary repairs and small household upgrades. Use a project for a separately planned job such as the garden project. Pub spending has its own scope and should not be assigned to household spending just because it used the same card.

## 8. Loans, recurring expenses and installments

**Loans:** In **הגדרות** → **פרטי הלוואות ומשכנתא**, edit the planned monthly payment and, if verified, the opening principal. The displayed remaining principal subtracts linked principal payments from that opening figure. Use a consistent starting point; do not enter today's remaining balance as the opening amount and then subtract old payments again.

Expand **פירוט תשלומים — קרן וריבית**, paste the ID of a confirmed mortgage/loan-payment transaction, and enter its principal and interest portions. Together they must equal the debit. This links a breakdown to an existing transaction; it does not add a second payment. Use **תיקון הפירוט** to correct an existing breakdown.

**Recurring expenses:** In Settings, add a name, category, expected amount and day of month (1–28). These are saved planning reminders; they do not create transactions, pay bills or send notifications.

**Installments:** Upcoming commitments require explicit installment information in the imported file. Provide all of `purchase_id`, `installment_number`, `installment_count`, `original_purchase_amount` and `remaining_commitment`. The ordinary `amount` field must contain the monthly charge, not the whole purchase price. Original total is positive; remaining commitment is nonnegative and must be zero for the final installment. The dashboard uses the latest known installment record per purchase. There is no browser installment-plan editor and no automatic generation of future charges.

## 9. Capabilities and current limits

| Capability | Availability |
|---|---|
| Import, preview, duplicate review and source download | Browser; CSV/XLSX mapping and restricted mapped PDF tables. |
| Classification, category/scope corrections, splits and history | Browser; exact user rules and reviewable suggestions. |
| Settlements, own transfers, full refunds/reimbursements | Browser; explicit approval and reversible matches. |
| Monthly plans, existing fund movements, project creation/tracking | Browser. |
| Existing salary-source and loan editing, payment breakdowns, recurring entries | Browser. |
| Creating additional funds, income sources or loans; editing existing project budgets | API routes documented in the README; not all have browser forms. |
| Encrypted backup and restore | CLI, not a browser button. |
| Automatic bank login or statement download | Not implemented. Upload exports yourself. |
| Validated native Hapoalim/Isracard layouts | Await real redacted samples. Existing source adapters require explicit supported layouts. |
| Scanned-PDF OCR, automatic exchange rates, partial/grouped refund matching | Not implemented. |
| Direct editing of imported monetary amounts/dates, committed-import deletion | No browser workflow. Allocation date and classification are editable; source financial facts are preserved. |
| Multi-user accounts, remote-access authentication, AI categorization | Not implemented/enabled. This is a trusted local workspace. |

## 10. Backup and restore

With Docker, the scheduled backup (`sudo make install-backup`, setup in `docs/setup.md`) runs every night at 03:30, encrypted to your SSH public key so only your private key can open it, and keeps 30 days (`make backup-status`, `make backup-now`). With `/etc/family-budget-backup.env` set up, each archive is also encrypted to your SSH key and uploaded to Google Drive (see `docs/operations.md`). The manual commands below are for a one-off backup.

For a local Python installation, run these from the project directory. They use the same data-directory environment setting as the app:

```bash
mkdir -p "$HOME/family-budget-backups"
.venv/bin/family-budget backup "$HOME/family-budget-backups/household-2026-10-18.fbb"
```

Choose a new filename each time. The command prompts for a passphrase of at least 12 characters and confirmation. Keep the passphrase separately; losing it means the encrypted backup cannot be recovered. Keep a copy of the encrypted archive on independent storage.

To test restoration into a new directory:

```bash
.venv/bin/family-budget restore \
  "$HOME/family-budget-backups/household-2026-10-18.fbb" \
  "$HOME/family-budget-restore-check"

.venv/bin/family-budget --data-dir "$HOME/family-budget-restore-check" serve --port 8001
```

The destination must be absent or empty. The second command opens the restored copy at `http://127.0.0.1:8001`, leaving the original instance separate. Stop it with Ctrl+C after checking it. Keep all data, statements and backups outside the source checkout.

If using Docker Compose instead:

```bash
docker compose exec budget family-budget backup /data/household-2026-10-18.fbb
```

That archive is in your configured host data directory. Copy it to independent storage. See the [README backup/restore procedure](README.md#encrypted-backup-and-restore) before switching the running service to a restored directory.

The live database is local but not encrypted by the application; backup encryption applies to the backup archive. The app sends no financial data to analytics or an AI service.

## 11. Troubleshooting

| What you see | What to check |
|---|---|
| No account/card available in the import dialog | Add it in Settings, select the card's settlement account, then reopen the dialog. |
| “Unrecognized columns” | Map the required headings exactly. Separate debit/credit columns need conversion to one signed amount field. |
| PDF rejected | Use a readable CSV/XLSX export or obtain a verified table mapping. Scans and encrypted PDFs are unsupported. |
| Total mismatch | Check signs, included rows, currencies and whether the supplied number is a transaction total rather than a balance. Correct the uncommitted preview options and re-upload. |
| Commit blocked on ambiguous/rejected rows | Give every such row an explicit decision. Do not reject a real expense merely to make the button available. |
| History changed since preview | Click **בדיקה חוזרת**, review the new overlaps, then commit. |
| No settlement proposal | Verify classifications, account/card links, billing dates, currency and the complete charge/refund total. |
| Expense missing from the dashboard | Check the month/allocation date, filters, pending status, currency, purpose, scope and reimbursement flag. |
| Can't edit a transaction | Reload after a stale-version error; undo active matches before reclassification. Fund/loan ledger links also restrict edits. |
| Fund shows zero despite a monthly target | Add actual earmarking movements; the target alone does not fund it. |
| No installment commitments shown | Check that all five installment fields were supplied. The app does not infer them from merchant names. |
| No budget suggestions | Three preceding reviewed months are required; there may also be no suggested increases. |

For a comfortable first run, finish one month's **account setup → import preview → classification → reconciliation → dashboard check** before importing a large archive of older statements.
