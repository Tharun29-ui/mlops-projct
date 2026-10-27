import copy
import io
import json
import tempfile
import unittest
from datetime import datetime,timezone
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch
import tarfile

from fastapi import HTTPException
from app.services import managed_training as service
from app.services.sagemaker_entry import train_bundle


class ConditionalFailure(Exception):pass


class Table:
    name='test-business-table'
    def __init__(self):
        self.items={};self.transactions=0
        self.meta=SimpleNamespace(client=SimpleNamespace(
            exceptions=SimpleNamespace(ConditionalCheckFailedException=ConditionalFailure),
            transact_write_items=self.transact))
    def get_item(self,Key,**kwargs):return {'Item':self.items[(Key['pk'],Key['sk'])]} if (Key['pk'],Key['sk']) in self.items else {}
    def put_item(self,Item,ConditionExpression=None):
        key=(Item['pk'],Item['sk'])
        if ConditionExpression is not None and key in self.items and self.items[key].get('next_allowed_epoch',0)>datetime.now(timezone.utc).timestamp():raise ConditionalFailure()
        self.items[key]=copy.deepcopy(Item)
    def transact(self,TransactItems):
        self.transactions+=1
        for item in TransactItems:self.put_item(Item=item['Put']['Item'])


class Storage:
    def __init__(self):self.items={}
    def put_object(self,Bucket,Key,Body,**kwargs):self.items[(Bucket,Key)]=Body
    def get_object(self,Bucket,Key):return {'Body':io.BytesIO(self.items[(Bucket,Key)])}


class SageMaker:
    def __init__(self):self.jobs={};self.requests=[]
    def create_training_job(self,**request):
        self.requests.append(request);self.jobs[request['TrainingJobName']]={'TrainingJobStatus':'InProgress'}
        return {'TrainingJobArn':'arn:aws:sagemaker:ap-southeast-2:000000000000:training-job/'+request['TrainingJobName']}
    def describe_training_job(self,TrainingJobName):return self.jobs[TrainingJobName]


class ManagedTrainingTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.fixture_patch=patch.object(service,'DATA_FILE',Path(__file__).parent/'fixtures/retired-sagemaker-demo-sales.csv')
        cls.fixture_patch.start()
        cls.addClassCleanup(cls.fixture_patch.stop)
        cls.temp=tempfile.TemporaryDirectory();base=Path(cls.temp.name)
        cls.input=base/'input';cls.input.mkdir();cls.output=base/'output'
        (cls.input/'sales_daily.csv').write_bytes(service.DATA_FILE.read_bytes())
        cls.job='abos-trial-demand-test-version'
        cls.manifest=dict(demo=True,data_source='reviewed_demo_invoices',coverage_complete=True,
            source_sha256=service.CSV_SHA,code_sha256=service.source_hash(),training_job_name=cls.job)
        (cls.input/'manifest.json').write_text(json.dumps(cls.manifest))
        cls.artifact=train_bundle(cls.input,cls.output)
    @classmethod
    def tearDownClass(cls):cls.temp.cleanup()
    def setUp(self):
        self.table=Table();self.s3=Storage();self.sm=SageMaker();self.quota=1
        self.settings=SimpleNamespace(aws_region='ap-southeast-2',business_data_table_name='test-business-table',
            invoice_bucket_name='test-private-bucket',sagemaker_training_role_arn='arn:aws:iam::000000000000:role/abos-trial-sagemaker-training',
            sagemaker_training_image='registry/image@sha256:pinned')
        quotas=SimpleNamespace(get_service_quota=lambda **kwargs:{'Quota':{'Value':self.quota}})
        self.patches=[patch.object(service,'get_settings',return_value=self.settings),
            patch.object(service,'clients',return_value=(self.sm,self.s3,quotas)),
            patch.object(service.store,'table',return_value=self.table)]
        for p in self.patches:p.start();self.addCleanup(p.stop)
    def test_dataset_and_training_artifact_contract(self):
        self.assertEqual(len(self.artifact['forecasts']),270)
        self.assertEqual(self.artifact['quality_gate'],'PASSED_DEMO_CHECK')
        self.assertEqual(self.artifact['metrics']['holdout_observations'],36)
        self.assertAlmostEqual(self.artifact['metrics']['wape'],.2222)
        self.assertTrue(self.artifact['demo'])
    def test_quota_zero_prevents_paid_calls_and_storage_writes(self):
        self.quota=0
        self.assertFalse(service.training_status()['ready'])
        with self.assertRaises(HTTPException):service.start_training(True)
        self.assertFalse(self.sm.requests);self.assertFalse(self.s3.items);self.assertFalse(self.table.items)
    def test_consent_is_required_even_with_quota(self):
        with self.assertRaises(HTTPException):service.start_training(False)
        self.assertFalse(self.sm.requests)
    def test_bounded_submission_and_rolling_lock(self):
        record=service.start_training(True)
        self.assertEqual(record['status'],'InProgress')
        request=self.sm.requests[0]
        self.assertEqual(request['ResourceConfig']['InstanceCount'],1)
        self.assertEqual(request['ResourceConfig']['InstanceType'],'ml.m5.large')
        self.assertEqual(request['StoppingCondition']['MaxRuntimeInSeconds'],300)
        self.assertTrue(request['EnableNetworkIsolation'])
        self.assertGreater(service.training_status()['cooldown_seconds'],86000)
        with self.assertRaises(HTTPException):service.start_training(True)
        self.assertEqual(len(self.sm.requests),1)
    def test_invalid_or_untracked_job_is_not_described(self):
        with self.assertRaises(HTTPException):service.job_status('another-project-job')
    def test_artifact_cannot_cross_jobs_or_data_cohorts(self):
        record=dict(job_name=self.job,source_sha256=service.CSV_SHA,code_sha256=service.source_hash())
        changed=copy.deepcopy(self.artifact);changed['demo']=False
        with self.assertRaises(ValueError):service.validate_artifact(changed,record)
        changed=copy.deepcopy(self.artifact);changed['training_job_name']='other-job'
        with self.assertRaises(ValueError):service.validate_artifact(changed,record)
    def test_failed_evaluation_is_not_activated(self):
        changed=copy.deepcopy(self.artifact);changed['metrics']['wape']=.9
        with self.assertRaises(ValueError):service.validate_artifact(changed,dict(job_name=self.job,source_sha256=service.CSV_SHA,code_sha256=service.source_hash()))
    def test_tampered_forecasts_are_not_activated(self):
        changed=copy.deepcopy(self.artifact);changed['forecasts'][0]['predicted_quantity']+=1
        with self.assertRaises(ValueError):service.validate_artifact(changed,dict(job_name=self.job,source_sha256=service.CSV_SHA,code_sha256=service.source_hash()))
    def test_completion_activation_and_lambda_prediction_without_retraining(self):
        record=dict(job_name=self.job,status='InProgress',source_sha256=service.CSV_SHA,code_sha256=service.source_hash(),demo=True)
        service._save('TRAINING#'+self.job,'MANAGED_TRAINING',record)
        artifact_uri=f's3://test-private-bucket/models/training-output/{self.job}/output/model.tar.gz'
        self.sm.jobs[self.job]={'TrainingJobStatus':'Completed','TrainingTimeInSeconds':2,'BillableTimeInSeconds':2,'ModelArtifacts':{'S3ModelArtifacts':artifact_uri}}
        stream=io.BytesIO();body=json.dumps(self.artifact).encode()
        with tarfile.open(fileobj=stream,mode='w:gz') as archive:
            info=tarfile.TarInfo('model.json');info.size=len(body);archive.addfile(info,io.BytesIO(body))
        self.s3.items[('test-private-bucket',f'models/training-output/{self.job}/output/model.tar.gz')]=stream.getvalue()
        active=service.activate(self.job)
        self.assertEqual(active['job_name'],self.job);self.assertEqual(self.table.transactions,1)
        with patch.object(service.demand_model,'train',side_effect=AssertionError('Inference must not train')):
            with patch.object(service.store,'create_forecast',side_effect=lambda record:record):result=service.predict_active(90)
        self.assertEqual(result['forecasts'],self.artifact['forecasts'])
        self.assertEqual(result['metrics']['inference_backend'],'aws_lambda')
        self.assertEqual(result['metrics']['training_backend'],'amazon_sagemaker')
    def test_incomplete_or_modified_input_is_rejected(self):
        with tempfile.TemporaryDirectory() as folder:
            p=Path(folder);(p/'sales_daily.csv').write_bytes(b'not the original data')
            (p/'manifest.json').write_text(json.dumps(self.manifest))
            with self.assertRaisesRegex(ValueError,'checksum mismatch'):train_bundle(p,p/'out')


if __name__=='__main__':unittest.main()
