EXPENSE_CATEGORIES = [
    "Food",
    "Transportation",
    "Utilities",
    "Shopping",
    "Entertainment",
    "Health",
    "Saving",
    "General",  # Fallback / Default Category
]

INCOME_CATEGORIES = [
    "Salary",
    "Freelance",
    "Business",
    "Gift",
    "Refund",
    "Investment",
    "General",  # Fallback / Default Category
]

CATEGORIES_BY_TYPE = {
    "expense": EXPENSE_CATEGORIES,
    "income": INCOME_CATEGORIES,
}

DEFAULT_CATEGORY = "General"
