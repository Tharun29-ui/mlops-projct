"""Commercial intelligence from approved records or explicitly unverified dataset evaluation."""
import json
from collections import defaultdict
from datetime import date
from pathlib import Path
from fastapi import APIRouter,Depends,HTTPException
from sqlalchemy import select
from sqlalchemy.orm import Session
from app.db import get_db
from app.core.config import get_settings
from app.models import Invoice,Product,ForecastRun
from app.repositories import dynamodb_store
from app.services.commercial_models import build_insights, effective_line_price
from app.services.analysis_sources import approved_analysis_invoices
from app.api.routes.invoices import _invoice_dict
from app.api.routes.inventory import _product_dict,_get_pricing_policy
from app.services.monthly_models import inventory_plan
from pydantic import BaseModel,Field
from typing import Annotated
from fastapi import Query

router=APIRouter(prefix='/intelligence',tags=['intelligence'])


class MonthlyScenarioInput(BaseModel):
    month: str | None = Field(default=None,pattern=r'^\d{4}-(0[1-9]|1[0-2])$')
    quantities: dict[str,float] = Field(default_factory=dict,max_length=500)


def _monthly_plan(db,month=None,quantities=None):
    path=Path(__file__).resolve().parents[2]/'data/monthly-model-bundle.json'
    if not path.exists():raise HTTPException(404,'Monthly real-invoice models are not trained yet')
    products=dynamodb_store.list_products() if get_settings().business_data_table_name else [_product_dict(p) for p in db.scalars(select(Product)).all()]
    if len(products)>500:raise HTTPException(422,'Monthly planning supports up to 500 inventory products')
    try:return inventory_plan(json.loads(path.read_text()),products,_get_pricing_policy(db),month=month,assumptions=quantities)
    except (ValueError,OverflowError) as exc:raise HTTPException(422,str(exc)) from exc


@router.get('/monthly-plan')
def monthly_plan(db:Session=Depends(get_db),month:Annotated[str|None,Query(pattern=r'^\d{4}-(0[1-9]|1[0-2])$')]=None):
    return _monthly_plan(db,month)


@router.post('/monthly-plan/scenario')
def monthly_scenario(payload:MonthlyScenarioInput,db:Session=Depends(get_db)):
    return _monthly_plan(db,payload.month,payload.quantities)


@router.get('/monthly-plan/artifact')
def monthly_artifact():
    path=Path(__file__).resolve().parents[2]/'data/monthly-model-bundle.json'
    if not path.exists():raise HTTPException(404,'Monthly models are not trained yet')
    return json.loads(path.read_text())


@router.get('/reviewed-sample')
def reviewed_real_sample():
    from app.services.reviewed_sample import run_reviewed_sample
    return run_reviewed_sample()


@router.post('/reviewed-sample/train')
def train_reviewed_real_sample():
    from app.services.reviewed_sample import run_reviewed_sample
    return run_reviewed_sample()


@router.get('/dataset')
def dataset_evaluation():
    path=Path(__file__).resolve().parents[2]/'data/commercial-dataset-report.json'
    if not path.exists():raise HTTPException(404,'The supplied dataset evaluation has not been generated yet')
    return json.loads(path.read_text())


@router.get('')
def commercial_intelligence(db:Session=Depends(get_db)):
    cloud=bool(get_settings().business_data_table_name)
    invoices=dynamodb_store.list_invoices() if cloud else [_invoice_dict(i) for i in db.scalars(select(Invoice)).all()]
    products=dynamodb_store.list_products() if cloud else [_product_dict(p) for p in db.scalars(select(Product)).all()]
    invoices, demo_only, excluded_demo_count=approved_analysis_invoices(invoices)
    rows=[]
    for invoice in invoices:
        if invoice.get('status')!='APPROVED':continue
        for line in invoice.get('lines',[]):
            if line.get('is_stock_item') is False:continue
            price = effective_line_price(line)
            if not line.get('product_id') or price is None or not invoice.get('invoice_date'):continue
            unit=(line.get('unit') or 'UNRECORDED').strip().casefold()
            if unit in {'nos','no','pcs','pieces','pc','unit','units'}:unit='each'
            rows.append({'product_id':str(line['product_id']),'date':str(invoice['invoice_date'])[:10],
                         'quantity':float(line['quantity']),'price':price,
                         'unit':unit,'currency':invoice.get('currency','INR'),
                         'party':invoice.get('vendor_name') if invoice.get('transaction_type')=='PURCHASE' else invoice.get('receiver_name') or '',
                         'direction':invoice.get('transaction_type')})
    demand=defaultdict(float)
    forecast=dynamodb_store.latest_forecast() if cloud else None
    if not cloud:
        local=db.scalars(select(ForecastRun).order_by(ForecastRun.created_at.desc())).first()
        if local:forecast={'metrics':local.metrics,'forecasts':local.forecasts}
    forecast_demo=bool((forecast or {}).get('metrics',{}).get('demo'))
    reviewed_demo=(forecast or {}).get('metrics',{}).get('data_source')=='reviewed_demo_invoices'
    if forecast and ((demo_only and reviewed_demo) or (not demo_only and not forecast_demo)):
        for row in forecast.get('forecasts',[]):
            if str(row['forecast_date'])>=date.today().isoformat():
                demand[str(row['product_id'])]+=float(row['predicted_quantity'])
    policy=_get_pricing_policy(db)
    minimum=float(policy.get('minimum_margin_percent') or 0)
    target=float(policy.get('target_margin_percent') if policy.get('target_margin_percent') is not None else minimum)
    result=build_insights(rows,{str(p['id']):p for p in products},dict(demand),minimum,max(minimum,target))
    approved=[i for i in invoices if i.get('status')=='APPROVED']
    result['data_source']='reviewed_demo_invoices' if demo_only else 'approved_business_records'
    result['excluded_demo_invoices']=excluded_demo_count
    result['review_status']='APPROVED_RECORDS_ONLY'
    return result
