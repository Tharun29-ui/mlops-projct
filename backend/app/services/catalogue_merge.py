"""Prepare a lossless catalogue consolidation only after an explicit identity decision."""
from decimal import Decimal, ROUND_HALF_UP
from app.services.product_classification import classified_product


def confirmed_product_merge(target: dict, source: dict, *, confirmed_same_product: bool, timestamp: str):
    if not confirmed_same_product:
        raise ValueError("An explicit same-product confirmation is required.")
    if target['id'] == source['id'] or target.get('merged_into') or source.get('merged_into'):
        raise ValueError("Choose two distinct active products.")
    left, right = classified_product(target), classified_product(source)
    for field in ('brand', 'product_type', 'model', 'hsn_sac'):
        a, b = str(left.get(field) or '').strip().casefold(), str(right.get(field) or '').strip().casefold()
        if a and b and a != b:
            raise ValueError(f"Conflicting {field}; verify the product specifications first.")
    for field in ('minimum_margin_percent', 'target_margin_percent'):
        if target.get(field) is not None and source.get(field) is not None:
            if Decimal(str(target[field])) != Decimal(str(source[field])):
                raise ValueError("Conflicting pricing rules; choose the intended rule first.")
    tq, sq = int(target['quantity_on_hand']), int(source['quantity_on_hand'])
    if min(tq, sq) < 0:
        raise ValueError("Resolve negative stock before merging.")
    total = tq + sq
    cost = ((Decimal(str(target['unit_cost'])) * tq + Decimal(str(source['unit_cost'])) * sq) / total
            if total else Decimal(str(target['unit_cost'])))
    cost = cost.quantize(Decimal('0.01'), rounding=ROUND_HALF_UP)
    aliases = []
    seen = {str(target['name']).casefold().strip()}
    for name in [*target.get('aliases', []), source['name'], *source.get('aliases', [])]:
        if name.strip().casefold() not in seen:
            aliases.append(name.strip()); seen.add(name.strip().casefold())
    if len(aliases) > 100:
        raise ValueError("Too many learned names for one product.")
    combined = {**target, 'aliases': aliases, 'quantity_on_hand': total, 'unit_cost': str(cost),
        'reorder_point': max(int(target.get('reorder_point') or 0), int(source.get('reorder_point') or 0)),
        'stock_count_verified': False, 'last_catalogue_merge_at': timestamp,
        'merged_product_ids': list(dict.fromkeys([*target.get('merged_product_ids', []), source['id'], *source.get('merged_product_ids', [])]))}
    combined.pop('stock_count_verified_at', None)
    combined.pop('last_counted_quantity', None)
    archived = {**source, 'quantity_on_hand': 0, 'pre_merge_quantity_on_hand': sq,
        'merged_into': target['id'], 'merged_at': timestamp, 'stock_count_verified': False}
    return combined, archived
