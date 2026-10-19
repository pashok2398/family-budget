from ..normalization.money import normalized


def matches(transaction, conditions):
    for key, value in conditions.items():
        if key == "merchant_in":
            if normalized(transaction.merchant) not in {normalized(x) for x in value}:
                return False
        elif key == "date_from":
            if transaction.transaction_date.isoformat() < value:
                return False
        elif key == "date_to":
            if transaction.transaction_date.isoformat() > value:
                return False
        elif key == "direction":
            if (transaction.amount_minor > 0) != (value == "credit"):
                return False
        elif key == "amount_minor":
            if transaction.amount_minor != value:
                return False
        elif key == "currency":
            if transaction.currency != value:
                return False
        elif key == "source":  # "bank": a bank account movement; "card": a credit card transaction
            if (transaction.card_id is None) != (value == "bank"):
                return False
        elif key == "transaction_date":
            if transaction.transaction_date.isoformat() != value:
                return False
        else:
            return False
    return True
