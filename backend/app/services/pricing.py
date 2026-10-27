from decimal import Decimal


def effective_margins(product: dict, policy: dict) -> tuple[Decimal | None, Decimal | None]:
    minimum = product.get("minimum_margin_percent")
    target = product.get("target_margin_percent")
    if minimum is None:
        minimum = policy.get("minimum_margin_percent")
    if target is None:
        target = policy.get("target_margin_percent")
    min_value = Decimal(str(minimum)) if minimum is not None else None
    target_value = Decimal(str(target)) if target is not None else None
    if target_value is None:
        target_value = min_value
    if min_value is not None and target_value is not None:
        target_value = max(target_value, min_value)
    return min_value, target_value


def price_points(unit_cost: Decimal, minimum_margin: Decimal | None,
                 target_margin: Decimal | None) -> tuple[Decimal, Decimal]:
    floor = unit_cost / (Decimal("1") - minimum_margin / Decimal("100")) if minimum_margin is not None else unit_cost
    suggested = unit_cost / (Decimal("1") - target_margin / Decimal("100")) if target_margin is not None else floor
    return floor, max(suggested, floor)
