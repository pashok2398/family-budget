import re
from sqlalchemy import select, func
from ..models import Budget, BudgetAllocation, Category, FundMovement, SinkingFund, Transaction, AuditEvent
from ..normalization.money import minor


def validate_month(month):
    if not re.fullmatch(r"\d{4}-(0[1-9]|1[0-2])", month):
        raise ValueError("Month must be YYYY-MM")
    return month


def budget_for(session, month, create=False):
    validate_month(month)
    budget = session.scalar(select(Budget).where(Budget.month == month))
    if budget or not create:
        return budget or session.scalar(select(Budget).where(Budget.month == "template"))
    template = session.scalar(select(Budget).where(Budget.month == "template"))
    budget = Budget(month=month, income_minor=template.income_minor, provisional=template.provisional)
    session.add(budget)
    session.flush()
    for allocation in session.scalars(
        select(BudgetAllocation).where(BudgetAllocation.budget_id == template.id)
    ):
        session.add(
            BudgetAllocation(
                budget_id=budget.id, category_id=allocation.category_id, amount_minor=allocation.amount_minor
            )
        )
    session.flush()
    return budget


def _template(session):
    return session.scalar(select(Budget).where(Budget.month == "template"))


def update_budget(session, month, income, allocations, provisional, as_template=False):
    if as_template:
        # Months without their own saved plan start from the template; saved months keep theirs.
        update_budget(session, "template", income, allocations, provisional)
    budget = budget_for(session, month, create=True) if month != "template" else _template(session)
    before = {
        "income_minor": budget.income_minor,
        "allocations": {
            a.category_id: a.amount_minor
            for a in session.scalars(select(BudgetAllocation).where(BudgetAllocation.budget_id == budget.id))
        },
    }
    budget.income_minor, budget.provisional = minor(income), provisional
    if budget.income_minor < 0:
        raise ValueError("Income plan cannot be negative")
    for category_id, amount in allocations.items():
        if not session.get(Category, category_id) or minor(amount) < 0:
            raise ValueError("Unknown category or negative allocation")
        allocation = session.scalar(
            select(BudgetAllocation).where(
                BudgetAllocation.budget_id == budget.id, BudgetAllocation.category_id == category_id
            )
        )
        if not allocation:
            allocation = BudgetAllocation(budget_id=budget.id, category_id=category_id, amount_minor=0)
            session.add(allocation)
        allocation.amount_minor = minor(amount)
    if month == "template":
        # Keep each fund's stored target equal to its default plan amount.
        for fund in session.scalars(select(SinkingFund)):
            if fund.category_id in allocations:
                fund.monthly_target_minor = minor(allocations[fund.category_id])
    session.add(
        AuditEvent(
            entity_type="budget",
            entity_id=budget.id,
            action="edit",
            before=before,
            after={"income_minor": budget.income_minor, "allocations": allocations},
        )
    )
    return budget


def move_fund(session, fund_id, amount, when, note, request_key, transaction_id=None):
    previous = session.scalar(select(FundMovement).where(FundMovement.request_key == request_key))
    value = minor(amount)
    if previous:
        if (
            previous.fund_id,
            previous.amount_minor,
            previous.date,
            previous.transaction_id,
            previous.note,
        ) != (fund_id, value, when, transaction_id, note):
            raise ValueError("Idempotency key reused with different data")
        return previous
    fund = session.get(SinkingFund, fund_id)
    if not fund or value == 0:
        raise ValueError("Unknown fund or zero movement")
    # A later allocation cannot finance an earlier withdrawal.
    days = dict(
        session.execute(
            select(FundMovement.date, func.sum(FundMovement.amount_minor))
            .where(FundMovement.fund_id == fund_id)
            .group_by(FundMovement.date)
        ).all()
    )
    days[when] = days.get(when, 0) + value
    balance = 0
    for day in sorted(days):
        balance += days[day]
        if balance < 0:
            raise ValueError("Insufficient earmarked fund balance on movement date")
    if transaction_id:
        tx = session.get(Transaction, transaction_id)
        if (
            not tx
            or tx.currency != "ILS"
            or tx.amount_minor != value
            or tx.category_id != fund.category_id
            or tx.scope != "household"
        ):
            raise ValueError(
                "Linked purchase must have same signed ILS amount, household scope and fund category"
            )
    movement = FundMovement(
        fund_id=fund_id,
        amount_minor=value,
        date=when,
        note=note,
        transaction_id=transaction_id,
        request_key=request_key,
    )
    session.add(movement)
    session.flush()
    session.add(
        AuditEvent(
            entity_type="fund_movement",
            entity_id=movement.id,
            action="create",
            before={},
            after={"fund_id": fund_id, "amount_minor": value, "transaction_id": transaction_id},
        )
    )
    return movement
