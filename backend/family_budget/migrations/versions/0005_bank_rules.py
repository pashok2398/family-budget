"""Classification rules for Bank Hapoalim descriptions seen in the first real fetch (19.10.2026).

Card lines in the bank are payments for purchases that the Isracard import already holds: "ישראכרט בע"מ"
is a monthly card bill and "מסטרקרד" an immediate-debit card charged per purchase. They are card
settlements, never spending. Rules carry "source": "bank" so a card purchase with a similar name is never
mistaken for one. Only purposes that are certain from the bank's own text are confirmed; transfers whose
meaning depends on the household (between own accounts, reimbursements, projects) stay in review.
"""

import uuid

from alembic import op
import sqlalchemy as sa

revision = "0005"
down_revision = "0004"
branch_labels = None
depends_on = None

BANK = {"currency": "ILS", "source": "bank"}
RULES = [
    (
        "בנק: תשלום כרטיס אשראי",
        ['ישראכרט בע"מ', "ישראכרט", "מסטרקרד"],
        "debit",
        {"kind": "card_settlement", "scope": "household"},
    ),
    (
        "בנק: החזר חיוב כרטיס אשראי",
        ['ישראכרט בע"מ', "ישראכרט", "מסטרקרד"],
        "credit",
        {"kind": "internal_transfer", "scope": "household"},
    ),
    (
        "בנק: משכנתא טפחות",
        ["טפחות-משכנתא"],
        "debit",
        {"kind": "mortgage", "scope": "household", "category_code": "mortgage"},
    ),
    (
        "בנק: קצבת ילדים",
        ["קצבת ילדים"],
        "credit",
        {"kind": "income", "scope": "household", "category_code": "income"},
    ),
    ("בנק: משיכת מזומן", ["משיכה מבנקט"], "debit", {"kind": "cash_withdrawal", "scope": "household"}),
    ('בנק: מכירת ני"ע', ['ני"ע-מכירה-נט'], "credit", {"kind": "investment_sale", "scope": "household"}),
]


def upgrade():
    rules = sa.table(
        "classification_rules",
        sa.column("id", sa.String),
        sa.column("name", sa.String),
        sa.column("priority", sa.Integer),
        sa.column("conditions", sa.JSON),
        sa.column("result", sa.JSON),
        sa.column("confirmed", sa.Boolean),
        sa.column("active", sa.Boolean),
    )
    existing = {row[0] for row in op.get_bind().execute(sa.text("SELECT name FROM classification_rules"))}
    op.bulk_insert(
        rules,
        [
            {
                "id": str(uuid.uuid4()),
                "name": name,
                "priority": 18,
                "conditions": {"merchant_in": merchants, "direction": direction, **BANK},
                "result": result,
                "confirmed": True,
                "active": True,
            }
            for name, merchants, direction, result in RULES
            if name not in existing
        ],
    )


def downgrade():
    names = ", ".join(f"'{name}'" for name, *_ in RULES)
    op.execute(f"DELETE FROM classification_rules WHERE name IN ({names})")
