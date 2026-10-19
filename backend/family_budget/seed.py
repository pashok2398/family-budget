from sqlalchemy import select
from .models import (
    Category,
    Budget,
    BudgetAllocation,
    ClassificationRule,
    SinkingFund,
    IncomeSource,
    Project,
    Loan,
)

BASELINE = [
    ("mortgage", "משכנתא", 8700, "debt"),
    ("groceries", "מזון וסופר", 4800, "expense"),
    ("utilities", "חשבונות, ארנונה ותקשורת", 2800, "expense"),
    ("health", "ביטוח ובריאות", 1700, "expense"),
    ("loan", "הלוואה", 1100, "debt"),
    ("transport", "רכב ותחבורה", 1800, "expense"),
    ("children", "ילדים וחוגים", 2000, "expense"),
    ("dining", "מסעדות ובילויים", 1400, "expense"),
    ("shopping", "ביגוד וקניות לבית", 1300, "expense"),
    ("hobbies", "תחביבים וכלי עבודה", 500, "expense"),
    ("vacation", "קרן חופשה", 1200, "fund"),
    ("maintenance", "תחזוקת הבית והוצאות שנתיות", 1000, "fund"),
    ("personal", "פארם, חיות ואישי", 900, "expense"),
    ("unexpected", "בלתי צפוי", 1300, "expense"),
    ("savings", "חיסכון אוטומטי", 1500, "savings"),
]


def seed(session):
    if session.scalar(select(Category.id).limit(1)):
        return
    categories = {}
    for code, name, _, kind in BASELINE + [
        ("donations", "תרומות", 0, "expense"),
        ("income", "הכנסה", 0, "income"),
    ]:
        item = Category(code=code, name=name, kind=kind)
        session.add(item)
        session.flush()
        categories[code] = item
    budget = Budget(month="template", income_minor=3200000, provisional=True)
    session.add(budget)
    session.flush()
    for code, _, amount, _ in BASELINE:
        session.add(
            BudgetAllocation(budget_id=budget.id, category_id=categories[code].id, amount_minor=amount * 100)
        )
    for code in ("vacation", "maintenance"):
        amount = next(row[2] for row in BASELINE if row[0] == code)
        session.add(
            SinkingFund(
                name=categories[code].name, category_id=categories[code].id, monthly_target_minor=amount * 100
            )
        )
    session.add_all(
        [
            IncomeSource(name="שכר משק בית", expected_minor=None),
            IncomeSource(name="שכר בן/בת זוג", expected_minor=None),
            Project(name="פרויקט גינה (דוגמה)", budget_minor=3540000),
            Loan(name="הלוואה לדוגמה", principal_minor=None, monthly_payment_minor=110000),
            Loan(name="משכנתא", principal_minor=None, monthly_payment_minor=870000),
        ]
    )

    def rule(
        name,
        merchants,
        code,
        *,
        amount=None,
        day=None,
        start=None,
        end=None,
        scope="household",
        kind="purchase",
        confirmed=True,
        priority=20,
        **extra,
    ):
        conditions = (
            {"merchant_in": merchants, "direction": "credit" if kind == "income" else "debit"}
            if merchants
            else {"direction": "debit"}
        )
        conditions["currency"] = "ILS"
        if amount is not None:
            conditions["amount_minor"] = -amount
        if day:
            conditions["transaction_date"] = day
        if start:
            conditions.update(date_from=start, date_to=end)
        session.add(
            ClassificationRule(
                name=name,
                priority=priority,
                conditions=conditions,
                result={"category_code": code, "scope": scope, "kind": kind, **extra},
                confirmed=confirmed,
            )
        )

    rule("שכר — אקמה", ["Acme Industries", "אקמה תעשיות"], "income", kind="income", income_name="שכר משק בית")
    rule(
        "שכר — גלובקס",
        ["Globex Systems", "גלובקס מערכות"],
        "income",
        kind="income",
        income_name="שכר בן/בת זוג",
    )
    rule("הלוואה לדוגמה", ["Example Credit", "אשראי לדוגמה"], "loan", kind="loan_payment")
    # Aggregate-only evidence is a suggestion: never claim to identify unseen source records.
    rule(
        "שתי העברות גינה — נדרש זיהוי מקור",
        [],
        "maintenance",
        amount=1770000,
        start="2026-09-01",
        end="2026-09-30",
        scope="project",
        confirmed=False,
        priority=40,
        project_name="פרויקט גינה (דוגמה)",
    )
    rule(
        "מטבחים לדוגמה — סה״כ צפוי 5,680",
        ["Example Kitchens", "מטבחים לדוגמה"],
        "shopping",
        confirmed=False,
        priority=40,
        reimbursement_expected=True,
        note="User reported aggregate 5680 ILS; exact records/refund unverified",
    )
    rule(
        "רהיטים לדוגמה",
        ["Example Furniture", "רהיטים לדוגמה"],
        "shopping",
        amount=370000,
        scope="pub",
        reimbursement_expected=True,
        note="Local committee reimbursement pending",
    )
    rule("איקאה", ["IKEA", "איקאה"], "shopping")
    rule("QTOOLS", ["QTOOLS"], "hobbies")
    rule("ציוד טכני לדוגמה", ["Example Tools", "ציוד טכני לדוגמה"], "hobbies")
    # One-off exceptions: an exact merchant, amount and date, so other purchases there still get reviewed.
    exact = [
        ("Example Hardware", "חומרי בניין לדוגמה", 97500, "2026-10-15", "maintenance", "household"),
        ("PAYBOX", "פייבוקס", 10000, "2026-10-13", "dining", "pub"),
        ("Example Sports Club", "מועדון ספורט לדוגמה", 41000, "2026-10-11", "children", "household"),
        ("Example Grocer", "מכולת לדוגמה", 36487, "2026-10-18", "groceries", "household"),
    ]
    for merchant, hebrew, amount, day, code, scope in exact:
        rule(
            merchant + " — חריג מאושר",
            [merchant, hebrew],
            code,
            amount=amount,
            day=day,
            scope=scope,
            priority=10,
        )
    rule(
        "AliExpress / Alipay — סה״כ צפוי 338.81",
        ["AliExpress", "PayPal Alipay"],
        "shopping",
        start="2026-10-05",
        end="2026-10-17",
        confirmed=False,
        priority=40,
    )
