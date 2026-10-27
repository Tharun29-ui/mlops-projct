"""Check dated stock availability without inventing purchases or opening quantities."""
from collections import defaultdict
from datetime import date


def validate_dated_stock(current_quantity, movements, change, effective_date, product_name):
    if int(change) >= 0:
        return
    day = str(effective_date)[:10]
    date.fromisoformat(day)
    dated = [m for m in movements if m.get('effective_date')]
    # Legacy, undated balances remain explicit opening history of unknown date.
    # All newly recorded invoice and manual movements carry an effective date.
    balance = int(current_quantity) - sum(int(m['quantity_change']) for m in dated)
    daily = defaultdict(int)
    for movement in dated:
        daily[str(movement['effective_date'])[:10]] += int(movement['quantity_change'])
    daily[day] += int(change)
    for recorded_day, delta in sorted(daily.items()):
        balance += delta
        if balance < 0:
            raise ValueError(f"{product_name}: stock is short by {-balance} on {recorded_day}. "
                "Record the missing earlier purchase or a confirmed opening balance dated on or before the sale. "
                "A later purchase cannot supply an earlier sale.")
