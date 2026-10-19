from datetime import date
from typing import Literal
from pydantic import BaseModel, Field, ConfigDict, StrictBool, StrictInt, StrictStr

Scope = Literal["household", "pub", "project", "investment"]
Kind = Literal[
    "purchase",
    "refund",
    "income",
    "card_settlement",
    "internal_transfer",
    "cash_withdrawal",
    "loan_received",
    "loan_payment",
    "mortgage",
    "savings_transfer",
    "investment_transfer",
    "investment_sale",
    "reimbursement",
    "interest",
]


class Input(BaseModel):
    model_config = ConfigDict(extra="forbid")


class AccountInput(Input):
    name: str = Field(min_length=1, max_length=100)
    institution: str = Field(min_length=1, max_length=100)
    currency: str = Field(default="ILS", pattern=r"^[A-Z]{3}$")


class CardInput(Input):
    account_id: str
    name: str = Field(min_length=1, max_length=100)
    last_four: str | None = Field(default=None, pattern=r"^\d{4}$")


class CardUpdateInput(Input):
    name: str = Field(min_length=1, max_length=100)
    last_four: str | None = Field(default=None, pattern=r"^\d{4}$")


class SplitInput(Input):
    category_id: str
    amount: StrictStr
    scope: Scope = "household"
    project_id: str | None = None


class Correction(Input):
    id: str
    version: int = Field(ge=1)
    category_id: str | None = None
    scope: Scope = "household"
    kind: Kind = "purchase"
    project_id: str | None = None
    income_source_id: str | None = None
    allocation_date: date | None = None
    merchant: str | None = Field(default=None, min_length=1, max_length=500)
    note: str = Field(min_length=1, max_length=1000)
    splits: list[SplitInput] = Field(default_factory=list, max_length=30)
    create_merchant_rule: bool = False
    reimbursement_expected: bool | None = None


class Corrections(Input):
    items: list[Correction] = Field(min_length=1, max_length=100)


class ResolveInput(Input):
    resolution: str


class MatchInput(Input):
    kind: Literal["card_settlement", "internal_transfer", "refund", "reimbursement"]
    ids: list[str] = Field(min_length=2, max_length=500)
    explanation: str = Field(min_length=5, max_length=2000)


class BudgetInput(Input):
    income: StrictStr
    allocations: dict[str, StrictStr]
    provisional: bool = True
    as_template: bool = False


class CategoryInput(Input):
    name: str = Field(min_length=1, max_length=100)
    kind: Literal["expense", "debt", "fund", "savings", "income"] = "expense"


class FundInput(Input):
    name: str = Field(min_length=1, max_length=100)
    category_id: str
    monthly_target: StrictStr


class MovementInput(Input):
    amount: StrictStr
    date: date
    note: str = Field(min_length=1, max_length=500)
    request_key: str = Field(min_length=8, max_length=100)
    transaction_id: str | None = None


class ProjectInput(Input):
    name: str = Field(min_length=1, max_length=100)
    budget: StrictStr
    scope: Literal["project", "pub", "investment"] = "project"
    active: bool = True
    reimbursable: bool = False


class IncomeInput(Input):
    name: str = Field(min_length=1, max_length=100)
    expected: StrictStr | None = None
    provisional: bool = True


class LoanInput(Input):
    name: str = Field(min_length=1, max_length=100)
    principal: StrictStr | None = None
    monthly_payment: StrictStr


class LoanPaymentInput(Input):
    transaction_id: str
    principal: StrictStr
    interest: StrictStr


class RecurringInput(Input):
    name: str = Field(min_length=1, max_length=100)
    category_id: str
    expected: StrictStr
    day_of_month: int = Field(ge=1, le=28)
    active: bool = True
    merchant: str | None = Field(default=None, max_length=200)


class PreferencesInput(Input):
    period_start_day: StrictInt | None = Field(default=None, ge=1, le=28)
    card_spending_basis: Literal["charge", "purchase"] | None = None
    review_all_imports: StrictBool | None = None


class FetcherCodeInput(Input):
    code: StrictStr = Field(pattern=r"^\d{4,8}$")


class InboxPreviewInput(Input):
    account_id: StrictStr
    card_id: StrictStr | None = None


class ReimbursementPaymentInput(Input):
    project_id: StrictStr
    received_on: date
    amount: StrictStr
    method: Literal["bank", "bit", "paybox", "cash", "other"]
    note: str = Field(default="", max_length=1000)
    reimbursement_ids: list[StrictStr] | None = Field(default=None, max_length=500)
    transaction_id: StrictStr | None = None
