"""Bounded SageMaker training; versioned JSON artifacts served by existing Lambda."""
import hashlib
import io
import json
import math
import re
import tarfile
from datetime import date, datetime, timedelta, timezone
from functools import lru_cache
from pathlib import Path
from uuid import uuid4

import boto3
from boto3.dynamodb.conditions import Attr
from botocore.config import Config
from fastapi import HTTPException
from app.core.config import get_settings
from app.repositories import dynamodb_store as store
from app.services import demand_model

QUOTA_CODE='L-611FA074'
INSTANCE_TYPE='ml.m5.large'
MAX_RUNTIME=300
CSV_SHA='d827efa5f53a1ca3ea4c337012380949229f98b1314f39ff77d4b6598182f8c3'
SOURCE_DIR=Path(__file__).resolve().parent
# Retired managed-training route: no synthetic dataset is packaged in the app.
# Historical unit tests explicitly inject their isolated fixture.
DATA_FILE=None
JOB_PREFIX='abos-trial-demand-'


@lru_cache(maxsize=1)
def clients():
    config=Config(retries={'mode':'standard','total_max_attempts':2},connect_timeout=3,read_timeout=12,user_agent_appid='AWSSkill-SageMaker')
    session=boto3.Session(region_name=get_settings().aws_region)
    return (session.client('sagemaker',config=config),session.client('s3',config=config),session.client('service-quotas',config=config))


def configured():
    s=get_settings()
    return bool(s.business_data_table_name and s.sagemaker_training_role_arn and s.sagemaker_training_image and s.invoice_bucket_name)


def _read(key):
    item=store.table().get_item(Key={'pk':key,'sk':'DATA'},ConsistentRead=True).get('Item')
    return json.loads(item['payload']) if item else None


def _save(key,kind,data):
    store.table().put_item(Item=store._item(key,'DATA',kind,data))
    return data


def source_files():
    return {'sagemaker_entry.py':(SOURCE_DIR/'sagemaker_entry.py').read_bytes(),
            'demand_model.py':(SOURCE_DIR/'demand_model.py').read_bytes()}


def source_hash():
    return hashlib.sha256(b''.join(name.encode()+b'\0'+body for name,body in sorted(source_files().items()))).hexdigest()


def training_status():
    if not configured():return dict(configured=False,ready=False,reason='Managed training is available in the deployed AWS workspace, not this local database.')
    _,_,quota=clients()
    value=quota.get_service_quota(ServiceCode='sagemaker',QuotaCode=QUOTA_CODE)['Quota']['Value']
    guard=_read('TRAINING_GUARD')
    remaining=max(0,int(guard.get('next_allowed_epoch',0)-datetime.now(timezone.utc).timestamp())) if guard else 0
    active=_read('ACTIVE_DEMAND_MODEL')
    return dict(configured=True,ready=value>=1 and remaining==0,quota=value,quota_code=QUOTA_CODE,
        reason='AWS training-instance quota approval is required.' if value<1 else 'One-job-per-24-hours safeguard is active.' if remaining else 'Ready for one bounded training job.',
        instance_type=INSTANCE_TYPE,instance_count=1,max_runtime_seconds=MAX_RUNTIME,cooldown_seconds=remaining,
        latest_job=_read('LATEST_MANAGED_TRAINING'),active_model=active,
        data_source='reviewed_demo_invoices',demo=True,source_sha256=CSV_SHA,
        cost_note='Training is billable. No persistent endpoint. Storage, logs and provisioning can add charges; the USD 20 budget is an alert, not a hard cap.')


def build_request(job,bucket,role,image,code_hash):
    if not re.fullmatch(r'abos-trial-demand-[a-z0-9-]+',job):raise ValueError('Invalid training job name')
    def channel(name,prefix):
        return dict(ChannelName=name,DataSource={'S3DataSource':{'S3DataType':'S3Prefix','S3Uri':f's3://{bucket}/{prefix}/','S3DataDistributionType':'FullyReplicated'}},InputMode='File')
    return dict(TrainingJobName=job,RoleArn=role,
        AlgorithmSpecification={'TrainingImage':image,'TrainingInputMode':'File',
            'ContainerEntrypoint':['python3','/opt/ml/input/data/code/sagemaker_entry.py'],
            'MetricDefinitions':[{'Name':'evaluation_mae','Regex':r'evaluation_mae: ([0-9.eE+-]+)'},
                                 {'Name':'evaluation_wape','Regex':r'evaluation_wape: ([0-9.eE+-]+)'}]},
        InputDataConfig=[channel('train',f'processed/sales/managed/{job}'),channel('code',f'models/source/{code_hash}')],
        OutputDataConfig={'S3OutputPath':f's3://{bucket}/models/training-output'},
        ResourceConfig={'InstanceType':INSTANCE_TYPE,'InstanceCount':1,'VolumeSizeInGB':5},
        StoppingCondition={'MaxRuntimeInSeconds':MAX_RUNTIME},EnableNetworkIsolation=True,
        ProfilerConfig={'DisableProfiler':True},
        Tags=[{'Key':'Project','Value':'autonomous-business-os'},{'Key':'DataSource','Value':'DEMO'}])


def start_training(confirm_billable):
    if not confirm_billable:raise HTTPException(422,'Confirm the bounded, billable training job before starting.')
    status=training_status()
    if not status.get('configured'):raise HTTPException(503,status['reason'])
    if not status['ready']:raise HTTPException(409,status['reason'])
    if DATA_FILE is None:
        raise HTTPException(410, 'Managed demo training has been retired; use real-data Lambda analysis.')
    content=DATA_FILE.read_bytes()
    if hashlib.sha256(content).hexdigest()!=CSV_SHA:raise HTTPException(500,'Packaged demo dataset checksum mismatch')
    sm,s3,_=clients();settings=get_settings();now=datetime.now(timezone.utc)
    job=JOB_PREFIX+now.strftime('%Y%m%d-%H%M%S')+'-'+uuid4().hex[:6];code_hash=source_hash()
    record=dict(job_name=job,status='SUBMITTING',created_at=now.isoformat(),source_sha256=CSV_SHA,
        next_allowed_epoch=int(now.timestamp())+86400,
        code_sha256=code_hash,data_source='reviewed_demo_invoices',demo=True)
    # A conditional rolling lock prevents concurrent/repeated paid submissions, including uncertain timeouts.
    guard=store._item('TRAINING_GUARD','DATA','TRAINING_GUARD',record,
        next_allowed_epoch=int(now.timestamp())+86400,job_name=job)
    try:
        store.table().put_item(Item=guard,ConditionExpression=Attr('pk').not_exists()|Attr('next_allowed_epoch').lte(int(now.timestamp())))
    except store.table().meta.client.exceptions.ConditionalCheckFailedException:
        raise HTTPException(409,'Another request already reserved the daily training slot')
    _save('TRAINING#'+job,'MANAGED_TRAINING',record);_save('LATEST_MANAGED_TRAINING','MANAGED_TRAINING_POINTER',record)
    manifest=dict(training_job_name=job,demo=True,data_source='reviewed_demo_invoices',coverage_complete=True,
        source_sha256=CSV_SHA,code_sha256=code_hash)
    for name,body in source_files().items():
        s3.put_object(Bucket=settings.invoice_bucket_name,Key=f'models/source/{code_hash}/{name}',Body=body,ServerSideEncryption='AES256')
    s3.put_object(Bucket=settings.invoice_bucket_name,Key=f'processed/sales/managed/{job}/sales_daily.csv',Body=content,ServerSideEncryption='AES256')
    s3.put_object(Bucket=settings.invoice_bucket_name,Key=f'processed/sales/managed/{job}/manifest.json',Body=json.dumps(manifest).encode(),ServerSideEncryption='AES256')
    sm.create_training_job(**build_request(job,settings.invoice_bucket_name,settings.sagemaker_training_role_arn,settings.sagemaker_training_image,code_hash))
    record['status']='InProgress'
    _save('TRAINING#'+job,'MANAGED_TRAINING',record);_save('LATEST_MANAGED_TRAINING','MANAGED_TRAINING_POINTER',record)
    return record


def job_status(job):
    record=_read('TRAINING#'+job)
    if not record or not job.startswith(JOB_PREFIX):raise HTTPException(404,'Training job not found in this project')
    sm,_,_=clients()
    description=sm.describe_training_job(TrainingJobName=job)
    record.update(status=description['TrainingJobStatus'],secondary_status=description.get('SecondaryStatus'),
        failure_reason=description.get('FailureReason'),training_seconds=description.get('TrainingTimeInSeconds'),
        billable_seconds=description.get('BillableTimeInSeconds'),
        artifact_uri=description.get('ModelArtifacts',{}).get('S3ModelArtifacts'))
    _save('TRAINING#'+job,'MANAGED_TRAINING',record)
    latest=_read('LATEST_MANAGED_TRAINING')
    if latest and latest['job_name']==job:_save('LATEST_MANAGED_TRAINING','MANAGED_TRAINING_POINTER',record)
    return record


def validate_artifact(artifact,record):
    if artifact.get('schema_version')!=1 or artifact.get('training_backend')!='amazon_sagemaker':raise ValueError('Unsupported model artifact')
    for key in ['training_job_name','source_sha256','code_sha256']:
        expected=record['job_name'] if key=='training_job_name' else record[key]
        if artifact.get(key)!=expected:raise ValueError('Artifact provenance mismatch: '+key)
    if artifact.get('demo') is not True or artifact.get('data_source')!='reviewed_demo_invoices':raise ValueError('Unexpected data cohort')
    if artifact.get('quality_gate')!='PASSED_DEMO_CHECK':raise ValueError('Model did not pass the demo evaluation gate')
    if set(artifact.get('models',{}))!={'DEMO-MONITOR','DEMO-CAMERA','DEMO-HDD'}:raise ValueError('Unexpected product set')
    evaluation=artifact['metrics']
    if evaluation.get('holdout_observations',0)<30 or not isinstance(evaluation.get('wape'),(int,float)) or not math.isfinite(evaluation['wape']) or not 0<=evaluation['wape']<=.5:
        raise ValueError('Invalid evaluation metrics')
    predicted=forecast_artifact(artifact,90)
    if predicted!=artifact.get('forecasts'):raise ValueError('Saved model does not reproduce training output')
    return artifact


def forecast_artifact(artifact,horizon):
    if not 1<=horizon<=365:raise ValueError('Horizon must be between 1 and 365 days')
    result=[]
    for product,model in sorted(artifact['models'].items()):
        last=date.fromisoformat(model['last_date'])
        for step,q in enumerate(demand_model.predict(model,horizon),1):
            result.append(dict(product_id=product,forecast_date=(last+timedelta(days=step)).isoformat(),predicted_quantity=q))
    return result


def load_completed_artifact(record):
    settings=get_settings();prefix=f's3://{settings.invoice_bucket_name}/models/training-output/{record["job_name"]}/output/'
    uri=record.get('artifact_uri') or ''
    if record['status']!='Completed' or not uri.startswith(prefix) or not uri.endswith('/model.tar.gz'):
        raise ValueError('A completed job with an expected private model artifact is required')
    _,s3,_=clients();key=uri.removeprefix(f's3://{settings.invoice_bucket_name}/')
    response=s3.get_object(Bucket=settings.invoice_bucket_name,Key=key)
    with response['Body'] as body:raw=body.read(5*1024*1024+1)
    if len(raw)>5*1024*1024:raise ValueError('Model archive is too large')
    with tarfile.open(fileobj=io.BytesIO(raw),mode='r:gz') as archive:
        member=archive.getmember('model.json')
        if not member.isfile() or member.size>2*1024*1024:raise ValueError('Invalid JSON model member')
        with archive.extractfile(member) as handle:artifact=json.load(handle)
    return validate_artifact(artifact,record)


def activate(job):
    record=job_status(job);artifact=load_completed_artifact(record)
    active=dict(job_name=job,activated_at=datetime.now(timezone.utc).isoformat(),artifact_uri=record['artifact_uri'],
        source_sha256=record['source_sha256'],code_sha256=record['code_sha256'],demo=True,
        quality_gate=artifact['quality_gate'],metrics=artifact['metrics'])
    table=store.table()
    table.meta.client.transact_write_items(TransactItems=[
        {'Put':{'TableName':table.name,'Item':store._item('ACTIVE_DEMAND_ARTIFACT','DATA','MANAGED_MODEL',artifact)}},
        {'Put':{'TableName':table.name,'Item':store._item('ACTIVE_DEMAND_MODEL','DATA','MANAGED_MODEL_POINTER',active)}},
    ])
    return active


def predict_active(horizon):
    artifact=_read('ACTIVE_DEMAND_ARTIFACT')
    active=_read('ACTIVE_DEMAND_MODEL')
    if not artifact or not active:raise HTTPException(409,'No evaluated SageMaker model has been activated yet')
    validate_artifact(artifact,active)
    rows=forecast_artifact(artifact,horizon)
    evaluation={**artifact['metrics'],'training_backend':'amazon_sagemaker','inference_backend':'aws_lambda',
        'training_job_name':active['job_name'],'artifact_uri':active['artifact_uri'],
        'demo':True,'data_source':'reviewed_demo_invoices','products':artifact['product_metrics'],
        'trained_models':artifact['models'],'source_sha256':artifact['source_sha256'],'horizon_days':horizon,
        'forecast_warnings':artifact['limitations']}
    return store.create_forecast(dict(model_name='sagemaker_trained_lambda_inference',window_days=28,horizon_days=horizon,metrics=evaluation,forecasts=rows))
