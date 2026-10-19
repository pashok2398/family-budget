import json
import io
import pytest

pytest.importorskip("pandas")
from family_budget.parsers.hapoalim import HapoalimAdapter
from family_budget.parsers.isracard import IsracardAdapter
from family_budget.parsers.base import ParserError


@pytest.mark.parametrize(
    "adapter,name", [(HapoalimAdapter, "hapoalim_canonical.csv"), (IsracardAdapter, "isracard_canonical.csv")]
)
def test_contracts(adapter, name, fixture_dir):
    p = fixture_dir / name
    rows = adapter().parse(p.read_bytes(), ".csv", p, {})
    assert rows and not any(r.error for r in rows)
    assert rows[0].row_number == 2


def test_explicit_hebrew_mapping(fixture_dir):
    p = fixture_dir / "mapped_hebrew.csv"
    options = json.loads((fixture_dir / "hebrew_mapping.json").read_text())
    row = IsracardAdapter().parse(p.read_bytes(), ".csv", p, options)[0]
    assert row.data["amount_minor"] == -36487
    assert row.data["posting_date"] == "2026-11-02"
    assert row.data["merchant"] == "מכולת לדוגמה"


def test_unknown_pdf_and_unknown_columns_fail(tmp_path):
    with pytest.raises(ParserError, match="not validated"):
        HapoalimAdapter().parse(b"%PDF-1.7\n", ".pdf", tmp_path / "x.pdf", {})
    with pytest.raises(ParserError, match="unrecognized columns"):
        HapoalimAdapter().parse(b"unknown,amount\nx,12\n", ".csv", tmp_path / "x.csv", {})


def test_xlsx_generated_fixture(tmp_path):
    import openpyxl

    book = openpyxl.Workbook()
    sheet = book.active
    sheet.append(["transaction_date", "posting_date", "merchant", "amount", "currency"])
    sheet.append(["2026-10-01", "2026-10-10", "Synthetic store", "-10.25", "ILS"])
    buffer = io.BytesIO()
    book.save(buffer)
    rows = IsracardAdapter().parse(buffer.getvalue(), ".xlsx", tmp_path / "example.xlsx", {})
    assert rows[0].data["amount_minor"] == -1025


def test_pdf_table_generated_fixture(tmp_path):
    pymupdf = pytest.importorskip("pymupdf")
    pytest.importorskip("pdfplumber")
    path = tmp_path / "table.pdf"
    doc = pymupdf.open()
    page = doc.new_page(width=800, height=300)
    columns = [20, 160, 300, 490, 620, 770]
    y = [30, 70, 110]
    for x in columns:
        page.draw_line((x, y[0]), (x, y[-1]))
    for n in y:
        page.draw_line((columns[0], n), (columns[-1], n))
    headers = ["transaction_date", "posting_date", "merchant", "amount", "currency"]
    values = ["2026-10-01", "2026-10-10", "Synthetic store", "-10.25", "ILS"]
    for row, top in [(headers, 55), (values, 95)]:
        for text, x in zip(row, columns):
            page.insert_text((x + 5, top), text, fontsize=10)
    doc.save(path)
    doc.close()
    rows = HapoalimAdapter().parse(
        path.read_bytes(),
        ".pdf",
        path,
        {"layout": "user_mapped_table_v1", "columns": dict(zip(headers, headers))},
    )
    assert rows[0].data["amount_minor"] == -1025
    assert rows[0].page == 1


def test_duplicate_headers_fail_before_pandas_mangles_them(tmp_path):
    content = b"transaction_date,posting_date,merchant,amount,amount,currency\n2026-10-01,2026-10-10,Example,-10,-20,ILS\n"
    with pytest.raises(ParserError, match="Duplicate"):
        HapoalimAdapter().parse(content, ".csv", tmp_path / "duplicate.csv", {})


def isracard_web_export(*, open_statement=False, header_total="₪ 150.50", footer=True, extra_row=None):
    """Synthetic workbook copying the isracard_web_xlsx_v1 layout; merchants and amounts are invented."""
    import openpyxl

    book = openpyxl.Workbook()
    sheet = book.active
    sheet.title = "פירוט עסקאות"
    blank = [None] * 9
    rows = [
        blank,
        ["פירוט עסקאות", None, "אוקטובר 2026"],
        blank,
        blank,
        ["‫מסטרקארד‬ - 1234", None, None, None, None, None, None, header_total],
        ["על שם בעל הכרטיס", None, None, None, None, None, None, "לחיוב ב-10.10"],
        blank,
        ["מסגרת:", "₪ 10,000", "נותר לניצול:", "₪ 9,000.00"]
        + ([None, None, None, "‫*החיוב אינו סופי‬"] if open_statement else []),
        blank,
        ["עסקאות שטרם נקלטו"],
        ["תאריך רכישה", "שם בית עסק", "סכום עסקה", "מטבע עסקה"],
        ["05.10.26", "Pending shop", 9.99, "₪"],
        blank,
        ["עסקאות למועד חיוב"],
        [
            "תאריך רכישה",
            "שם בית עסק",
            "סכום עסקה",
            "מטבע עסקה",
            "סכום חיוב",
            "מטבע חיוב",
            "מס' שובר",
            "פירוט נוסף",
        ],
        ["01.09.26", "Synthetic grocer", 120.25, "₪", 120.25, "₪", "900000001", ""],
        ["03.09.26", "Synthetic furniture", 600, "₪", 50.25, "₪", "900000002", "תשלום 2 מתוך 12"],
        ["04.09.26", "Synthetic return", -20, "₪", -20, "₪", "900000003", ""],
        [None, 'סה"כ', None, None, 150.50, "₪"],
        blank,
        ["עסקאות בחיוב מחוץ למועד"],
        [
            "תאריך רכישה",
            "שם בית עסק",
            "סכום עסקה",
            "מטבע עסקה",
            "סכום חיוב",
            "מטבע חיוב",
            "מס' שובר",
            "פירוט נוסף",
            "חיוב בחשבון הבנק",
        ],
        [
            "20.09.26",
            "SYNTHETIC CAFE BUCHAREST",
            63,
            "L",
            47.11,
            "₪",
            "900000004",
            'בוצע בחו"ל\nהנחה ₪1.22 ',
            "02.10.26",
        ],
        ["21.09.26", "SYNTHETIC SAAS", 20, "$", 61.44, "₪", "900000005", 'אתר חו"ל\nהוראת קבע', "02.10.26"],
    ]
    if extra_row:
        rows.append(extra_row)
    if footer:
        rows += [
            blank,
            ["עסקאות בחיוב מיידי -  עסקאות שבצעת החודש ומחוייבות לאחר ביצוען"],
            blank,
            ["תנאים משפטיים"],
        ]
    for row in rows:
        sheet.append(row)
    buffer = io.BytesIO()
    book.save(buffer)
    return buffer.getvalue()


def test_isracard_web_export_native(tmp_path):
    adapter = IsracardAdapter(card_last_four="1234")
    rows = adapter.parse(isracard_web_export(), ".xlsx", tmp_path / "x.xlsx", {})
    assert adapter.version == "isracard_web_xlsx_v1"
    assert not any(r.error for r in rows)
    assert [r.data["merchant"] for r in rows] == [
        "Synthetic grocer",
        "Synthetic furniture",
        "Synthetic return",
        "SYNTHETIC CAFE BUCHAREST",
        "SYNTHETIC SAAS",
    ]  # pending section skipped, footer ignored
    grocer, furniture, refund, cafe, saas = (r.data for r in rows)
    assert grocer["amount_minor"] == -12025 and grocer["posting_date"] == "2026-10-10"
    assert grocer["transaction_date"] == "2026-09-01" and grocer["source_reference"] == "900000001"
    assert furniture["amount_minor"] == -5025 and "תשלום 2 מתוך 12" in furniture["raw_description"]
    assert furniture["allocation_date"] == "2026-10-10" and grocer["allocation_date"] == "2026-09-01"
    assert refund["amount_minor"] == 2000 and refund["kind"] == "refund"
    assert cafe["currency"] == "ILS" and cafe["amount_minor"] == -4711
    assert cafe["original_currency"] == "RON" and cafe["original_amount_minor"] == -6300
    assert cafe["posting_date"] == "2026-10-02"
    assert saas["original_currency"] == "USD"
    assert adapter.native == {
        "layout": "isracard_web_xlsx_v1",
        "card_name": "מסטרקארד",
        "card_last_four": "1234",
        "charge_date": "2026-10-10",
        "statement_final": True,
        "scheduled_total": "150.50",
        "off_cycle_total": "108.55",
    }


def test_isracard_web_export_refuses_unsafe_input(tmp_path):
    path = tmp_path / "x.xlsx"
    with pytest.raises(ParserError, match="card ending 1234"):
        IsracardAdapter(card_last_four="9999").parse(isracard_web_export(), ".xlsx", path, {})
    with pytest.raises(ParserError, match="not final"):
        IsracardAdapter().parse(isracard_web_export(open_statement=True), ".xlsx", path, {})
    rows = IsracardAdapter().parse(
        isracard_web_export(open_statement=True), ".xlsx", path, {"allow_open": True}
    )
    assert len(rows) == 5
    with pytest.raises(ParserError, match="totals disagree"):
        IsracardAdapter().parse(isracard_web_export(header_total="₪ 150.51"), ".xlsx", path, {})
    without_footer = isracard_web_export(footer=False)
    assert len(IsracardAdapter().parse(without_footer, ".xlsx", path, {})) == 5


def test_isracard_mapped_xlsx_still_uses_explicit_mapping(tmp_path):
    with pytest.raises(ParserError, match="allow_open"):
        IsracardAdapter().parse(b"a,b\n", ".csv", tmp_path / "x.csv", {"allow_open": True})


def isracard_fetcher_export(last_four="1234", **changes):
    """Synthetic fetcher/fetch.mjs output shaped like israeli-bank-scrapers 6.12.2; values are invented and
    mirror isracard_web_export so both sources describe the same purchases."""

    def txn(day, merchant, charged, voucher, *, billing="2026-10-09T21:00:00.000Z", **extra):
        return {
            "type": "normal",
            "identifier": voucher,
            "date": day,
            "processedDate": billing,
            "originalAmount": charged,
            "originalCurrency": "ILS",
            "chargedAmount": charged,
            "chargedCurrency": "ILS",
            "description": merchant,
            "memo": "",
            "status": "completed",
        } | extra

    document = {
        "format": "israeli-bank-scrapers-account/v1",
        "company": "isracard",
        "scraper_version": "6.12.2",
        "fetched_at": "2026-10-19T08:00:00.000Z",
        "start_date": "2026-08-31T21:00:00.000Z",
        "account_number": last_four,
        "txns": [
            txn("2026-08-31T21:00:00.000Z", "Synthetic grocer", -120.25, 900000001),
            txn(
                "2026-10-02T21:00:00.000Z",  # library shifts installment dates
                "Synthetic furniture",
                -50.25,
                900000002,
                type="installments",
                originalAmount=-600,
                installments={"number": 2, "total": 12},
            ),
            txn("2026-09-03T21:00:00.000Z", "Synthetic return", 20, 900000003),
            txn(
                "2026-09-19T21:00:00.000Z",
                "SYNTHETIC CAFE BUCHA",  # the library cuts names at 20 characters
                -47.11,
                900000004,
                billing="2026-10-01T21:00:00.000Z",
                originalAmount=-63,
                originalCurrency="RON",
                memo='בוצע בחו"ל',
            ),
            txn(
                "2026-09-20T21:00:00.000Z",
                "SYNTHETIC SAAS",
                -61.44,
                900000005,
                billing="2026-10-01T21:00:00.000Z",
                originalAmount=-20,
                originalCurrency="USD",
            ),
            txn("2026-10-04T21:00:00.000Z", "Pending shop", -9.99, 0, status="pending"),
            txn("2026-09-05T21:00:00.000Z", "Leading zero voucher", -5, 12345678),
            txn("2026-09-06T21:00:00.000Z", "Waived fee", 0, 900000009),
        ],
    } | changes
    return json.dumps(document).encode()


def test_isracard_fetcher_export(tmp_path):
    adapter = IsracardAdapter(card_last_four="1234")
    rows = adapter.parse(isracard_fetcher_export(), ".json", tmp_path / "x.json", {})
    assert adapter.version == "isracard_scraper_json_v1" and not any(r.error for r in rows)
    grocer, furniture, refund, cafe, saas, padded = (r.data for r in rows)  # pending skipped
    assert (
        grocer["transaction_date"] == "2026-09-01" and grocer["posting_date"] == "2026-10-10"
    )  # UTC -> Israel
    assert grocer["amount_minor"] == -12025 and grocer["source_reference"] == "900000001"
    assert padded["source_reference"] == "012345678"
    assert furniture["amount_minor"] == -5025 and "תשלום 2 מתוך 12" in furniture["raw_description"]
    assert furniture["transaction_date"] == "2026-09-03"  # shift undone, matches the website export
    assert furniture["allocation_date"] == "2026-10-10"  # installment belongs to its charge month
    assert refund["kind"] == "refund" and refund["amount_minor"] == 2000
    assert cafe["original_currency"] == "RON" and cafe["original_amount_minor"] == -6300
    assert cafe["posting_date"] == "2026-10-02" and saas["original_currency"] == "USD"
    assert adapter.native["pending_skipped"] == 1 and adapter.native["zero_skipped"] == 1
    assert adapter.native["billing_totals"] == {"2026-10-02": "108.55", "2026-10-10": "155.50"}


def test_isracard_fetcher_export_refuses_wrong_card_and_other_json(tmp_path):
    path = tmp_path / "x.json"
    with pytest.raises(ParserError, match="card ending 9876"):
        IsracardAdapter(card_last_four="1234").parse(isracard_fetcher_export("9876"), ".json", path, {})
    with pytest.raises(ParserError, match="not from Isracard"):
        IsracardAdapter().parse(isracard_fetcher_export(company="max"), ".json", path, {})
    with pytest.raises(ParserError, match="Not an Isracard fetcher export"):
        IsracardAdapter().parse(b'{"rows": []}', ".json", path, {})
    with pytest.raises(ParserError, match="not from Isracard"):
        IsracardAdapter().parse(hapoalim_fetcher_export(), ".json", path, {})
    waived = json.loads(isracard_fetcher_export())
    waived["txns"] = [t for t in waived["txns"] if t["chargedAmount"] == 0 or t["status"] == "pending"]
    adapter = IsracardAdapter()
    assert adapter.parse(json.dumps(waived).encode(), ".json", path, {}) == []  # nothing to import yet
    assert adapter.native["zero_skipped"] == 1 and adapter.native["pending_skipped"] == 1
    with pytest.raises(ParserError, match="no billed transactions"):
        IsracardAdapter().parse(isracard_fetcher_export(txns=[]), ".json", path, {})


def hapoalim_fetcher_export(**changes):
    """Synthetic fetcher/lib.mjs output shaped like israeli-bank-scrapers 6.12.2 (scrapers/hapoalim.js);
    values are invented."""

    def txn(day, description, amount, reference, *, value_day=None, **extra):
        return {
            "type": "normal",
            "identifier": reference,
            "date": day,
            "processedDate": value_day or day,
            "originalAmount": amount,
            "originalCurrency": "ILS",
            "chargedAmount": amount,
            "description": description,
            "status": "completed",
            "memo": "",
        } | extra

    document = {
        "format": "israeli-bank-scrapers-account/v1",
        "company": "hapoalim",
        "scraper_version": "6.12.2",
        "fetched_at": "2026-10-19T08:00:00.000Z",
        "start_date": "2026-08-31T21:00:00.000Z",
        "account_number": "12-345-123456",
        "balance": 1234.5,
        "txns": [
            txn("2026-10-01T21:00:00.000Z", "משכורת", 20000, 11),
            txn("2026-10-09T21:00:00.000Z", "ישראכרט", -5975.15, 99),
            txn("2026-10-09T21:00:00.000Z", "עמלה", -12.5, 99, value_day="2026-10-10T21:00:00.000Z"),
            txn(
                "2026-10-11T21:00:00.000Z",
                "העברה-נייד",
                -150,
                None,
                memo="לטובת  ישראל ישראלי. ארוחה.",
            ),
            txn("2026-10-12T21:00:00.000Z", "הוראת קבע", -100, 5, status="pending"),
        ],
    } | changes
    return json.dumps(document).encode()


def test_hapoalim_fetcher_export(tmp_path):
    adapter = HapoalimAdapter()
    rows = adapter.parse(hapoalim_fetcher_export(), ".json", tmp_path / "x.json", {})
    assert adapter.version == "hapoalim_scraper_json_v1" and not any(r.error for r in rows)
    salary, card, fee, transfer = (r.data for r in rows)  # pending skipped
    assert salary["transaction_date"] == "2026-10-02" and salary["amount_minor"] == 2000000
    assert salary["kind"] == "refund"  # from the sign only; classification rules set the purpose
    assert card["merchant"] == "ישראכרט" and card["amount_minor"] == -597515
    assert card["source_reference"] is None and "אסמכתא 99" in card["raw_description"]  # references repeat
    assert fee["transaction_date"] == "2026-10-10" and fee["posting_date"] == "2026-10-11"  # value date
    assert transfer["raw_description"] == "לטובת ישראל ישראלי. ארוחה."
    assert adapter.native == {
        "layout": "hapoalim_scraper_json_v1",
        "account_last_four": "3456",
        "scraper_version": "6.12.2",
        "fetched_at": "2026-10-19T08:00:00.000Z",
        "pending_skipped": 1,
        "balance": "1234.50",
    }


def test_hapoalim_fetcher_export_refuses_other_json(tmp_path):
    path = tmp_path / "x.json"
    with pytest.raises(ParserError, match="Not a Bank Hapoalim fetcher export"):
        HapoalimAdapter().parse(isracard_fetcher_export(), ".json", path, {})
    with pytest.raises(ParserError, match="Not a Bank Hapoalim fetcher export"):
        HapoalimAdapter().parse(b"{}", ".json", path, {})
    with pytest.raises(ParserError, match="takes no import options"):
        HapoalimAdapter().parse(hapoalim_fetcher_export(), ".json", path, {"sign": "signed"})
    rows = HapoalimAdapter().parse(
        hapoalim_fetcher_export(txns=[{"date": "2026-10-01T21:00:00.000Z", "chargedAmount": -1}]),
        ".json",
        path,
        {},
    )
    assert rows[0].error
