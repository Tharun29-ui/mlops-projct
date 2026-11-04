"""Persist confirmed bill profits separately from invoices and inventory."""
import json
from copy import deepcopy
from datetime import datetime, timezone
from uuid import uuid4
from sqlalchemy import update
from sqlalchemy.exc import IntegrityError
from app.core.config import get_settings
from app.models import AppSetting
from app.repositories import dynamodb_store as store

KEY = 'SETTINGS#MONTHLY_BILL_PROFIT_REAL'


def load(db):
    if get_settings().business_data_table_name:
        item = store.table().get_item(Key={'pk': KEY, 'sk': 'DATA'}, ConsistentRead=True).get('Item')
        return json.loads(item['payload']) if item else {'revision': None, 'inputs': {}, 'history': []}
    row = db.get(AppSetting, KEY)
    return deepcopy(row.value) if row else {'revision': None, 'inputs': {}, 'history': []}


def save(db, records, expected_revision):
    cloud = bool(get_settings().business_data_table_name)
    original_item = store.table().get_item(Key={'pk': KEY, 'sk': 'DATA'}, ConsistentRead=True).get('Item') if cloud else None
    old = json.loads(original_item['payload']) if original_item else load(db) if not cloud else {'revision': None, 'inputs': {}, 'history': []}
    if old.get('revision') != expected_revision:
        raise ValueError('Bill profits changed in another window. Refresh before saving.')
    now = datetime.now(timezone.utc).isoformat()
    inputs = deepcopy(old.get('inputs', {}))
    history = deepcopy(old.get('history', []))
    for record in records:
        history.append({'bill_id': record['bill_id'], 'previous': inputs.get(record['bill_id']),
                        'new': record, 'recorded_at': now})
        inputs[record['bill_id']] = {**record, 'recorded_at': now}
    new = {'revision': uuid4().hex, 'inputs': inputs, 'history': history[-100:], 'updated_at': now}
    encoded = json.dumps(new, separators=(',', ':'), ensure_ascii=False)
    if len(encoded.encode()) > 300000:
        raise ValueError('The profit-confirmation record is full; export it before adding more history.')
    if cloud:
        kwargs = {'ConditionExpression': '#payload = :previous',
                  'ExpressionAttributeNames': {'#payload': 'payload'},
                  'ExpressionAttributeValues': {':previous': original_item['payload']}} if original_item else {'ConditionExpression': 'attribute_not_exists(pk)'}
        try:
            store.table().put_item(Item=store._item(KEY, 'DATA', 'APP_SETTING', new), **kwargs)
        except store.table().meta.client.exceptions.ConditionalCheckFailedException as exc:
            raise ValueError('Bill profits changed in another window. Refresh before saving.') from exc
    else:
        try:
            if old.get('revision') is None:
                db.add(AppSetting(key=KEY, value=new))
            else:
                changed = db.execute(update(AppSetting).where(AppSetting.key == KEY, AppSetting.value == old).values(value=new))
                if changed.rowcount != 1:
                    db.rollback()
                    raise ValueError('Bill profits changed in another window. Refresh before saving.')
            db.commit()
        except IntegrityError as exc:
            db.rollback()
            raise ValueError('Bill profits changed in another window. Refresh before saving.') from exc
    return new
