"""One forecast pipeline: bill profits aggregated by month."""
import json
from pathlib import Path
from typing import Annotated, Literal
from fastapi import APIRouter, Depends, HTTPException, Query, Response
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.orm import Session
from app.core.config import get_settings
from app.db import get_db
from app.models import Invoice
from app.repositories import dynamodb_store as store
from app.api.routes.invoices import _invoice_dict
from app.services.analysis_sources import is_synthetic_invoice
from app.services.bill_profit import amount, normalise_invoice, build_profit_report
from app.services import profit_state
from app.services.inference_timing import benchmark_report

router = APIRouter(prefix='/forecasts', tags=['monthly bill profit'])
Source = Literal['real', 'synthetic']
Month = Annotated[str | None, Query(pattern=r'^\d{4}-(0[1-9]|1[0-2])$')]


def _catalog(db, source):
    path = Path(__file__).resolve().parents[2] / 'data/bill-profit-ledger.json'
    data = json.loads(path.read_text(encoding='utf-8'))[source]
    sales = {b['id']: b for b in data['sales']}
    purchases = {b['id']: b for b in data['purchases']}
    if source == 'real':
        invoices = store.list_invoices() if get_settings().business_data_table_name else [_invoice_dict(i) for i in db.scalars(select(Invoice)).all()]
        for invoice in invoices:
            if is_synthetic_invoice(invoice) or invoice.get('transaction_type') not in {'SALE', 'PURCHASE'}:
                continue
            try:
                bill = normalise_invoice(invoice, filename=invoice.get('filename', ''))
            except (ValueError, TypeError):
                continue
            target = sales if invoice['transaction_type'] == 'SALE' else purchases
            if invoice.get('status') == 'REJECTED':
                target.pop(bill['id'], None)
            elif invoice.get('status') == 'APPROVED' or bill['id'] in target:
                target[bill['id']] = bill
    return list(sales.values()), list(purchases.values())


def _report(db, source, horizon, start_month, state=None):
    sales, purchases = _catalog(db, source)
    state = state if state is not None else profit_state.load(db) if source == 'real' else {'revision': None, 'inputs': {}}
    try:
        result = build_profit_report(sales, purchases, source=source, confirmations=state['inputs'],
                                     horizon=horizon, start_month=start_month)
    except (ValueError, OverflowError) as exc:
        raise HTTPException(422, str(exc)) from exc
    result['label_revision'] = state.get('revision')
    result['source_description'] = 'Source-reviewed real bills and additional approved business invoices' if source == 'real' else 'Separate generated sales and purchase invoices'
    return result


@router.get('/profit')
def profit_report(db: Session = Depends(get_db), source: Source = 'real', horizon: Annotated[int, Query(ge=1, le=6)] = 1, start_month: Month = None):
    return _report(db, source, horizon, start_month)


@router.get('/profit/benchmark')
def profit_benchmark(response: Response, db: Session = Depends(get_db), source: Source = 'real', horizon: Annotated[int, Query(ge=1, le=6)] = 1, start_month: Month = None):
    response.headers['Cache-Control'] = 'no-store'
    return benchmark_report(lambda: _report(db, source, horizon, start_month))


class BillProfitInput(BaseModel):
    bill_id: str = Field(min_length=1, max_length=100)
    source_fingerprint: str = Field(pattern=r'^[a-f0-9]{64}$')
    gross_profit: str = Field(min_length=1, max_length=32)
    note: str = Field(min_length=1, max_length=600)


class ProfitConfirmations(BaseModel):
    expected_revision: str | None = Field(default=None, max_length=64)
    values: list[BillProfitInput] = Field(min_length=1, max_length=500)
    confirm_actual_values: bool


@router.post('/profit/confirmations')
def confirm_profits(payload: ProfitConfirmations, db: Session = Depends(get_db)):
    if not payload.confirm_actual_values:
        raise HTTPException(422, 'Confirm that these bill profits come from verified costs or actual profit records.')
    sales, _ = _catalog(db, 'real')
    by_id = {b['id']: b for b in sales}
    records = []
    seen = set()
    for entry in payload.values:
        bill = by_id.get(entry.bill_id)
        if bill is None:
            raise HTTPException(404, 'A bill is not present in the real invoice ledger.')
        if entry.bill_id in seen:
            raise HTTPException(422, 'Each bill can appear only once in an import.')
        seen.add(entry.bill_id)
        if bill['source_fingerprint'] != entry.source_fingerprint:
            raise HTTPException(409, 'The bill changed. Reload it before confirming its profit.')
        try:
            profit = amount(entry.gross_profit)
            if profit > amount(bill['net_sales']):
                raise ValueError('Bill profit cannot exceed its net sales.')
            if not entry.note.strip():
                raise ValueError('Add the source or reason for this profit value.')
        except ValueError as exc:
            raise HTTPException(422, str(exc)) from exc
        records.append({'bill_id': entry.bill_id, 'source_fingerprint': entry.source_fingerprint,
                        'gross_profit': str(profit), 'note': entry.note.strip()})
    try:
        state = profit_state.save(db, records, payload.expected_revision)
    except ValueError as exc:
        raise HTTPException(409, str(exc)) from exc
    return _report(db, 'real', 1, None, state=state)


@router.get('/latest')
def latest_profit_compatibility():
    """Legacy refresh clients can upgrade without loading any retired model."""
    return None
