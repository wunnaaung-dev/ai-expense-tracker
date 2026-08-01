from constants.categories import DEFAULT_CATEGORY, VALID_CATEGORIES

def validate_category(category: str | None) -> str:
    if not category:
        return DEFAULT_CATEGORY

    normalized = category.strip().title()

    for valid in VALID_CATEGORIES:
        if normalized.lower() == valid.lower():
            return valid 

    return DEFAULT_CATEGORY